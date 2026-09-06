# Continuwuity — Matrix homeserver

A community-maintained fork of conduwuit: a single-binary Matrix homeserver in
Rust with an embedded RocksDB. No external database, no worker processes.

- Upstream: <https://forgejo.ellis.link/continuwuation/continuwuity>
- Docs: <https://continuwuity.org/>
- Server name: `kalitsune.net` — user IDs look like `@you:kalitsune.net`
- Actually served from: `matrix.kalitsune.net` (the apex delegates to it)

## Layout

| File | What it holds |
| --- | --- |
| `configmap.yaml` | every non-secret config option, as `CONTINUWUITY_*` env vars |
| `secret.yaml` | the registration token (SOPS-encrypted) |
| `statefulset.yaml` | the single server pod + its RocksDB volume |
| `httproute.yaml` | `matrix.kalitsune.net` → `continuwuity:8008` |

Continuwuity maps every TOML config key to an environment variable: uppercase,
prefixed `CONTINUWUITY_`, with `__` for nesting. So `[global.well_known].server`
becomes `CONTINUWUITY_WELL_KNOWN__SERVER`. Full option list:
<https://continuwuity.org/reference/config.html>

## Decisions worth knowing before you change something

### `server_name` is permanent

`kalitsune.net` is baked into every event, room ID and user ID this server
creates. **Changing it requires wiping the database.** It is not a hostname
you can refactor later.

The apex was chosen for `@you:kalitsune.net` user IDs. The cost is that
delegation **must be served from `https://kalitsune.net/`**, which resolves
to Vercel (76.76.21.21) and is not this cluster — see below.

### Federation runs on 443, not 8448 — and the apex must delegate

Matrix's default federation port is 8448. Rather than add an 8448 listener to
the shared `apps` Gateway (a change in `kubernetes/network/`, the blast-radius
directory) and a second router port-forward, this server uses `.well-known`
delegation.

Because `server_name` is the apex, **remote servers and clients resolve
`kalitsune.net`, not `matrix.kalitsune.net`.** So the website project — not
this repo — must serve:

```
https://kalitsune.net/.well-known/matrix/server
  {"m.server": "matrix.kalitsune.net:443"}

https://kalitsune.net/.well-known/matrix/client
  {"m.homeserver": {"base_url": "https://matrix.kalitsune.net"}}
```

Both need `Content-Type: application/json` and CORS
(`Access-Control-Allow-Origin: *`) on the client document — browser-based
clients fetch it cross-origin and fail silently without it. A `308` to
`www.` that lands on an HTML page (the current state) does **not** count:
the response body must be the JSON above.

Continuwuity still serves its own copies at `matrix.kalitsune.net` from
`CONTINUWUITY_WELL_KNOWN__*`. Nothing federating reads those anymore, but
keep them identical to the apex's — a mismatch between the two is the classic
cause of "federation tester passes, clients break".

Verify from outside with the Matrix Federation Tester, against the **apex**:
<https://federationtester.matrix.org/#kalitsune.net>

### It is on the apex domain on purpose

Every other internal service here lives on `*.lab.kalitsune.net`. A homeserver
cannot federate if other homeservers cannot reach it, so this one is public —
that is the feature, not an oversight. The surface is kept narrow instead:

- registration requires a token (`secret.yaml`, never in plaintext git)
- the room directory is not federated (`allow_public_room_directory_over_federation: false`)
- built-in web pages are `noindex` (`allow_web_indexing: false`)

### Storage is `local-path`, not `truenas-nfs`

Deliberate deviation from the repo default. Continuwuity's RocksDB needs POSIX
`fsync()` durability and byte-range locking that NFS does not reliably provide,
and it uses **direct I/O by default**, which upstream documents as unsafe on
network filesystems. The failure mode is silent corruption, not an error.

Consequences you have accepted:

- the volume is pinned to one node (fine today — the control plane is tainted,
  so everything lands on the worker anyway)
