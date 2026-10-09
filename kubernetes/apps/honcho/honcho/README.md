# Honcho

[Honcho](https://github.com/plastic-labs/honcho) — the memory backend for
Hermes Agent. It stores conversation messages, reasons over them in the
background, and serves back peer representations and conclusions so Hermes
remembers a user across sessions.

Self-hosted rather than using `api.honcho.dev`: every conversation this
cluster's agents have would otherwise be sent to a third party.

## Components

| Workload          | What it does                                                    |
| ----------------- | --------------------------------------------------------------- |
| `honcho-api`      | FastAPI server on `:8000`; also runs migrations at startup        |
| `honcho-deriver`  | Background worker — builds representations, summaries, dreams     |
| `honcho-postgres` | Postgres 17 + pgvector; the only durable state                    |
| `honcho-redis`    | Cache and lock coordination; disposable, no PVC                   |
| `openconcho`      | Third-party web UI for browsing memory                            |
| `oauth2-proxy`    | OIDC gate in front of the browser route only                      |

The deriver is not optional. Without it messages are stored but no memory is
ever derived, which looks like "Honcho is up but knows nothing".

## The web UI

Honcho upstream ships **no dashboard** — the API is the product, and the only
bundled browser surface is FastAPI's `/docs`. The dashboard in Plastic Labs'
marketing is their hosted SaaS, not part of the AGPL server.

[`offendingcommit/openconcho`](https://github.com/offendingcommit/openconcho)
(MIT) fills that gap: browse peers, sessions, conclusions and chat with memory
context. It is a static React SPA served by nginx, which proxies `/api/`
**server-side** to whatever host the browser names in an `X-Honcho-Upstream`
header — so the Honcho API is never exposed to the browser and there is no CORS.

### Why it is a sidecar, not its own Deployment

The UI runs as a second container **inside the `honcho-api` pod**, sharing its
network namespace so the backend URL can be `http://127.0.0.1:8000`.

That is not cosmetic. The settings form refuses to store an API token unless the
base URL is HTTPS *or* a loopback host:

```ts
// packages/web/src/lib/security.ts
export function isSecureTokenTransport(baseUrl: string): boolean {
  if (parsed.protocol === "https:") return true;
  if (parsed.protocol === "http:" && LOOPBACK_HOSTS.has(hostname)) return true;
  return false;   // → "API tokens require HTTPS unless connecting to localhost."
}
```

A Service DNS name over plain HTTP (`http://honcho.honcho.svc.cluster.local:8000`)
fails that check, so the token cannot be saved and every request 401s. The
alternatives were publishing the API over TLS on its own hostname — which drops
the OIDC gate in front of it — or colocating. Colocating keeps the API
unpublished.

The check runs in the **browser**, against the URL string; the request is made
by **nginx inside the pod**, where `127.0.0.1:8000` genuinely is the `api`
container. Those two facts agree only because the containers are colocated.

Consequences worth knowing:

- The `openconcho` Service selects the `honcho-api` pod and targets its `ui`
  port. Both Services front the same pod.
- **No pod-level `runAsUser`.** The images run as different uids (honcho 100,
  nginx-unprivileged 101); forcing one onto the other breaks file ownership.
  Security context is per-container, with `fsGroup: 101` for nginx's writable
  mounts.
- A UI restart restarts the API too, since they share a pod lifecycle.
- `OPENCONCHO_UPSTREAM_ALLOWLIST` is `127.0.0.1` — the proxy can now only reach
  its own pod. Unset, it forwards anywhere a request names (upstream defaults it
  open, assuming a localhost-only bind), which is an SSRF relay onto the cluster
  network.

### Paths on `honcho.lab.kalitsune.net`

oauth2-proxy has a single upstream — the UI. Honcho's own HTTP surface
(`/docs`, `/v3/…`) is deliberately **not** mapped onto this hostname.

The SPA issues every Honcho call same-origin to `/api/`, naming the backend in
an `X-Honcho-Upstream` header that nginx forwards server-side. So the API is
already reachable through the UI pod, constrained by that pod's allowlist.
Publishing `/v3/` alongside it would be a second, unconstrained route to the
same API for no gain.

For raw API work — including Swagger — use a port-forward:

```bash
kubectl -n honcho port-forward svc/honcho 8000:8000
# http://localhost:8000/docs
```

### The UI's token

The UI asks for the Honcho URL and token **in the browser** and keeps them in
`localStorage` under `openconcho:instances` — nothing is baked into this
deployment, and there is no env var to preseed a token. Do not paste the admin
JWT from `honcho-client-secrets`: that is Hermes' credential and it can
administer the server.

In **Settings**, enter:

| Field | Value |
| ----- | ----- |
| Base URL | `http://127.0.0.1:8000` |
| Token | a workspace-scoped JWT (below) |

The loopback URL is what satisfies the transport guard; it resolves to the `api`
container in the same pod.

Mint a workspace-scoped token instead. It covers every view except the
multi-workspace "fleet" page (`/v3/workspaces/list` is admin-only), which is
irrelevant with a single workspace — navigate straight to `/workspaces/hermes`.

```bash
umask 077
kubectl -n honcho exec deploy/honcho-api -- \
  python scripts/generate_jwt.py --workspace hermes --print-only \
  > /opt/data/export/honcho-ui-token.txt
chmod 600 /opt/data/export/honcho-ui-token.txt
```

**Do not pass `--expires`.** Upstream's `generate_jwt.py` formats the `exp`
claim as an ISO-8601 string, but the PyJWT version in the image rejects that at
decode time:

```
DecodeError: Expiration Time claim (exp) must be an integer.
```

The token mints and its signature verifies, yet every request 401s with
`{"detail":"Invalid JWT"}` — `src/security.py` catches `PyJWTError` and reports
it as invalid, so the real cause is hidden. Honcho's own `verify_jwt` *does*
handle a string `exp` (it parses with `fromisoformat`), but PyJWT rejects the
claim before that code is reached. A non-expiring token is the working option
until upstream emits a numeric `exp`; rotate it by rotating `AUTH_JWT_SECRET`,
which also invalidates Hermes' credential.

## Two access paths, deliberately different

```
Hermes pod ──(ClusterIP, scoped JWT)──────────────> honcho:8000
Browser ──(HTTPS)──> oauth2-proxy ──(OIDC via Pocket ID)──> honcho:8000
```

**Hermes must not go through oauth2-proxy.** It talks to Honcho with the
`honcho-ai` SDK — a non-interactive HTTP client. oauth2-proxy answers an
unauthenticated request with a 302 into an interactive browser sign-in that an
SDK cannot complete, so every call would fail. The memory plugin degrades
quietly when that happens (it reports "no context" rather than erroring), which
makes it a genuinely nasty failure to debug.

The API's own auth is `AUTH_USE_AUTH=true` plus `AUTH_JWT_SECRET`: scoped HS256
JWTs (admin / workspace / peer / session). That is the correct auth primitive
for a machine client, and it is what `HONCHO_API_KEY` in the hermes namespace
holds.

oauth2-proxy exists solely because the browser surface — `/docs`, and any
manual inspection of peers and conclusions — is human-driven and must not be
open. `/docs` issues live authenticated API calls, so leaving it unguarded
would be equivalent to publishing the API.

## Storage

Postgres uses `storageClassName: local-path`, **not** the repo default
`truenas-nfs`. Postgres relies on POSIX `fsync()` durability and byte-range
locking that NFS does not reliably provide; the failure mode is silent data
corruption, not an error. This is the same reasoning that keeps SQLite apps off
NFS. The trade-off is that the volume is pinned to one node and is not covered
by TrueNAS snapshots — back up with `pg_dump` instead.

## Secrets to fill in before first deploy

Both are SOPS-encrypted and currently hold placeholders:

- `secret.yaml` → `LLM_GEMINI_API_KEY`: set to the same value as
  `GOOGLE_API_KEY` in `hermes-secrets`. Honcho **fails to start** without a
  working LLM provider.
- `oauth2-secret.yaml` → `clientId` / `clientSecret`: register a new OIDC
  client in Pocket ID with callback
  `https://honcho.lab.kalitsune.net/oauth2/callback`. Clients are per-host, so
  another app's client will not work.

Edit with `sops kubernetes/apps/honcho/honcho/<file>.yaml`.

## Models

Everything runs on the Gemini transport against the existing Google key.
Text features use `gemini-3.6-flash` (`gemini-3.7-flash` for the `max`
dialectic level); embeddings use `gemini-embedding-2` at 1536 dimensions.

Two constraints worth knowing before changing these:

- **All five dialectic levels must be set.** They have no shared default, so
  leaving one on the stock `openai` transport makes the whole config invalid.
- **`EMBEDDING_VECTOR_DIMENSIONS` is not freely editable.** It has to match the
  physical pgvector column width, and a startup validator crashes the process
  when they disagree. Changing it after the first deploy means re-embedding
  everything, not just editing the ConfigMap.

`gemini-embedding-2` is chosen over `-001` because Honcho does not normalise
embedding vectors itself and `-001` requires manual normalisation at any
dimension other than 3072.

## Rotating the Hermes credential

`HONCHO_API_KEY` (in `apps/hermes/hermes/honcho-client-secret.yaml`) is an
admin JWT signed with `AUTH_JWT_SECRET`. The two are a pair — rotating the
secret invalidates the token, so both must be regenerated in the same commit.
The token is `{"t": "", "ad": true}` signed HS256; `scripts/generate_jwt.py`
in the upstream repo produces one.

## Verifying

```bash
kubectl -n honcho rollout status deploy/honcho-api
kubectl -n honcho logs deploy/honcho-deriver --tail=50

# API health (no auth required on /health)
kubectl -n honcho port-forward svc/honcho 8000:8000
curl -s localhost:8000/health

# authenticated call — proves the JWT and DB both work
curl -s -H "Authorization: Bearer <admin-jwt>" localhost:8000/v3/workspaces/list -X POST
```

A `/health` 200 only means the process is up. It does not check the database
or the LLM provider — an authenticated workspace call does.
