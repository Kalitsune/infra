# n8n

Workflow automation. `https://n8n.kalitsune.net`.

## First boot: claim the owner account

A fresh instance serves an **unauthenticated `/setup`** that mints the first
owner account, and this host is on the public apex. Until someone claims it,
anyone who reaches the URL can take the instance.

    https://n8n.kalitsune.net/setup

Do this first, before anything else. Afterwards `/setup` redirects to the
sign-in page and further accounts are created by the owner from Settings →
Members. n8n has no public self-registration.

## Why no oauth2-proxy / OIDC SecurityPolicy

Every other admin UI here sits behind Pocket ID. n8n deliberately does not, and
this is not an oversight to "fix" for consistency:

- Gateway-native OIDC gates **every** path, `/webhook/*` and `/form/*`
  included. Those are called by third-party services that cannot complete a
  browser redirect, so adding the gate breaks the one job the app exists for.
- n8n authenticates its own editor: owner/member accounts with bcrypt hashes,
  and MFA available per-user under Settings → Personal.
- SSO (SAML/OIDC) in n8n is an Enterprise-licensed feature, so a split
  `/rest` vs `/webhook` proxy would still leave two separate logins.

If SSO is wanted later, the shape is a second HTTPRoute rule: the editor and
`/rest` behind oauth2-proxy, `/webhook`, `/webhook-test`, `/form`, `/form-waiting`
and `/healthz` direct. That costs a proxy Deployment and a double login.

## Models: 9Router, not OpenAI directly

The OpenAI credential type is clamped to the in-cluster 9Router instance by
`CREDENTIALS_OVERWRITE_DATA`, supplied through the Secret
`9router-n8n-overwrite` (`envFrom`, `optional: true`):

    {"openAiApi": {"apiKey": "sk-n8n-…",
                   "url": "http://9router.9router.svc.cluster.local:20128/v1"}}

Credential overwrites are merged server-side over **every** `openAiApi`
credential and the overwritten fields are hidden in the editor, so an OpenAI
credential is created empty — name it, save it, it works. The model dropdown on
the OpenAI Chat Model node calls `<url>/models`, which is 9Router's catalogue:
the `high-effort`, `medium-effort` and `fast-af` combos (effort dials) plus the
individual upstream models. Pick one per node.

Three consequences, all deliberate:

- **This is a global clamp, not a default.** While the Secret is present, a
  second OpenAI credential pointing at `api.openai.com` is impossible — the
  overwrite wins. Routing all model traffic through 9Router is the intent.
- The Secret is **not** in git. It is minted by the provisioning Job in
  `apps/9router` against its own `n8n` machineId, so the key is revocable from
  the 9Router dashboard without touching the agents. To re-run it, bump the
  generation in `apps/9router/9router/provision-job.yaml`.
- The ClusterIP URL bypasses oauth2-proxy, which is correct: 9Router's `/v1`
  has its own API-key gate (`401 Missing API key` without one), and an OIDC
  browser redirect is meaningless to an API client.

`CREDENTIALS_OVERWRITE_PERSISTENCE` is left off. It only exists to propagate
overwrites to queue-mode workers; this is a single process, so enabling it would
copy the key into SQLite for no gain.

## Storage — do not move this to NFS

`storageClassName: local-path`, for two independent reasons:

- `DB_TYPE` defaults to `sqlite`, so all state is an embedded database at
  `/home/node/.n8n/database.sqlite`. SQLite's `fcntl()` locking is unreliable
  over NFS; the failure mode is silent corruption.
- The volume holds the **credentials encryption key**. n8n generates it with
  `randomBytes(24)` on first boot and writes it to `/home/node/.n8n/config` —
  it is not supplied by env and not in a Secret. Lose the volume and every
  stored credential becomes undecryptable, even from a database backup.

Consequences accepted: the volume is pinned to one node, and there are no
TrueNAS snapshots. Back up by copying `/home/node/.n8n` (database **and**
`config` together — either alone is useless).

Because the encryption key is self-generated and persisted, `replicas: 1` and
`strategy: Recreate` are both load-bearing: two pods would mean two SQLite
writers on one file.

## Upgrading

Bump tag and digest together in `deployment.yaml`:

    curl -sS https://hub.docker.com/v2/repositories/n8nio/n8n/tags/<version> | jq -r .digest

Check `packages/cli/BREAKING-CHANGES.md` upstream first. Migrations run on
start; `/healthz/readiness` stays 503 until they finish, which is why the
startup probe allows five minutes.

## Environment notes

- `N8N_PROXY_HOPS=1` — exactly one reverse proxy (Envoy). The push channel's
  origin validator reconstructs the expected origin from `X-Forwarded-*`; a
  wrong hop count surfaces as `Invalid origin!` on the editor's websocket while
  HTTP still works.
- `N8N_EDITOR_BASE_URL` / `N8N_WEBHOOK_URL` — without these n8n hands out
  `http://localhost:5678` as the OAuth2 redirect URI when connecting
  credentials to Google, GitHub, etc.
- Telemetry (`N8N_DIAGNOSTICS_ENABLED`) defaults to **on** upstream and is
  turned off here.
- Task runners default to `internal` (in-process), so no second Deployment.
