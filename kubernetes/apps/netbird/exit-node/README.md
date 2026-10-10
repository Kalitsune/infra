# netbird exit node

One plain netbird client peer, promoted to an exit node (`0.0.0.0/0`) from the
dashboard. Mesh clients that select it send their internet traffic out through
the cluster's own WAN.

This replaces `apps/vpn-egress/exit-1`, which paired netbird with gluetun so
mesh traffic left through a commercial VPN. That pod never ran: both its
Secrets held placeholders and it was parked at `replicas: 0` for its whole
life. Egress through a commercial provider is a different feature from cluster
access, and nothing asked for it — so the extra container, its credentials and
its three MTU/firewall workarounds are gone rather than carried.

## This is not the cluster-access path

Advertising cluster CIDRs is a **route**, which lives in the management
server's database, not here. Cluster access is `../operator/` instead, where
`NetworkRouter`/`NetworkResource` express exposure as CRDs. Keep the two apart:
an exit node answers "send my internet traffic through the cluster", a routing
peer answers "let me reach things inside the cluster".

## Finishing it

The Secret ships with a placeholder and the Deployment is `replicas: 0`,
because the image's entrypoint runs `netbird up` under `set -e` — a bad key is
a permanent CrashLoopBackOff. Landing the real key and setting `replicas: 1`
belong in the **same commit**.

1. Dashboard → Setup Keys → create a **reusable, ephemeral** key with an
   auto-assigned group (e.g. `cluster-exit-nodes`). Ephemeral reaps peers that
   have been offline for over 10 minutes, which is exactly what pod churn
   produces.
2. `sops kubernetes/apps/netbird/exit-node/secret.sops.yaml` and replace
   `NB_SETUP_KEY`. The path matters: `.sops.yaml`'s `path_regex` only matches
   under `kubernetes/`.
3. Dashboard → the peer → **Enable as exit node**, then add an access policy
   whose source is your client group. NetBird is deny-by-default, so a peer
   with no policy looks exactly like a routing failure.

## Why it needs no MTU override

The old gluetun pairing ran this WireGuard interface *inside* the provider's
tunnel, so the 1280 default had to come down to 1200 or large transfers hung
forever. Here the interface sits directly on the pod network (`eth0` MTU 1500,
verified), so the default fits.

## netbird issue #5391 — still handled

A containerised exit node never gets the `0.0.0.0/0` accept rule in its
`netbird-rt-fwd` chain, only per-route ones, so mesh clients reach the peer and
then get no internet. The chain is rebuilt whenever a client reconnects, so the
workaround re-asserts in a loop rather than running once. There is no `nft` in
the image; `iptables` (nf_tables backend) is present and works.

The loop is backgrounded and the image's own entrypoint stays in the
foreground, so a failed `netbird up` still exits the container. The previous
version wrapped everything in a shell that outlived the failure, which is why
it needed a readiness probe to notice broken registration at all.

## Forwarding is inherited, not set

`net.ipv4.ip_forward` is already `1` inside pods on this cluster (verified by
reading `/proc/sys/net/ipv4/ip_forward` from a running pod). The client could
not raise it itself: `/proc/sys` is mounted read-only in containers, and the
kubelet sets no `allowedUnsafeSysctls`. If that ever changes, this peer
silently forwards nothing — check the value before blaming a route.

## Probes

`netbird status --check startup` is used for both startup and readiness because
it asserts management *and* signal are connected. `--check ready` passes while
the daemon is merely idle, which reports a broken registration as healthy.
