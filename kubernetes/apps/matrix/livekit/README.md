# LiveKit + lk-jwt-service — MatrixRTC / Element Call

Element Call is **MatrixRTC**, which is a completely separate stack from the
TURN relay in `../coturn/`:

| | serves | needs |
| --- | --- | --- |
| `coturn` | legacy 1:1 calls | TURN/STUN relay |
| `livekit` | Element Call (group + 1:1) | SFU + JWT broker |

Configuring TURN does **not** make Element Call work. The symptom of the SFU
missing is:

> Call is not supported. The server is not configured to work with Element
> Call. (Domain: kalitsune.net, Error Code: `MISSING_MATRIX_RTC_TRANSPORT`)

Reference: <https://continuwuity.org/calls/livekit>

## The three moving parts

1. **`livekit`** — the SFU. Receives each participant's stream and forwards it
   to the others. `hostNetwork`, because media uses `50100-50200/udp` plus
   `7881/tcp` and a Service cannot enumerate a range.
2. **`lk-jwt-service`** — the broker. Element Call presents a Matrix OpenID
   token; this verifies it against the homeserver and returns a LiveKit JWT.
   Ordinary ClusterIP — no media, just HTTP.
3. **`matrix_rtc.foci`** in continuwuity's ConfigMap — how clients *discover*
   the above. Without it the homeserver returns an empty
   `rtc_transports` list and Element refuses to start a call.

All three are required. Any one missing looks like "calls don't work".

## Routing

Both services sit behind **one** hostname, `livekit.kalitsune.net`, split by
path in `httproute.yaml`:

- `/sfu/get`, `/get_token`, `/healthz` → `lk-jwt-service:8081`
- everything else → `livekit:7880` (the signalling WebSocket)

Get that split wrong and Element Call asks the SFU for a token, gets a 404,
and the call never starts.

The signalling route sets a 3600s timeout. A call's WebSocket stays open for
its whole duration, and Envoy's 15s default would sever it — the same trap
that caused continuwuity's `/sync` 504s.

## Ports the router must forward

To **`192.168.1.171`** (the worker; both media pods are pinned there):

| Port | Protocol | Service |
| --- | --- | --- |
| `50100-50200` | UDP | LiveKit media |
| `7881` | TCP | LiveKit TCP fallback |

`7880` is **not** forwarded — signalling arrives through Envoy on 443.

The ranges are deliberately disjoint from coturn's `50201-65535`, per the
continuwuity docs' guidance to keep LiveKit below coturn.

## Public IP handling — better than coturn's

LiveKit sets `use_external_ip: true`, so it discovers the WAN address via
STUN at startup instead of hardcoding it. A WAN failover is picked up by
restarting the Deployment; no config edit. coturn's `external-ip` cannot do
this, which is why that file carries a warning and this one does not.

## The shared key pair

`livekit-keys` holds one credential in two shapes:

- `LIVEKIT_KEYS` = `"<key>: <secret>"` — LiveKit's own key map
- `LIVEKIT_KEY` / `LIVEKIT_SECRET` — split, for lk-jwt-service to sign with

They must stay in sync. If they diverge, the broker mints JWTs the SFU
rejects, and the client sees a connection that opens and immediately closes.

## Verifying

```bash
# 1. Does the homeserver advertise the SFU?
curl -H "Authorization: Bearer <token>" \
  https://matrix.kalitsune.net/_matrix/client/unstable/org.matrix.msc4143/rtc/transports
# -> should list livekit.kalitsune.net, NOT {"rtc_transports":[]}

# 2. Is the broker alive through the gateway?
curl -sS https://livekit.kalitsune.net/healthz

# 3. Is the SFU answering signalling?
kubectl -n matrix logs deploy/livekit
```

The full token-exchange test (OpenID token → `/get_token` → LiveKit
connection tester) is in the continuwuity doc linked above.

## Known limitation: single node

No Redis is configured, so this is a **single-instance** SFU. Setting
`replicas: 2` will not work — LiveKit needs Redis to share room state, and
without it the two instances would each think they own a room. Scaling is a
real change, not a replica bump.
