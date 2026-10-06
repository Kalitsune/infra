# netbird (self-hosted control plane)

`netbirdio/netbird-server` 0.80.0 — a single binary carrying management, signal,
relay and the embedded Dex IdP — plus the dashboard SPA in the same pod.
Served from `https://netbird.kalitsune.net`.

## Why the apex, not `lab.`

A mesh VPN is only useful if peers can reach the control plane from hostile
networks: a phone on mobile data, a laptop in a cafe. That is the same argument
that puts the Matrix homeserver on the apex, and it is public exposure as the
*function*, not as a convenience. The surface is narrowed instead of the
hostname: the only account is the configured owner, local signup is closed, and
the admin UI sits behind that login.

## The one thing that will bite you

**`server.auth.issuer` must be this server's own `/oauth2`, never Pocket ID.**

netbird-server *hosts* its OIDC issuer — discovery, JWKS, token and device
endpoints all live under `/oauth2` on this hostname. Dex refuses to start with an
issuer it does not serve, and the failure is one line with no context:

```
FATL ... failed to create embedded IDP service: issuer is required
```

Pocket ID is added **afterwards**, as a second connector, from the dashboard's
Settings → Identity Providers. That is stored in the database, so it is
deliberately not expressed in this directory. There is no manifest to change for
it, and pointing `AUTH_AUTHORITY` at `id.kalitsune.net` to "wire it up properly"
re-breaks login.

## Ports: :8080, not :443

The dashboard container is nginx on `:80` and shares this pod's network
namespace, so the server cannot have `:80` — and `listenAddress: ":443"` from
upstream's example is equally wrong here, since Envoy terminates TLS. Hence
`:8080`, with the Service and HTTPRoute agreeing. A mismatch shows up as
`bind: address already in use`, not as a routing error.

## Routing

`appProtocol: kubernetes.io/h2c` on the `netbird-server` Service is load-bearing:
management and signal are gRPC over cleartext HTTP/2, and without the hint Envoy
talks HTTP/1.1 upstream and every call dies with
`upstream connect error ... reset reason: protocol error`. The documented
`BackendTrafficPolicy` `http2: {}` knob does *not* work (envoyproxy/gateway#8648).

`/oauth2` is routed to the server, ahead of the `/` catch-all. If it falls
through to nginx, discovery returns the SPA's `index.html` and login fails with a
JSON parse error.

The gRPC and relay rules set `timeouts.request: "0s"`. Peers hold management and
signal streams open for their whole session, and Envoy's default 15s request
timeout would sever them.

## Secrets

`secret.yaml` is a whole-file SOPS Secret holding `config.yaml`. It is encrypted
to the operator's age key, so an agent cannot read or edit it — the only safe
move is to regenerate the whole file, which is fine because every value in it is
either freshly generated or non-secret.

`store.encryptionKey` **must** be set. Without it the server mints a new key on
every boot and logs `DataStoreEncryptionKey generated (...)`; anything it
encrypted at rest becomes unreadable after the next restart. Rotating it later
orphans that data, so it has to be right before peers enrol.

Do not hand-edit this file with `sed`. An earlier attempt did, failed to notice
`sops -d` had errored, and left five duplicated top-level `server:` blocks —
YAML is last-wins, so the final block silently won and discarded all the auth
config.

## Storage

`local-path`, not the repo default `truenas-nfs`: SQLite over NFS corrupts under
lock contention. That also makes the PVC node-bound — see the storage notes in
the root `AGENT.md`.

## Exit nodes

`../../vpn-egress/` holds the gluetun+netbird egress pairs. They register as
peers and are turned into exit nodes from the dashboard; nothing in this
directory references them.
