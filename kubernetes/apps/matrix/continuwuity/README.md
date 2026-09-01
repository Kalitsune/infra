# Continuwuity — Matrix homeserver

A community-maintained fork of conduwuit: a single-binary Matrix homeserver in
Rust with an embedded RocksDB. No external database, no worker processes.

- Upstream: <https://forgejo.ellis.link/continuwuation/continuwuity>
- Docs: <https://continuwuity.org/>
- Server name: `matrix.kalitsune.net` — user IDs look like `@you:matrix.kalitsune.net`

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

`matrix.kalitsune.net` is baked into every event, room ID and user ID this
server creates. **Changing it requires wiping the database.** It is not a
hostname you can refactor later.

It is not the bare apex `kalitsune.net` (which would give prettier
`@you:kalitsune.net` IDs) because that would require serving
`/.well-known/matrix/{server,client}` from `https://kalitsune.net/`, and that
name resolves to Vercel — a host this cluster does not control.

### Federation runs on 443, not 8448

Matrix's default federation port is 8448. Rather than add an 8448 listener to
the shared `apps` Gateway (a change in `kubernetes/network/`, the blast-radius
directory) and a second router port-forward, this server publishes
`.well-known` delegation:

```
/.well-known/matrix/server  ->  {"m.server": "matrix.kalitsune.net:443"}
/.well-known/matrix/client  ->  {"m.homeserver": {"base_url": "https://matrix.kalitsune.net"}}
```

Continuwuity serves both documents itself, from the same hostname the
delegation points at, so it is self-consistent and needs no second web server.
Remote homeservers fetch the delegation and then talk to 443, which is already
open and already terminated by Envoy.

Verify it from outside with the Matrix Federation Tester:
<https://federationtester.matrix.org/#matrix.kalitsune.net>

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

The first account registered is automatically made **server admin** and
invited to the admin room, where `!admin` commands work.

1. Point any Matrix client (Element, Cinny, FluffyChat) at
   `https://matrix.kalitsune.net`.
2. Register. Supply the registration token when asked.
3. Accept the invite to the admin room.

To read the token without printing it into a terminal:

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

## Optional: OIDC via Pocket ID

Continuwuity supports delegated authentication against an OIDC provider
(`[global.oauth.oidc]`, i.e. `CONTINUWUITY_OAUTH__OIDC__*`), which would let
accounts come from `id.kalitsune.net` instead of local passwords.

**Not enabled**, and the reason matters: when delegated auth is active
Continuwuity behaves as if `compatibility_mode = "exclusive"`, and **any Matrix
client that does not support next-gen OAuth login can no longer sign in.**
Client support is still patchy, so this trades away working clients. Revisit
once your preferred client supports it.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Federation tester fails, clients fine | `.well-known` delegation, or DNS for `matrix.kalitsune.net` |
| Very slow first join to a big room | expected — signature verification; see upstream troubleshooting |
| Joined a room but the client never shows it | upstream bug !779 — clear the client cache |
| Pod crashloops citing corruption | `CONTINUWUITY_ROCKSDB_RECOVERY_MODE: "2"` (PointInTime), run 30-60min, then revert |
| `DNS No connections available` in logs | CoreDNS overload under federation load; consider a dedicated resolver |
