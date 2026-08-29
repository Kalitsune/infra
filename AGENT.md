# AGENT.md

Operating manual for coding agents working in this repository.
Read this fully before touching anything.

## What this repo is

The single source of truth for the homelab: a Dell R630 running Proxmox, a Talos
Linux VM hosting a Kubernetes cluster, and everything running on that cluster.
The cluster is reconciled by Flux; Cilium is the CNI and Envoy Gateway is the
ingress/Gateway API implementation.

This repo is declarative. Nothing here is a script that you run to "make it so" —
it is state that a controller converges on. Act accordingly.

## Layout

```
proxmox/                              OpenTofu — VM definitions on the PVE host
talos/                                Talos machine configs (controlplane/worker patches)
kubernetes/                           Flux-managed cluster tree
├── apps/<namespace>/<app>/           workloads, plus that app's httproute.yaml
├── cicd/flux-system/flux-system/     Flux's own bootstrap + controllers
└── network/<namespace>/<app>/        Gateways, Cilium policies, Envoy config
```

| Path            | Applied by                 | Agent may                    |
| --------------- | -------------------------- | ---------------------------- |
| `proxmox/`      | OpenTofu, run by a human   | edit on request; never apply |
| `talos/`        | `talosctl`, run by a human | edit on request; never apply |
| `kubernetes/**` | Flux                       | edit, commit, push           |

## Golden rules

### 1. No out-of-band changes

Git is the only write path to the cluster. You may **never** mutate live state
directly. Concretely, these are forbidden:

- `kubectl apply|create|edit|patch|delete|scale|set|annotate|label`
- `helm install|upgrade|uninstall`
- `flux suspend|resume` (these mutate the object in-cluster)
- any `tofu` or `talosctl` command, and any write to the Proxmox API or web UI —
  editing those files when asked is fine; running the tools never is

If a fix requires one of these, stop and say so. Do not "just do it now and
commit it after" — a hand-applied change that Flux later reverts is worse than
no change at all, because it looks fixed until it isn't.

The permitted mutation is: **edit files under `kubernetes/` → commit → push →
let Flux converge.**

