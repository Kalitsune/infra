# vpn-egress

Exit nodes for the netbird mesh. Each `exit-N/` is one pod holding two
containers that share a network namespace:

- **gluetun** dials the commercial VPN and owns the pod's default route.
- **netbird** joins the mesh and is promoted to an exit node from the dashboard,
  so mesh traffic leaves through gluetun's tunnel.

One pod per VPN location, up to the subscription's simultaneous-connection cap.
Switching destination is a client-side choice between exit nodes, not a change
here.

## Finishing an exit node

Both Secrets ship with placeholders and the Deployment is `replicas: 0`, because
gluetun exits immediately on a malformed WireGuard key and a running replica
would be a permanent CrashLoopBackOff. Landing the real credentials and setting
`replicas: 1` belong in the **same commit**.

`secret-vpn.sops.yaml` — from the provider's WireGuard config file:

| key                     | from the `.conf`                       |
| ----------------------- | -------------------------------------- |
| `WIREGUARD_PRIVATE_KEY` | `[Interface] PrivateKey`               |
| `WIREGUARD_ADDRESSES`   | `[Interface] Address`                  |
| `WIREGUARD_PUBLIC_KEY`  | `[Peer] PublicKey`                     |
| `VPN_ENDPOINT_IP`       | `[Peer] Endpoint`, host part           |
| `VPN_ENDPOINT_PORT`     | `[Peer] Endpoint`, port part           |

`secret-netbird.sops.yaml` — `NB_SETUP_KEY`, a reusable setup key created in the
dashboard under Setup Keys.

Those files are encrypted to the operator's age key, so an agent cannot edit
them: adding a key to an existing encrypted file requires decryption. Use
`sops kubernetes/apps/vpn-egress/exit-N/secret-vpn.sops.yaml` from the repo root
(the path matters — `.sops.yaml`'s `path_regex` only matches under `kubernetes/`).

## Three traps, all already handled here

**`FIREWALL_OUTBOUND_SUBNETS`.** gluetun drops everything that is not the
tunnel, which includes cluster DNS. Without the Service CIDR, the pod CIDR and
the LAN listed, every lookup fails with
`write udp ... 10.96.0.10:53: operation not permitted` and the netbird container
can never reach the management server.

**MTU 1200, below netbird's 1280 default.** This WireGuard interface runs
*inside* gluetun's tunnel, so the provider's overhead has already eaten the path
MTU. At 1280 the failure is deceptive: handshakes and DNS work, then large
transfers hang forever.

**netbird issue #5391.** A containerised exit node never gets the `0.0.0.0/0`
accept rule in its `netbird-rt-fwd` chain, only per-route ones, so mesh clients
reach the peer and then get no internet. The chain is rebuilt whenever a client
reconnects, so the workaround has to keep re-asserting rather than run once —
hence the loop in the command. There is no `nft` in the image; `iptables`
(nf_tables backend) is present and works.

## Capabilities

`NET_ADMIN` for both containers (each manages a tunnel interface) and `MKNOD`
for gluetun, because `/dev/net/tun` does not exist in the image and the pod
cannot mount a hostPath. The device node is created at container start.

## Readiness

The netbird container's command is a shell wrapper that stays alive even when
`netbird up` fails, so without an explicit probe the container reports Ready
while registration is broken. The probe greps `netbird status` for
`Management: Connected`.