- **no TrueNAS snapshots.** Back up at the application layer, see below.

The volume holds the database *and* all uploaded media, hence 20Gi.

### One replica, always

Continuwuity does not support horizontal scaling and has no distributed
locking. Two pods on one volume corrupt RocksDB. This is a StatefulSet rather
than a Deployment specifically because its rollout is ordered: the old pod is
fully gone before the new one starts, so there is never a moment with two
writers — which a Deployment's default `RollingUpdate` (`maxSurge: 1`) would
create on every image bump.

### No CPU limit

Federation is bursty, and verifying signatures when joining a large room is
CPU-bound. Throttling it turns a slow join into a failed one. Memory *is*
capped, because a runaway RocksDB cache would threaten the whole node.

The `TOKIO_WORKER_THREADS` / `*_CAPACITY_*` settings in the ConfigMap exist
because Continuwuity otherwise sizes its caches from the **node's** core count
(8 here), not the container's share, and would grow past the memory limit.

## First run

Continuwuity is in **first-run mode** until one real local account exists, and
in that mode it rejects *every* registration token except a single-use
bootstrap token it generates in memory at startup and prints to stderr. The
token in `secret.yaml` does not work yet — that is by design upstream, not a
misconfiguration.

So the order is:

1. Read the bootstrap token from the log:

   ```bash
   kubectl -n matrix logs continuwuity-0 \
     | sed -E 's/\x1b\[[0-9;]*[A-Za-z]//g' \
     | grep -m1 'using the registration token'
   ```

   The `sed` is not optional: the welcome banner is printed with ANSI colour
   escapes even though `CONTINUWUITY_LOG_COLORS` is `false` (that setting
   governs the tracing log lines, not the banner), and the raw token is
   wrapped in them. Copying it straight out of the terminal picks up
   invisible `ESC[;` bytes and the token is then rejected.

   It is regenerated on every pod restart and never written to the
   database. If it is ever captured somewhere it should not be (a log
   shipper, a pasted terminal, an agent transcript), rotate it by bumping
   `kalitsune.net/bootstrap-token-generation` in `statefulset.yaml` and
   pushing — the restart invalidates the old one permanently.

2. Point a Matrix client (Element, Cinny, FluffyChat) at
   `https://matrix.kalitsune.net`, register, and supply that token.

   Enter the server as `matrix.kalitsune.net` explicitly. Until the apex
   serves the delegation documents, typing `kalitsune.net` will not resolve
   — the client has no way to find the homeserver yet. Your user ID is
   `@you:kalitsune.net` regardless of which address you connected through;
   the ID comes from `server_name`, not from the host you typed.

3. That first account is automatically made **server admin** and invited to
   the admin room, where `!admin` commands work.

From then on `CONTINUWUITY_REGISTRATION_TOKEN` in `secret.yaml` is the live
token for any further accounts. To read it without printing it to a terminal:

```bash
kubectl -n matrix get secret continuwuity-secrets \
  -o jsonpath='{.data.CONTINUWUITY_REGISTRATION_TOKEN}' | base64 -d > ~/token.txt
```

Once your own account exists, consider setting
`CONTINUWUITY_ALLOW_REGISTRATION: "false"` in `configmap.yaml` to close the
server, or rotate the token by editing `secret.yaml` with `sops`.

## Admin commands

Everything is driven from the admin room, not a CLI:

```
!admin server show-config      # careful: prints secrets into the room
!admin users list
!admin token generate
!admin debug ping <server>
```

## Backups

There are no volume snapshots on `local-path`. Continuwuity has its own online
RocksDB backup engine — set `CONTINUWUITY_DATABASE_BACKUP_PATH` and
`CONTINUWUITY_DATABASE_BACKUPS_TO_KEEP`, then trigger with
`!admin server backup-database`. Not configured yet; see
<https://continuwuity.org/maintenance.html#backups>.

