# Hermes

Hermes Agent deployed as a Kubernetes StatefulSet via the `hermes-agent` Helm chart, with OIDC authentication via oauth2-proxy.

## Adding a profile

Profiles live under `profiles/<name>/`. On every deploy, the `seed-profiles` init container reads the `hermes-profiles` ConfigMap and seeds each profile into the persistent volume.

### 1. Create the profile directory

```
profiles/
└── my-profile/
    ├── SOUL.md       # Agent identity and personality
    ├── config.yaml   # Toolsets and model overrides
    └── skills        # Optional: declarative skill list (one per line)
```

**`config.yaml`** — partial override on top of Hermes defaults. Example:

```yaml
toolsets:
  - terminal
  - file
  - coding
  - web
  - search
  - skills
```

**`skills`** — one `category/skill-name` entry per line, referencing bundled skills from the Hermes image (`/opt/hermes/skills/`). If omitted, no skills are seeded and auto-seeding is suppressed. Example:

```
creative/architecture-diagram
github/github-code-review
software-development/systematic-debugging
```

To browse available skills and their categories, run:

```sh
kubectl exec -n hermes statefulset/hermes-hermes-agent -- hermes skills browse
```

### 2. Register the profile in `kustomization.yaml`

Add three entries to the `hermes-profiles` configMapGenerator (the `<name>--` prefix is how the init container discovers profile boundaries):

```yaml
configMapGenerator:
  - name: hermes-profiles
    files:
      # existing profiles ...
      - my-profile--SOUL.md=profiles/my-profile/SOUL.md
      - my-profile--config.yaml=profiles/my-profile/config.yaml
      - my-profile--skills=profiles/my-profile/skills   # omit if no skills file
```

### 3. Allow the profile in the gateway

Add the profile name to `multiplex_profile_allowlist` in `helmrelease.yaml`:

```yaml
config:
  gateway:
    multiplex_profiles: true
    multiplex_profile_allowlist:
      - infra-expert
      - my-profile
```

## How skill seeding works

The `seed-profiles` init container (busybox) runs before Hermes starts on every deploy. For each profile it:

1. Copies `SOUL.md` and `config.yaml` from the ConfigMap into the profile's directory on the PVC.
2. If a `skills` file is present, writes `.no-bundled-skills` to suppress Hermes auto-seeding, removes the **symlinks** a previous run created, then re-links each listed skill into `/opt/hermes/skills/` (baked into the Hermes image). Symlinks mean no data duplication and skills stay in sync with image upgrades.

### It must never `rm -rf` the skills directory

Step 2 originally ran `rm -rf "$dest/skills"` before re-linking. That deleted
**agent-authored skills too** — the ones the running agent writes with
`skill_manage`, which are real directories on the PVC and exist in no
ConfigMap, no image, and nowhere in this repo. Every `helm upgrade` silently
destroyed them, and it happened twice before anyone noticed, because the init
container still exits 0.

The script now deletes only what it created (`find … -type l -delete`, plus
empty leftover directories) and never the bundled targets. Two rules follow:

- **Bundled skills are symlinks; agent-authored skills are real directories.**
  Anything that cleans up must key on that distinction, not on "everything
  that is not in the ConfigMap".
- Agent-authored skills are **not backed up by this repo.** They live only on
  the `data` PVC. Treat them as user data.

Regression test — run it before changing that script:

```sh
kustomize build kubernetes/apps/hermes/hermes > /tmp/built.yaml
uv run --with pyyaml python3 \
  kubernetes/apps/hermes/hermes/test-skill-preservation.py /tmp/built.yaml
```

It seeds a sandbox with a real agent-authored skill plus stale symlinks and
asserts the skill survives, the bundled links are rebuilt, and stale links are
cleaned. The pre-fix script fails it; the current one passes. It is a test, not
a manifest, so it is deliberately absent from `kustomization.yaml`.

## Resources: why they are set here, not left to the chart

The agent was **OOMKilled (exit 137)** on 2026-09-06 after ~3 days of uptime,
on the chart defaults (`requests 256Mi` / `limits 2Gi`).

It was **not** node exhaustion, which is the intuitive but wrong reading:

```console
$ kubectl get node talos-192-168-1-171 -o jsonpath='{.status.conditions}'
MemoryPressure=False (KubeletHasSufficientMemory)

$ free -m          # the NODE, not this pod
              total        used   buff/cache   available
Mem:          15970        4604        10668        11365
```

**`free` inside a container shows the host, not your cgroup** — that is the
trap. Seeing "4 GB used, 10 GB cache" and concluding the kernel sacrificed a
process to protect page cache is backwards: page cache is reclaimable, and the
kernel drops it under pressure rather than killing anything. Those 10 GB were
never the constraint.

