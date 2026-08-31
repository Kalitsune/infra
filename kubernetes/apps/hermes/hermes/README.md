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
2. If a `skills` file is present, wipes `<profile>/skills/`, writes `.no-bundled-skills` to suppress Hermes auto-seeding, then creates symlinks from the profile's skills directory into `/opt/hermes/skills/` (baked into the Hermes image). Symlinks mean no data duplication and skills stay in sync with image upgrades.
