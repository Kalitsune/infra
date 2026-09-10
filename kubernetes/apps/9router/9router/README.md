# 9Router

Local AI-provider proxy/router with a dashboard and an OpenAI-compatible API.
Upstream: <https://github.com/decolua/9router>

Deployed on request from Fanny (via maple's Hermes assistant), relayed through
the `hermes` A2A dispatcher.

## Reaching it

Internal only — ClusterIP, no `HTTPRoute`, no Gateway listener, no DNS record.
This was the explicit ask: the dashboard is not needed from outside the
cluster.

| | |
| --- | --- |
| Service | `9router` in namespace `9router` |
| Cluster DNS | `9router.9router.svc.cluster.local` |
| Port | `20128` |
| OpenAI-compatible API | `http://9router.9router.svc.cluster.local:20128/v1` |
| Dashboard | same host/port, `/` |
| Health | `/api/health` → `{"ok":true}`, unauthenticated |

From a workstation, reach the dashboard with a port-forward rather than by
adding a route:

```bash
kubectl -n 9router port-forward svc/9router 20128:20128
# then http://localhost:20128
```

**Do not promote this to a `lab.` hostname without a deliberate decision.** It
holds provider OAuth tokens and API keys and its own auth is a single shared
password, so exposing it widens the blast radius of that one credential.

## Storage

`storageClassName: local-path`, **not** the repo default `truenas-nfs`. All
state is an embedded SQLite database at `$DATA_DIR/db/data.sqlite`; SQLite
coordinates writers with `fcntl()` locks and cannot share WAL across processes
on a network filesystem, so NFS risks silent corruption. See the storage
section of `AGENT.md`.

Consequences accepted:

- The volume is pinned to one node. Both the control plane is tainted and this
  is a two-node cluster, so everything lands on the worker anyway — but this
  stops being free if a third node is added.
- No TrueNAS snapshots. Nothing is backed up at the storage layer.

The volume also holds `$DATA_DIR/jwt-secret`, which the app generates on first
boot if `JWT_SECRET` is unset. That is why `JWT_SECRET` is deliberately *not*
in the Secret: the app manages it, and it must simply persist. If the volume is
lost, every dashboard session is invalidated (harmless) **and every stored
provider credential is gone** (not harmless — they must be re-authorised).

The Deployment is `replicas: 1` with `strategy: Recreate`. The default
`RollingUpdate` has `maxSurge: 1`, so on an image bump the new pod would start
while the old one still holds the database open; `replicas: 1` does not prevent
that overlap on its own.

## Secrets

`9router-secrets` (SOPS-encrypted in `secret.yaml`) carries only
`INITIAL_PASSWORD`. It overrides the hardcoded `DEFAULT_PASSWORD` of `123456`
in `src/lib/auth/dashboardSession.js`.

It is a **bootstrap** value only: it is consulted just until a password is set
in the dashboard, which persists a bcrypt hash into SQLite that then takes
precedence. Rotating the Secret afterwards changes nothing. Change the password
in the dashboard instead.

## Image pinning

Pinned to `docker.io/decolua/9router:0.5.69` by digest.

The request mentioned `ghcr.io/decolua/9router` as an alternative. Both
registries are pushed by the same CI workflow and both carry semver tags;
`0.5.69` resolves to the *same* digest
(`sha256:47c17576…`) on each, verified against both registry APIs. So the
registry choice is arbitrary here — moving to GHCR is a hostname change with
the digest left intact. `linux/amd64` and `linux/arm64` are both available.

To upgrade, bump the tag *and* the digest together:

```bash
curl -sS https://hub.docker.com/v2/repositories/decolua/9router/tags/<version> | jq -r .digest
```

## Not deployed: the Headroom sidecar

Upstream's `docker-compose.yml` pairs 9Router with
`ghcr.io/chopratejas/headroom` for token-saving, configured via `HEADROOM_URL`.
It is optional, off by default, and was not requested, so it is omitted. Adding
it later means a second Deployment/Service plus `HEADROOM_URL` pointing at it —
the dashboard's `Endpoint → Token Saver → Headroom` panel then needs it
enabled by hand.
