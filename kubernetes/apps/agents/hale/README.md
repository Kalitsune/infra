# homelab-expert

The infra/homelab agent, running as its own Hermes release.

## Why this is a separate release

It used to be a *profile* inside the `hermes` release, via
`gateway.multiplex_profiles`. That is not an isolation boundary:

- The NFS export squashes every uid to `3001`. A file written by the
  container's uid 10000 lands owned by 3001, so `0600` means "readable by
  3001" — i.e. by everything on that volume.
- Both profiles were the **same OS process** on the **same volume** as the
  **same uid**. Only path separation stood between them.
- The default profile declares no `toolsets`, so it gets the default set
  including `terminal`, and could read the other profile's `.env` directly.

What was reachable that way: a **cluster-admin** kubeconfig and the
**push-capable** infra deploy key. Since the default agent auto-accepts room
invites, a prompt injection against it was a full cluster-admin and
repo-write compromise.

The credential *plumbing* was correct — the infra Secret was mounted only into
this profile's directory and never entered the default profile's `.env`. It
simply did not matter, because a shared volume and a shared process defeat it.

**The fix is process + volume separation, not stricter file modes.** This
release has its own pod, its own PVC (therefore its own NFS export path), its
own gateway process, and mounts the infra Secret nowhere else. It holds even
with uid squashing in place: there is no shared volume to squash into, and the
other pod has no mount, no service-account token
(`automountServiceAccountToken: false`) and no kubeconfig with which to get
one.

Do **not** re-enable multiplexing to add another agent. Give it its own
release.

## Deploying

Flux tracks `main`, so merging is the deploy. Ordering is handled by
`dependsOn: hermes` in the Flux Kustomization — the namespace and the shared
`honcho-client-secrets` / `hermes-managed` objects belong to that release, and
the migration init container mounts its PVC.

```bash
git checkout main && git pull
git merge --ff-only split-homelab-expert
git push origin main

flux reconcile source git flux-system
flux reconcile kustomization hermes --with-source
flux reconcile kustomization homelab-expert --with-source
```

Watch the cutover:

```bash
kubectl -n hermes get pods -w
kubectl -n hermes logs homelab-expert-0 -c seed-agent-home
```

The migration is the line to check. Expect:

```
migrating profile state from the shared volume
  migrated skills
  migrated memories
  migrated sessions
  migrated state.db
migration complete
```

`migration already done or source absent — skipping` on a **first** run means
the old PVC did not mount; the new agent will come up with no history.

## Verifying the isolation

```bash
# the new pod holds the infra credentials
kubectl -n hermes exec homelab-expert-0 -c hermes-agent -- \
  sh -c 'grep -c KUBE_CONFIG /opt/data/.env'          # expect 1

# the default pod does NOT
kubectl -n hermes exec hermes-hermes-agent-0 -c hermes-agent -- \
  sh -c 'grep -c KUBE_CONFIG /opt/data/.env || true'  # expect 0

# and cannot see the other volume at all
kubectl -n hermes exec hermes-hermes-agent-0 -c hermes-agent -- \
  ls /opt/data/profiles 2>&1                          # expect: no such file
```

## After the cutover

1. **Rotate the exposed credentials** — the deploy key and the admin
   kubeconfig were readable by the default profile for the lifetime of the old
   layout. Rotation is not part of this change.
2. **Drop the migration mount.** Once `/opt/data/.migrated-from-shared-volume`
   exists on the new PVC, delete the `migration-old-volume` volume and its
   mount from `helmrelease.yaml`. It is inert after the first run (marker plus
   a no-clobber guard), but it is a cross-release PVC reference and does not
   belong in the steady state.
3. **Reclaim space on the old PVC.** `profiles/infra-expert/` is now a stale
   copy. Deleting data is a stop-and-ask, so it is left in place deliberately
   — it also doubles as the rollback.

## Rollback

Nothing is destroyed by this change: the old PVC keeps every file, and the
migration only ever *copies*. To revert, `git revert` the merge and reconcile;
the previous release picks its profile tree back up.

## Testing

`bin/test-split-init.py` (in the agent's workspace, not this repo) executes
both releases' `seed-agent-home` scripts against a sandbox — 24 assertions
covering the migration, non-clobbering, idempotency, credential encoding,
`.env` mode, skill linking, and the crypto-store reset.

It caught a real bug before deploy: linking bundled skills *before* the
migration created `/opt/data/skills`, so the no-clobber guard skipped the
`skills` entry and silently dropped every agent-authored skill — the same
failure mode that destroyed authored skills twice under the old seeding logic.
The order is now migrate-then-link. Keep it that way.