Diagnostics are a separate matter. Reading, port-forwarding, exec-ing a
read-only command into an existing pod and curling an endpoint are all fair
game — see [Diagnostics](#diagnostics--allowed-with-limits).

### 2. If you are allowed to commit, push

Flux tracks **`main`**, and direct pushes to `main` are allowed. There is no PR
gate, so a push _is_ a deploy — the commit you write is live within one
reconcile interval. Validate before you push, not after.

A commit that sits in the local repo changes nothing, since Flux reads the
remote. Committing without pushing leaves the working copy, the local branch and
the cluster all disagreeing. So:

- Commit and push to `main` in the same turn.
- `git pull --rebase` first. A human may have pushed since you started; do not
  resolve a divergence with a merge commit on `main`.
- Never `push --force` or `--force-with-lease` to `main`.
- Never rewrite pushed history.
- Roll back by pushing a `git revert`, never by hand-editing the cluster.
- If you are not sure you have permission to commit, ask before staging.

### 3. If you triggered a reconcile, verify it

A green `git push` is not a successful change. After pushing anything under
`kubernetes/`, you own the outcome until you have confirmed the service is
actually working — as far up the stack as you can get from where you are
running. `flux get` is the first check, not the last one: reach for `kubectl`,
`port-forward` and `curl` to prove the thing serves traffic. Do not assume. See
[Verification](#verification) below.

If verification fails, report the actual controller error — not a guess. Revert
by committing a revert, never by hand-editing the cluster (rule 1).

## Toolchain

Your working set: `git`, `flux`, `kubectl`, `kustomize`, `helm`, `cilium`,
`sops`, `curl`.

`tofu` and `talosctl` are deliberately not on that list. You can edit the files
they consume when asked; you never run them. See
[edit on request, never apply](#talos-and-proxmox--edit-on-request-never-apply).

If a tool you need is missing or unauthenticated, say so and stop. Do not route
around it: no raw `curl` against the Kubernetes API with a scraped token, no
hand-rolled HTTP calls to the Proxmox API, no reimplementing `kustomize build`
by hand. A missing tool is a signal about scope, not an obstacle to solve.

### Read-only commands — run freely

```
kubectl get|describe|logs|events|explain|api-resources|diff
flux get|events|trace|diff|check
helm list|get|template
kustomize build
cilium status
git status|diff|log|show
```

`kubectl diff` and `flux diff` are read-only and are the right way to preview the
effect of a change before committing it.

### Diagnostics — allowed, with limits

Proving a change works usually needs more than `get`. These are allowed:

```
kubectl port-forward svc/<svc> <local>:<remote>   # no state change, always fine
kubectl exec <existing-pod> -- <read-only cmd>    # curl, wget, nslookup, cat, ls
curl / dig / nc                                    # from the host, or through a port-forward
kubectl run tmp-curl --rm -it --restart=Never --image=curlimages/curl -- <args>
```

Limits on those last two:

- `exec` runs read-only commands only. Do not edit config, restart a process, or
  write to a volume from inside a container — that is an out-of-band change with
  extra steps, and it vanishes on the next pod restart.
- `kubectl run` must carry `--rm --restart=Never`. Confirm the pod is gone when
  you are done. Never leave a debug pod running; Flux does not own it and nobody
  will clean it up.

### Never run

Anything in the forbidden list under rule 1, plus:

- `cilium connectivity test` — creates namespaces and long-lived pods, and is slow.
- `kubectl debug` — attaching an ephemeral container mutates the pod spec and
  cannot be undone without deleting the pod. Ask first.
- any curl that is not a `GET`/`HEAD` against a service you are verifying. Do not
  probe an endpoint with `-X POST` to "see what happens".

## Working on the cluster

### Editing an existing app

1. Find the app under `kubernetes/apps/<namespace>/<app>/`.
2. Read the sibling files first — the `kustomization.yaml` tells you what is
   actually included. A file that exists but is not listed is dead.
3. Make the edit. Keep it minimal and scoped to one app per commit.
4. Validate locally before committing:
   ```
   kustomize build kubernetes/apps/<ns>/<app> | kubectl apply --dry-run=client -f -
   flux diff kustomization <name> --path ./kubernetes/apps/<ns>/<app>
   ```
5. Commit, push, verify.

### Adding a new app

Mirror the closest existing app directory rather than inventing a layout. A new
app is not done until it is referenced by its parent kustomization — an
unreferenced directory is silently ignored by Flux and will look like a
mysterious no-op.

Checklist:

- [ ] namespace exists or is created in the same commit
- [ ] added to the parent `kustomization.yaml`
- [ ] resource requests set; limits only where the workload actually needs them
- [ ] any PVC sets `storageClassName: truenas-nfs` explicitly; if the app stores
      state in SQLite, `replicas: 1` **and** `strategy: { type: Recreate }`
- [ ] if it needs to be reachable: an `HTTPRoute` in the app's own directory as
      `httproute.yaml`, listed in that app's `kustomization.yaml` — not an
      Ingress, and not under `network/`. This cluster uses Gateway API via
      Envoy Gateway
- [ ] hostname follows the convention below — `lab.kalitsune.net` unless public
      exposure was explicitly asked for

### Network changes

Routes and plumbing live in different places:

| Resource                                                 | Lives in                         |
| -------------------------------------------------------- | -------------------------------- |
| `HTTPRoute` for an app                                   | `apps/<ns>/<app>/httproute.yaml` |
| `Gateway`, `GatewayClass`, Envoy config, Cilium policies | `network/<ns>/<app>/`            |

An app's route ships with the app. Exposing a new service is a change inside
`apps/`; it should not touch `network/` at all unless the Gateway itself needs a
new listener.

`kubernetes/network/` is the blast-radius directory. A bad `Gateway`, Cilium
network policy or Envoy patch can lock out access to everything, including your
own means of diagnosing it.

- Change one route or one policy per commit.
- Cilium policies are default-allow only until the first policy selects a pod —
  adding your first policy in a namespace silently drops everything else. Check
  what already selects the workload before adding one.
- After a Gateway change, confirm `PROGRAMMED=True` before assuming success.
- Never edit the Gateway that fronts Flux's own webhook receiver or the
  management endpoints without flagging it first.

### Hostnames and exposure

Two suffixes, and the choice is a security decision, not a naming one:

| Suffix                    | For                            | Example                       |
| ------------------------- | ------------------------------ | ----------------------------- |
| `<app>.lab.kalitsune.net` | administrative / internal-only | `portainer.lab.kalitsune.net` |
| `<app>.kalitsune.net`     | deliberately public-facing     | `jellyfin.kalitsune.net`      |

Rules:

- **Default to `lab.`.** Only put a service on the apex domain when exposing it
  publicly is the actual intent, stated in the request. "It needs a hostname" is
  not that.
- Never move an existing route from `lab.` to the apex as a side effect of
  another change. Promoting a service to public is its own commit with its own
  justification.
- Anything with an admin surface, a cluster-level API, a dashboard over the
  infrastructure, or no authentication of its own belongs on `lab.`, full stop.
- Match the suffix to the Gateway and any Cilium policy the siblings use. A
  `lab.` hostname attached to the public Gateway is publicly reachable regardless
  of what it is called — the name does not enforce anything on its own, so check
  which listener you are binding to.

### `kubernetes/cicd/flux-system/`

Flux's own bootstrap. Changes here can break the mechanism that would deliver
the fix. Treat as read-only: propose a diff, explain the failure mode, and let a
human apply it. Version bumps of the Flux components go through
`flux bootstrap`/`flux install --export`, not hand-edited manifests.

### `talos/` and `proxmox/` — edit on request, never apply

You may edit these **only when the request names them.** Never touch them as a
side effect of another task, and never conclude on your own that a Kubernetes
problem needs a VM or node fix — propose that, do not act on it.

When you do edit them, you edit files and nothing else. A human applies.

- Never run `tofu` or `talosctl`. The tooling is likely not installed, and
  `talosconfig` may not be readable.
- You therefore cannot `plan`, cannot validate a schema, and cannot check the
  result against a live node. Write conservatively: mirror the structure and
  field names already in the file rather than reaching for options you remember.
  Talos machine config schemas are version-specific and a field that existed two
  releases ago will be silently ignored or rejected.
- Put these in their own commit. Do not bundle a `talos/` or `proxmox/` change
  with a `kubernetes/` one.

**A push here is not a deploy.** Flux does not watch these directories, so the
commit is inert until a human runs something. Do not report the change as
applied, live, or verified. The correct handoff is:

> Committed `<sha>` touching `proxmox/<file>`. Not applied — you need to run
> `tofu plan` in `proxmox/` and review it. I could not run it here.

Call out anything you suspect will be destructive so it gets checked before the
apply, since you cannot see the plan yourself: changes to VM identity, boot
disk, machine type or a disk shrink typically force a replacement, and replacing
a VM on this host means data loss. The control plane is a single VM — a bad
machine config takes the whole cluster with it, and there is no second node to
fall back on.

## Storage

Persistent volumes use the **`truenas-nfs`** storage class.

- Set `storageClassName: truenas-nfs` explicitly on every PVC. Do not rely on a
  cluster default being what you expect.
- Do not hardcode an NFS server address or export path in a manifest. Go through
  the storage class.
- Check the reclaim policy before you touch an existing PVC:
  `kubectl get sc truenas-nfs -o yaml`. Never delete a PVC — that is a
  stop-and-ask, no matter how obviously orphaned it looks.

Two NFS-specific traps worth knowing before you file a bug against an app:

- **SQLite on NFS.** Several apps here keep their state in an embedded database.
  SQLite has no server process — concurrent writers coordinate through `fcntl()`
  locks on the file, which are unreliable over NFS, and WAL mode cannot be shared
  across processes on a network filesystem at all. Two writers do not produce an
  error, they produce a corrupted database, often noticed long after the fact.
  So, for any SQLite-backed app:
  - Keep it at **one replica**. NFS gives you `ReadWriteMany`, so nothing stops
    you from scaling the Deployment to 2 — the access mode is not permission.
  - Set `strategy: { type: Recreate }`. This is the one people miss:
    the default `RollingUpdate` has `maxSurge: 1`, so on every image bump the new
    pod starts while the old one is still running, both mount the same PVC, and
    both open the database. `replicas: 1` does not prevent that overlap;
    `Recreate` does, at the cost of a few seconds of downtime during updates.
  - These apps are singletons by design anyway — they run background schedulers
    and cache DB state in memory, so a second instance corrupts state and
    duplicates work even where the locking happens to hold.
- **Permissions.** NFS does not honour `fsGroup` the way block storage does. If a
  container fails on a read-only or permission-denied write to its volume, that
  is usually an export/ownership question on the TrueNAS side, not a manifest bug.
  Report it rather than papering over it with a root `securityContext`.

TrueNAS runs as a VM on the same Proxmox host as the cluster. If PVCs across
several unrelated namespaces are failing to mount at once, suspect the storage
VM rather than your change — and report it rather than reaching for `proxmox/`
uninvited.

## Verification

Verify in layers, and do not stop early. Each layer can pass while the next one
fails. This applies to `kubernetes/` only — a `talos/` or `proxmox/` commit is
not deployed by pushing it, and there is nothing for you to verify.

### Layer 1 — did Flux apply it

```bash
# 1. pull the new commit
flux reconcile source git flux-system

# 2. reconcile the kustomization that owns your change
flux reconcile kustomization <name> --with-source

# 3. did anything fail to become ready?
flux get all -A --status-selector ready=false

# 4. what did the controllers actually say?
flux events --for Kustomization/<name>
flux logs --level=error --all-namespaces
```

### Layer 2 — is the workload healthy

```bash
kubectl -n <ns> rollout status deploy/<app> --timeout=5m
kubectl -n <ns> get pods
kubectl -n <ns> describe pod <pod>          # events, not just status
kubectl -n <ns> logs <pod> --tail=100
```

### Layer 3 — is it routable

```bash
kubectl get gateway -A          # PROGRAMMED must be True
kubectl get httproute -A
kubectl -n <ns> describe httproute <name>   # check the Accepted/ResolvedRefs conditions
cilium status
```

The route lives in the app's namespace and the Gateway does not, so attachment
is cross-namespace. That is governed by the Gateway listener's
`allowedRoutes.namespaces` — if it does not select the app's namespace, the
route is created, reconciled, reported `Ready` by Flux, and quietly never
attached. The tell is `Accepted=False` with reason `NotAllowedByListeners` in
the route's conditions, which is why `describe` on the route is not optional
here.

### Layer 4 — the service actually serves

This is the layer that matters. Everything above can be green while the service
is broken. Work outward until something answers:

```bash
# in-cluster, bypassing DNS, TLS and the gateway
kubectl -n <ns> port-forward svc/<svc> 8080:<port>
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/

# through the gateway, bypassing public DNS
curl -sS -o /dev/null -w '%{http_code}\n' \
     --resolve jellyfin.kalitsune.net:443:<gateway-ip> \
     https://jellyfin.kalitsune.net/

# the real path, as a user hits it
curl -sSI https://jellyfin.kalitsune.net/
```

Hit a path the app actually serves — a health endpoint if it has one, otherwise
`/`. A `200` on a login redirect is a pass; so is a `302` to an auth provider.

Reading the failure:

| Symptom                                        | Where the problem is                                                                       |
| ---------------------------------------------- | ------------------------------------------------------------------------------------------ |
| port-forward works, gateway does not           | route/Gateway, not the app                                                                 |
| `404` from Envoy                               | no HTTPRoute matched — hostname/path mismatch, or the route never attached to the listener |
| `503`                                          | route matched, no healthy backend — check the Service selector and pod readiness           |
| connection refused / timeout on the gateway IP | Gateway not programmed, or a Cilium policy dropping it                                     |
| TLS error                                      | certificate not issued yet — check the Certificate resource before blaming the route       |
| works on `--resolve`, fails on the public name | DNS or WAN, **not your change**                                                            |

That last row matters here: the home connection drops to a 4G modem
periodically and the public IP changes with it. If the `--resolve` form works and
the plain form does not, the change is fine and the WAN has flipped. Say that
explicitly rather than reverting a good commit.

The same caution applies to `lab.` hostnames: they are not meant to be reachable
from the open internet, so a failed curl against one may only mean the machine
you are running on is not on the internal network. Verify those through
`port-forward` instead, and do not "fix" a `lab.` service by exposing it.

### Done means

Kustomization `Ready=True` **and** pods `Ready` **and**, for anything routed,
the Gateway programmed, the route accepted, and an HTTP request answered.
`Ready=True` on its own only means the YAML parsed and applied.

If you could not reach layer 4 — no external access from where you are running,
no health endpoint, a service that is not HTTP — say which layer you got to and
what you could not check. An honest "verified to layer 3, could not curl it from
here" is useful. "Deployed successfully" when you only ran `flux get` is not.

Flux's default reconcile interval means a change may take minutes to land on its
own. Prefer an explicit `flux reconcile` over polling and over waiting silently.

## Secrets

Never commit a plaintext secret, token, kubeconfig, talosconfig, age key, or
`.tfstate`. Secrets are SOPS-encrypted at rest in this repo.

- If you need a new secret, create the SOPS-encrypted resource and reference it;
  do not inline the value "temporarily".
- Do not decrypt a secret to standard output while debugging.
- If you find a plaintext credential committed, stop and report it. Do not
  rewrite history to remove it — that is a human decision, and the credential
  must be rotated regardless.

## Conventions

- Commits: `<scope>: <imperative summary>`, e.g. `apps/media: bump jellyfin to 10.10.3`,
  `network/gateway: add route for kavita`. Scope is the path.
- One logical change per commit. Do not bundle a version bump with a refactor.
- YAML: 2-space indent, no tabs, explicit `apiVersion`/`kind`, no trailing
  whitespace. Match the surrounding file over any general style rule.
- Do not reformat or reorder files you did not otherwise need to change.

## When to stop and ask

- The change requires any command from the forbidden list.
- The fix is in `cicd/flux-system/`.
- You concluded a fix belongs in `talos/` or `proxmox/` without being asked.
- A tool you need is missing or unauthenticated.
- Verification fails twice for the same reason.
- The change would remove a PVC, StorageClass, or anything holding data.
- You cannot tell whether something is out-of-band. Assume it is.

Report failures with the exact command, the exact error, and what you would do
next. Do not retry a mutating action hoping for a different result.
