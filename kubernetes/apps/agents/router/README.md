# router — LiteLLM proxy for the agents

One OpenAI-compatible front door that every agent and Open WebUI calls, in
front of 9Router.

| | |
| --- | --- |
| Public URL | `https://router.agents.kalitsune.net` (master key required) |
| Service | `router` in namespace `agents` |
| Cluster DNS | `router.agents.svc.cluster.local` |
| Port | `4000` |
| OpenAI-compatible API | `http://router.agents.svc.cluster.local:4000/v1` |
| Health | `/health/liveliness`, `/health/readiness` → `200`, unauthenticated |
| Credential | `router-secrets` → `LITELLM_MASTER_KEY` (SOPS, this directory) |
| Upstream | 9Router, with its own minted key `ROUTER_9ROUTER_KEY` |

## It is LiteLLM, not LightLLM

The request named "lightllm". `ModelTC/LightLLM` is a **GPU inference engine**
— it loads model weights and serves them with CUDA kernels. Every node here
reports `nvidia.com/gpu: none`:

```
paragonite            gpu=none  cpu=16  mem=32711636Ki
talos-192-168-1-170   gpu=none  cpu=2   mem=3987652Ki
talos-192-168-1-171   gpu=none  cpu=8   mem=32843972Ki
```

so it cannot run on this cluster at all, and would need local weights besides.
LiteLLM is a routing proxy, needs no GPU, and is what the operator asked for
in earlier sessions ("I'd like litellm because it's fast"). That is what this
directory deploys. If the GPU engine was genuinely meant, this is the wrong
app and the answer is a machine with a card in it.

## Why it does not replace 9Router

9Router's database holds three **OAuth subscription** connections — Claude,
Antigravity, Codex — and **zero API keys**. LiteLLM cannot hold those:

- `codex` has a native `chatgpt/` provider, but the OAuth device-code login
  needs a human at a browser, and the token file would have to be mounted
  writable for refresh.
- The Claude subscription is not supported as a stored provider at all.
  LiteLLM can only *forward* a client-supplied `sk-ant-oat01-*` header; that
  expires in ~8h and nothing in this cluster holds or refreshes one.

So 9Router keeps doing credential custody and refresh, and LiteLLM owns the
agent-facing surface: retries, one model namespace, one place to add a second
upstream later. Replacing 9Router outright means re-authorising all three
subscriptions somewhere else, which is a separate decision.

It also will not make anything faster on its own. The measured cause of slow
agent turns was Claude subscription **rate limiting** (84% of `high-effort`
requests paid a doomed `cc/claude-opus-5` attempt before falling back to
Gemini flash), which is upstream of both proxies.

## No database, deliberately

`DATABASE_URL` is unset, so the proxy is config-only and stateless: no PVC,
nothing to back up, rescheduleable anywhere. Consequences, all verified
against the rendered config:

- `general_settings.master_key` is the **only** accepted credential.
- `/key/generate` → `401`. Per-caller virtual keys need Postgres.
- The admin UI cannot be logged into (`POST /login` → `401`).

Adding per-agent virtual keys (so one agent can be revoked or budgeted
without rotating everyone) means adding Postgres on `local-path` per
`AGENT.md`. That is the natural next step, not a missing piece of this one.

## The model list is explicit, and must stay that way

`configmap.yaml` names every model as its own `model_list` entry. Provider
wildcards (`model_name: "cc/*"` → `openai/cc/*`) **route** correctly but
wreck `/v1/models`: LiteLLM answers that from its own static OpenAI catalogue
grafted onto the prefix, so the list came back with **615** entries including
`cc/gpt-image-1` and `cx/sora-2-pro` — models 9Router does not have, which
fail when called. `litellm_settings.public_model_groups` does **not** filter
it (measured: still 615). With explicit entries the list is exactly the 20
configured ids and nothing else, which is what makes the Open WebUI picker
usable.

To add a model:

1. Check it exists upstream, and that it actually answers:

   ```bash
   kubectl -n agents exec hale-0 -c hermes-agent -- sh -c \
     'curl -sS -H "Authorization: Bearer $HALE_9ROUTER_KEY" \
      http://9router.9router.svc.cluster.local:20128/v1/models' | jq -r '.data[].id'
   ```

2. Add the entry to `configmap.yaml`.
3. **Bump `kalitsune.net/config-generation` in `deployment.yaml`** in the same
   commit. A ConfigMap edit alone does not restart the pod — litellm reads
   `config.yaml` once at startup, so the new model would silently never appear
   while everything reported `Ready`.

Two models are deliberately **absent** because 9Router advertises them but
they fail on call:

- `cx/gpt-6-astra[1m]` → `[codex/gpt-6-astra] [400] The 'gpt-6-astra' mode…`

And one is present but quota-limited upstream: `cx/gpt-5.5` returned
`[429] The usage limit has been reached` during verification. That is the
ChatGPT subscription's own limit, not a routing fault.

## Exposure

The hostname is on the same public `apps` Gateway listener as every other host
here; `*.agents.kalitsune.net` is already in `apps-tls` and already CNAMEs to
the dynamic anchor, so no DNS or certificate change shipped with this.

There is no oauth2-proxy in front, unlike 9Router. The reasoning is in
`httproute.yaml`: this release serves only an API, every `/v1` and management
path requires the master key, and a browser OIDC flow would break every
caller. `/ui/` (static assets) and `/openapi.json` remain unauthenticated and
are accepted as harmless.
