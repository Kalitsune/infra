# coturn — TURN/STUN relay for Matrix voice

Continuwuity does not relay call media. It mints time-limited TURN
credentials signed with a shared secret, hands them to clients, and the
clients talk to **this** server directly. Without it, calls work only when
both parties can reach each other directly — which usually means same LAN,
no symmetric NAT.

Reference: <https://continuwuity.org/calls/turn>

## The part that is not in this repo

**This will not work until the router forwards ports to the worker node.**
That is a manual step on the home router, outside the cluster and outside
GitOps:

| Port          | Protocol | Purpose              |
| ------------- | -------- | -------------------- |
| `3478`        | UDP+TCP  | STUN/TURN            |
| `5349`        | TCP      | TURN over TLS        |
| `50201-65535` | UDP      | media relay range    |

All forwarded to **`192.168.1.171`** (the worker node), because this
Deployment uses `hostNetwork`.

`turn.kalitsune.net` must also resolve publicly to the WAN address. It did
not resolve at all when this was written.

### Why hostNetwork rather than a LoadBalancer

A `Service` has to enumerate every port it forwards. The media range is
~15 000 ports, which cannot be expressed — and a Service carrying only
3478/5349 would accept the control channel then silently drop every relayed
stream. The symptom is a call that connects and then has no audio, which is
a miserable thing to debug.

`hostNetwork` is also what coturn's own Docker documentation recommends.
The cost is that the pod is pinned to one node (`nodeSelector`), because the
router forward targets a single address.

## Trap: the WAN address is baked into the config

`external-ip=82.64.31.214/192.168.1.171` in `configmap.yaml` tells coturn to
listen on the private address but advertise the public one. Relay candidates
carry that public address, so **if the WAN IP changes the value must be
updated**. `AGENT.md` notes the connection periodically fails over to a 4G
modem, which changes the public IP — and if that path is CGNAT, inbound
forwarding cannot work at all and external calls will fail regardless of
this config.

That is the most likely cause of "voice broke and nothing changed".

## The shared secret

`coturn-secret` (`static-auth-secret`) and `continuwuity-turn`
(`CONTINUWUITY_TURN_SECRET`) hold the **same value** in two SOPS-encrypted
Secrets, one per namespace-consumer. They are generated together. If they
ever diverge, clients receive credentials the relay rejects, and calls fail
with no useful client-side error.

coturn has no env-var interpolation in its config file, so the secret cannot
live in the ConfigMap. An init container concatenates the static config with
the secret line into an `emptyDir` at startup.

## Verifying

Layer 1 — is the relay up:

```bash
kubectl -n matrix logs deploy/coturn
kubectl -n matrix get pod -l app.kubernetes.io/name=coturn -o wide
```

Layer 2 — is the homeserver handing out credentials (needs a client token):

```bash
curl "https://matrix.kalitsune.net/_matrix/client/v3/voip/turnServer" \
  -H "Authorization: Bearer <token>" | jq
```

A `404` here means `turn_uris` is empty — per MSC4166 that is the documented
response when TURN is unconfigured, not a sign the relay is down.

Layer 3 — does relaying actually work. This cannot be tested from inside the
cluster, because the pod has no path back to its own WAN address. Paste the
credentials from the call above into
[Trickle ICE](https://webrtc.github.io/samples/src/content/peerconnection/trickle-ice/)
and look for `relay` candidates. No relay candidate means the port forward or
DNS is missing.

## Abuse surface

A TURN relay forwards UDP to wherever a client asks, so an unhardened one is
a way to probe the LAN from the internet. `turnserver.conf` denies all
RFC1918, loopback, link-local, CGNAT and special-use ranges, disables TCP
relaying, blocks multicast peers, and sets per-user and total quotas. Do not
remove those to "fix" a connectivity problem — if a legitimate peer is being
denied, the cause is almost always the `external-ip` line or a missing port
forward.
