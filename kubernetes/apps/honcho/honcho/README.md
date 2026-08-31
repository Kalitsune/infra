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

Two consequences that are easy to get wrong:

- `OPENCONCHO_DEFAULT_HONCHO_URL` must be the **in-cluster** address. Set it to
  the public hostname and nginx would proxy back through oauth2-proxy, holding
  no OIDC cookie, and get a login redirect instead of an API response.
- `OPENCONCHO_UPSTREAM_ALLOWLIST` **must** be set. Upstream leaves it open
  because it assumes a localhost-only bind; exposed as we do, an unpinned proxy
  would forward to any host a request names — an SSRF relay onto the cluster
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
`localStorage` — nothing is baked into this deployment. Do not paste the admin
JWT from `honcho-client-secrets`: that is Hermes' credential and it can
administer the server.

Mint a workspace-scoped token instead. It covers every view except the
multi-workspace "fleet" page (`/v3/workspaces/list` is admin-only), which is
irrelevant with a single workspace — navigate straight to `/workspaces/hermes`.

```bash
# from a checkout of github.com/plastic-labs/honcho, with AUTH_JWT_SECRET set
uv run python scripts/generate_jwt.py --workspace hermes --expires 90d
```

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
