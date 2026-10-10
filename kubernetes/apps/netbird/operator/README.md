# netbird kubernetes operator

`netbird-operator` 0.8.0 — makes cluster exposure declarative. A
`NetworkRouter` creates a NetBird network plus a pool of routing-peer pods; a
`NetworkResource` publishes one `ClusterIP` Service into that network as
`<service>.<namespace>.<zone>`.

This is the "cluster as a NetBird network" half. The exit node in
`../exit-node/` is the other direction and shares nothing but the namespace.

## Why the operator and not a hand-rolled routing peer

A plain client Deployment can advertise the pod and Service CIDRs just as well,
but its routes, groups and policies live in the management server's database —
invisible to this repo. The operator's CRDs put exposure in git, which is the
whole point of this cluster. The cost is a new dependency and a management-API
token.

## Three things must exist before a CRD will reconcile

1. **The API token.** `secret.sops.yaml` holds `NB_API_KEY`, a **personal
   access token for a service user**, not a setup key. Create it under the
   dashboard's Users → service user, then
   `sops kubernetes/apps/netbird/operator/secret.sops.yaml`. It ships as a
   placeholder; an agent cannot encrypt a replacement into an existing file.
2. **The DNS zone.** Dashboard → DNS → Zones → Add Zone, e.g.
   `k8s.kalitsune.net`, distributed to the client group that should resolve it.
   Create the zone only — leave its records empty; the operator writes the `A`
   records. A `NetworkRouter` whose `dnsZoneRef` names a zone that does not
   exist sits unreconciled with no other symptom.
3. **A group and a policy.** NetBird is deny-by-default and the operator writes
   **no** access policies. Put peers in a dedicated group, never `All`.

## Shape of the CRDs

```yaml
apiVersion: netbird.io/v1alpha1
kind: NetworkRouter
metadata:
  name: homelab
  namespace: netbird
spec:
  dnsZoneRef:
    name: k8s.kalitsune.net
  workloadOverride:
    # Default is 3. Two schedulable nodes here, and the operator spreads
    # replicas with a ScheduleAnyway topology constraint -- so 3 replicas means
    # two peers on one node, which buys nothing over 2.
    replicas: 2
---
apiVersion: netbird.io/v1alpha1
kind: NetworkResource
metadata:
  name: jellyfin
  namespace: media
spec:
  networkRouterRef:
    name: homelab
    namespace: netbird
  serviceRef:
    name: jellyfin
  groups:
    - name: kubernetes-services
```

Those live in the consuming app's directory, next to its `httproute.yaml` — not
here. This directory is only the controller.

## The data path is unproven on this cluster

`NetworkResource` uses the Service's **ClusterIP** as the resource address, so
reaching it from the mesh depends on Cilium translating *forwarded* traffic
(not socket-originated) to a Service VIP. This cluster runs
`kube-proxy-replacement=true`, where load-balancer translation hangs off
sockets and host paths. Pod-CIDR routing has no such doubt; ClusterIP routing
does, and no manifest-level check distinguishes them.

**Test it with a real mesh client curl before promising DNS-name access.** If
it fails, the fallback is a plain routing peer advertising `10.244.0.0/16`
(pod CIDR) as a dashboard route, reaching pods directly instead of VIPs.

## The webhook is the dangerous part

The chart's only webhook **mutates every pod CREATE in the cluster** to inject
`SidecarProfile` sidecars. Upstream defaults are `failurePolicy: Fail` with no
selector, so an unavailable operator blocks all pod creation — including the
pods that would fix it, on a cluster whose recovery path is pod creation.

Here it is `failurePolicy: Ignore` and scoped to namespaces labelled
`netbird.io/sidecar-injection=enabled`. Nothing carries that label. If sidecar
injection is ever wanted, label that one namespace; do not widen the selector.

## RBAC worth knowing

The operator gets cluster-wide `secrets` `get/list/watch/patch/update/create/
delete`, plus `create/delete` on Deployments, Services, ServiceAccounts, Roles
and RoleBindings. `clusterSecretsPermissions.allowAllSecrets: false` does
**not** narrow it — the chart's `if` is `or keyFromSecret allowAllSecrets`, and
the API key *is* `keyFromSecret`, so the rule renders regardless (verified by
rendering both ways). Treat this controller as cluster-admin-adjacent.

## CRDs and upgrades

The chart ships CRDs under `crds/`, which Helm installs and never upgrades.
The `OCIRepository` tag is therefore **pinned**, not a range, and the
HelmRelease sets `install.crds: Create` / `upgrade.crds: CreateReplace` so a
deliberate bump actually lands new CRD fields.

## Client image

`routingClientImage` pins `netbird:0.80.0` to match the control plane. The
operator's built-in default is `0.74.7`, six minor versions behind what
`apps/netbird/server` runs.
