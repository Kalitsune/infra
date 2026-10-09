# finnegan

The agent that writes other agents. Interviews the operator, drafts a new
agent's `SOUL.md`, then hands the build to `hale` over A2A.

## Why it holds no credentials

finnegan's entire input is operator prose — which is exactly the surface a prompt
injection arrives on. So it is given nothing an injection could spend: no
kubeconfig, no deploy key, no `terminal`, no `code_execution`. It drafts text
and delegates.

`hale` (the `homelab-expert` release) holds the cluster-admin kubeconfig and
the push-capable deploy key, and only accepts A2A work from named,
token-authenticated peers. Asking hale is not a workaround for finnegan's lack of
access; it *is* the design.

Direction is one-way, **finnegan -> hale**:

- finnegan runs the outbound A2A **client** only. No `gateway.platforms.a2a`
  block, no `A2A_PORT`, no inbound Service port. Nothing can drive finnegan
  remotely.
- The delegation graph stays a tree: `umami -> hale`, `finnegan -> hale`. No cycle
  is possible by construction, not just by the ping-pong turn cap.

Declaring a peer under `a2a_agents` is also what turns the `a2a` toolset on at
all — the client tools are config-gated (`_a2a_tools_available`) and stay
unregistered when no peer is configured.

## Naming

`finnegan` is a person at the relay station, like every agent here. The name is
the identity everywhere: HelmRelease, StatefulSet, this directory, the
hostname `finnegan.agents.kalitsune.net`, and (when it gets one) the Matrix
localpart. Convention and the current roster live in its `SOUL.md`.

## What it still needs from the operator

**A Matrix account**, if it should be reachable from chat. That needs
`mas-cli manage register-user` on the homeserver plus a SOPS Secret with the
access token — mirror `../homelab-expert/matrix-secret.yaml`. Without it,
finnegan is reachable through its dashboard and the OpenAI-compatible API only.

The A2A token needs nothing: hale reads `A2A_TOKEN_HALE` straight from
`finnegan-a2a` into its `A2A_PEER_TOKENS` (see `../homelab-expert/helmrelease.yaml`),
so both ends share one value. After rotating it, bump `env-generation` on both
releases. Removing finnegan means removing those hale env entries too, or hale
stops starting.

## Deliberate omissions

- **No Honcho memory.** That needs the `honcho.json` seeding init container
  plus the `honcho-ai` SDK installer the siblings carry (~60 lines) for an
  agent whose output is a document, not a relationship. Copy both from
  `../hermes` if finnegan starts needing to recall past design decisions.
- **No `hermes-secrets` envFrom.** It consumes neither `HASS_TOKEN` nor
  `HONCHO_API_KEY`, so this app's `kustomization.yaml` deliberately does not
  include `../secret.yaml`.

## Gotchas inherited from the siblings

- The chart version is closed to `1.14.x` and the `volumeClaimTemplates`
  labels are frozen by a `postRenderer`. That field is immutable on a live
  StatefulSet; a chart bump that rewrites it makes Helm roll back forever and
  silently revert every other value in the release. The frozen values here
  match a fresh 1.14.0 install.
- `API_SERVER_KEY` is deliberately unset. The agent loads `/opt/data/.env` via
  python-dotenv, which *overrides* the container environment, so anything set
  here is ignored and callers get `401 gateway_auth_failed`.
- `--cookie-name` must stay unique per agent. The cookie domain is shared
  across `*.agents.kalitsune.net`, so a reused name means one agent's session
  cookie overwrites another's.
- Anything consumed through `envFrom` is injected **once** at container start.
  After changing such a Secret, bump `kalitsune.net/env-generation` in
  `helmrelease.yaml` or the process keeps the old value while Flux reports
  Ready.
