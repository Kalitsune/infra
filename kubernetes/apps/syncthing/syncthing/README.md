# Syncthing

File synchronisation, at `https://sync.geod.es`.

## Authentication

Syncthing has **no login of its own**. A freshly generated `config.xml`
contains an `<apikey>` but no `<user>` / `<password>` element, so anything that
can reach port 8384 is already an administrator with full access to every
configured folder.

Access control therefore lives entirely in `SecurityPolicy/syncthing`, which
attaches Pocket ID OIDC to the HTTPRoute. This is not defence in depth — it is
the only thing in front of an unauthenticated admin panel. Do not detach it,
and do not add a second hostname to the HTTPRoute without attaching a policy to
that one too.

Gateway-level OIDC is used instead of an `oauth2-proxy` Deployment because
Envoy Gateway does it natively; `hermes/agents-dashboard` is the other example
in this repo. That removes a Deployment, a Service and a ConfigMap per app.

The Pocket ID client is named `Syncthing`
(`b65247ed-5b97-4d56-bf37-4d4f703dd1e8`); its secret is in `oidc-secret.yaml`,
encrypted to the cluster age key.

## Storage

Two PVCs, deliberately on different storage classes:

| PVC                | Class         | Holds                        |
| ------------------ | ------------- | ---------------------------- |
| `syncthing-config` | `local-path`  | config.xml, keys, **SQLite** |
| `syncthing-data`   | `truenas-nfs` | the synced files             |

Syncthing 2.x replaced LevelDB with SQLite. Starting 2.1.5 produces
`index-v2/main.db`, `main.db-wal` and `main.db-shm` — WAL mode, which cannot be
shared across processes on NFS and fails by corrupting rather than erroring.
Hence `local-path` for the index, and `strategy: Recreate` on the Deployment so
two pods never hold the database open at once during an image bump.

The synced files are ordinary files, so they stay on NFS where TrueNAS
snapshots apply.

**`local-path` pins the config volume to one node.** The worker is the only
schedulable node today, so this costs nothing now, and would need revisiting if
a third node were added. There are no TrueNAS snapshots of the index — but the
index is derived state and Syncthing rebuilds it by rehashing.

## What is not exposed

Only the GUI (8384) is routed. The device-to-device sync ports — 22000/TCP,
22000/UDP (QUIC) and 21027/UDP (local discovery) — are **not** reachable from
outside the cluster, because the `apps` Gateway has only HTTP/HTTPS listeners
and the Cilium LB pool has no free address on the node LAN: its blocks are
`192.168.100.0/24` (a different subnet from the nodes at `192.168.1.x`) and
`192.168.1.200/32`, which the Gateway already holds.

In practice this instance still syncs: it dials **out** to peers and reaches
them over the public relay pool (`relays.syncthing.net`, confirmed joining a
relay at startup). What it cannot do is accept a direct inbound connection,
which means relay-speed transfers rather than LAN-speed ones.

Giving it a direct port needs one of:

- adding a `192.168.1.0/24` block to the Cilium LB pool, then a
  `LoadBalancer` Service for 22000 — a `kubernetes/network/` change, and the
  pool is shared infrastructure;
- `hostNetwork: true` with host ports, as `matrix/coturn` and
  `media/music-assistant` do — cheaper, but it makes the pod a node-level
  singleton and requires relaxing the namespace's PodSecurity level, which the
  current non-root securityContext otherwise satisfies.

Neither was done here because neither was asked for. Pick deliberately.

## Notes

- Runs as uid 1000. The image defaults to root and Syncthing logs
  `Syncthing should not run as a privileged or system user` when it does.
- `STGUIADDRESS=0.0.0.0:8384` also disables the GUI's Host-header check, so
  requests arriving as `sync.geod.es` are served instead of the
  "Host check error" page. No `insecureSkipHostcheck` is set.
- NFS ignores `fsGroup`. If Syncthing cannot write to `/var/syncthing/data`,
  that is an export ownership question on TrueNAS, not a manifest bug.
- `workingDir: /var/syncthing/data` is load-bearing. A folder accepted from a
  peer is stored with its path set to the bare label (`PKMS`) unless
  `<defaults><folder path>` is set, and Syncthing resolves a relative folder
  path against its working directory — `/` in this image. Without `workingDir`
  it tries `mkdir /PKMS` as uid 1000 and the folder never starts:

      Failed to create folder root directory (folder.label=PKMS
      error="mkdir /PKMS: permission denied")
      Failed initial scan (error="folder path missing" folder.label=PKMS)

  Setting it here rather than a path per folder means every future accepted
  share lands under the data volume automatically.
- `pull: no such file` on a folder whose root exists is **not** a cluster
  problem: it is the remote device answering that it does not have the block it
  advertised. Fix it by rescanning on that device, not here.