## OIDC via Pocket ID — ACTIVE

Delegated authentication against `id.kalitsune.net`
(`[global.oauth.oidc]`, i.e. `CONTINUWUITY_OAUTH__OIDC__*`). Accounts come
from the IdP instead of local passwords.

### What this changed, and what it cost

With delegated auth active Continuwuity behaves as if
`compatibility_mode = "exclusive"`:

- **Only OAuth-capable clients can sign in.** Element and **Element X** (the
  client in use here) implement next-gen OAuth login. Cinny, FluffyChat and
  Nheko generally do not, and will fail with a login error. That is a client
  limitation, not a server fault.
- **Legacy registration is disabled**, including the first-run bootstrap
  token. `CONTINUWUITY_REGISTRATION_TOKEN` in `secret.yaml` no longer does
  anything for new signups; users come from Pocket ID.

This was only safe to enable because `@maple:kalitsune.net` already existed
and was server admin. **Never enable OIDC on a server with zero accounts** —
legacy registration is the only way to create the first one, and turning
this on removes it, leaving no admin and no path to create one.

### The wiring

| Piece | Where |
| --- | --- |
| `discovery_url`, `prompt_for_localpart` | `configmap.yaml` |
| `client_id`, `client_secret` | `oidc-secret.yaml` (SOPS-encrypted) |
| secret mounted into the pod | `envFrom` in `statefulset.yaml` |

The `client_id` is a Pocket ID UUID and lives in the encrypted Secret next to
the client secret rather than in the ConfigMap. It is not sensitive on its own
— an OAuth client ID is public by design — but keeping the credential pair
together means rotating the Pocket ID client touches exactly one file.

All three must change together. Continuwuity builds its config from env var
**names**, so mounting the secret without `discovery_url` creates a partial
`oauth.oidc` section — not an absent one — which can fail config parsing at
startup.

The redirect URI registered in Pocket ID is:

```
https://matrix.kalitsune.net/_continuwuity/oidc/complete
```

Note this is the **host clients reach**, not the `server_name`. Those differ
here (`server_name` is the apex `kalitsune.net`), and using the server_name
would break the callback.

`discovery_url` takes the **base** URL — Continuwuity appends
`/.well-known/openid-configuration` itself. Verified against the live IdP:
issuer `https://id.kalitsune.net`, S256 PKCE, scopes include `openid`.

### Linking an existing account

`prompt_for_localpart` is left at its default `true`. On first OIDC sign-in
you are asked which user ID to use — **enter the existing one
(`@maple:kalitsune.net`) and then its old password** to link the accounts.
Skipping that creates a second, separate shadow account.

### Rolling it back

Comment out the two `CONTINUWUITY_OAUTH__OIDC__*` keys in `configmap.yaml`,
remove the `continuwuity-oidc` entry from `envFrom` in `statefulset.yaml`,
bump `kalitsune.net/config-generation`, and push. Legacy password login
returns. Accounts that only ever existed via the IdP have no local password,
so keep one password-capable admin account as a way back in.

### Rotating the client secret

```sh
sops kubernetes/apps/matrix/continuwuity/oidc-secret.yaml
```

Then bump `kalitsune.net/config-generation` in `statefulset.yaml` in the same
commit — a Secret consumed through `envFrom` is read once at container start,
so editing it alone does not restart the pod.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Federation tester fails, clients fine | `.well-known` delegation, or DNS for `matrix.kalitsune.net` |
| Very slow first join to a big room | expected — signature verification; see upstream troubleshooting |
| Joined a room but the client never shows it | upstream bug !779 — clear the client cache |
| Pod crashloops citing corruption | `CONTINUWUITY_ROCKSDB_RECOVERY_MODE: "2"` (PointInTime), run 30-60min, then revert |
| `DNS No connections available` in logs | CoreDNS overload under federation load; consider a dedicated resolver |