The kill came from **this container's own cgroup**, which is the only limit
that applied:

```console
$ cat /sys/fs/cgroup/memory.max        # 2Gi, the chart default
2147483648
$ cat /sys/fs/cgroup/memory.current    # steady state, post-restart
1157173248                             # ~1.15 GiB
$ grep -E '^(anon|file) ' /sys/fs/cgroup/memory.stat
anon 969232384                         # ~970 MiB genuinely resident
file 123207680
```

A ~1.15 GiB floor under a 2 GiB ceiling leaves very little headroom — roughly
one large context window. So `limits` go to **4Gi**.

### `requests` were the other half of the bug

The chart requested `256Mi` while the pod actually needs >1 GiB. For a
Burstable pod the kubelet derives

```
oom_score_adj = 1000 - 1000 * (request / node_allocatable)
```

so an understated request makes the pod the node's **preferred OOM victim**:

```console
$ cat /proc/206/oom_score_adj
984                                    # near the 1000 maximum
```

Raising the request to `1Gi` brings that to ~934 and makes scheduling honest.
Node memory requests move ~3.5 → ~4.2 GiB of 15.1 GiB allocatable, so there is
ample room; limits were already overcommitted at 112%, which is normal and
only bites under genuine node pressure.

**Do not "fix" the request back down to match idle usage.** It is deliberately
close to the real floor, and lowering it re-arms the same failure.

### Checking this later

```sh
kubectl -n hermes get pod hermes-hermes-agent-0 \
  -o jsonpath='{.status.containerStatuses[0].lastState}'   # reason: OOMKilled
kubectl exec -n hermes hermes-hermes-agent-0 -- cat /sys/fs/cgroup/memory.peak
kubectl exec -n hermes hermes-hermes-agent-0 -- cat /sys/fs/cgroup/memory.events
```

`memory.events` is ground truth: a nonzero `oom_kill` counter means this
cgroup hit its own ceiling. The counter resets with the container, so read it
before restarting anything. Note the CPU limit of 2 cores comes from the
chart and is intentionally left alone — Helm deep-merges, and only `memory` is
overridden.

## Chart upgrades: the immutable `volumeClaimTemplates` trap

`spec.chart.spec.version` is a range (`>=1.11.0 <2.0.0`), so a new chart
release is picked up automatically — and **every one of them failed to
upgrade** with:

```
StatefulSet.apps "hermes-hermes-agent" is invalid: spec: Forbidden:
updates to statefulset spec for fields other than 'replicas', 'ordinals',
'template', 'updateStrategy', 'revisionHistoryLimit',
'persistentVolumeClaimRetentionPolicy' and 'minReadySeconds' are forbidden
```

The cause is not this repo's values. The chart stamps
`helm.sh/chart: hermes-agent-<version>` and
`app.kubernetes.io/version: <appVersion>` onto
`volumeClaimTemplates[0].metadata.labels`. A StatefulSet's
`volumeClaimTemplates` is **immutable**, so those two cosmetic labels change on
every release and the API server rejects the whole update. Flux then rolls back,
leaving `Ready=False` with `RetriesExceeded` while the release stays on the old
chart — so the agent looks healthy but is pinned and no config change can land.

`spec.upgrade.force: true` does **not** fix this: it is a replace-style patch,
which still cannot mutate an immutable field.

The fix is the `postRenderers` block in `helmrelease.yaml`, which pins those two
labels back to the values frozen on the live object. It is version-independent —
the labels never change again, so future bumps stop tripping over them — and it
touches only PVC-template metadata, which is decorative.

Do not "modernise" those pinned values to match a newer chart. They must equal
what is on the live StatefulSet:

```sh
kubectl -n hermes get sts hermes-hermes-agent \
  -o jsonpath='{.spec.volumeClaimTemplates[0].metadata.labels}'
```

Verify any chart bump with a real **update** dry-run before pushing. The
namespace flag is essential — without `-n hermes` kubectl validates against
`default`, reports `created`, and silently exercises the CREATE path, which
never checks immutability:

```sh
helm template hermes oci://ghcr.io/jyje/hermes-agent-helm/hermes-agent \
  --version <new> -n hermes -f <values> > /tmp/new.yaml
kubectl apply --dry-run=server -n hermes -f /tmp/new.yaml   # expect "configured"
```

`kubectl diff` is not sufficient here: it computes a dry-run patch and does not
run the StatefulSet update validation, so it shows the label delta without ever
reporting that it is forbidden.

Changing the PVC template for real (size, storage class) is a different job: it
needs the StatefulSet recreated with `--cascade=orphan` so the PVC survives.
That is a human operation — it is outside the git-only write path.

