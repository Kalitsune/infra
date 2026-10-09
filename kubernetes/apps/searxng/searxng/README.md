# SearXNG

Self-hosted metasearch at `https://search.kalitsune.net`. Aggregates upstream
engines (Google, DuckDuckGo, Brave, Wikipedia, …) and returns results without
tracking or profiling the user.

Two consumers, and they reach it by different paths:

| Consumer | Path | Why |
| --- | --- | --- |
| Humans | `https://search.kalitsune.net` via the `apps` Gateway | the requested hostname |
| Hermes agents | `http://searxng.searxng.svc.cluster.local:8080` | in-cluster, no DNS/TLS/gateway in the way |

The agents are configured with `web.search_backend: searxng` and
`SEARXNG_URL` in their own HelmReleases under `apps/hermes/`. That replaces
the keyless free-tier search rotation Hermes falls back to by default, which
is rate-limited and rotates through third parties.

## No state, no volume

There is no PVC and no database:

- `/etc/searxng/settings.yml` is a read-only generated ConfigMap.
- `/var/cache/searxng` (`__SEARXNG_DATA_PATH`) is an `emptyDir`. The
  entrypoint only requires the directory to exist.
- User preferences live in a client-side cookie, not server-side.

So nothing here needs `truenas-nfs` or `local-path`, a `Recreate` strategy, or
a backup story. A restart loses nothing.

## settings.yml is a patch, not a replacement

`use_default_settings: true` is load-bearing. Without it SearXNG expects the
file to define *everything*, including the whole `engines:` list, and starts
with no engines.

Anything the image can take from an environment variable is set in the
Deployment instead, because `searx/settings_defaults.py` gives those keys an
`environ_name` and the environment wins over the file:

| Setting | Env var | Set to |
| --- | --- | --- |
| `server.secret_key` | `SEARXNG_SECRET` | SOPS secret (never in the ConfigMap) |
| `server.base_url` | `SEARXNG_BASE_URL` | `https://search.kalitsune.net/` |
| `server.limiter` | `SEARXNG_LIMITER` | `false` — see below |

`search.formats` has **no** env override, which is the only reason the
ConfigMap exists at all.

## The one line that makes the agents work

```yaml
search:
  formats:
    - html
    - json
```

The shipped default is `formats: [html]`, and `searx/webapp.py` answers
`flask.abort(403)` for any other output format (webapp.py:626-631 at
`2026.10.7`). Hermes' SearXNG provider calls `/search?format=json`
(`plugins/web/searxng/provider.py`), so without `json` in that list every
agent search returns a bare 403 while the web UI looks perfectly healthy.

Removing `html` would be the inverse trap — the browser UI would 403.

## Rate limiting lives at the edge, not in SearXNG

`server.limiter: false` is deliberate. SearXNG's own bot limiter refuses to
arm without a Valkey connection (`searx/limiter.py`), so enabling it means
running a second Deployment for a single-household instance.

`ratelimit.yaml` does the equivalent in Envoy: a `BackendTrafficPolicy` with a
`Local` rate limit of 60 requests/minute, bucketed per client IP
(`sourceCIDR: {type: Distinct, value: 0.0.0.0/0}` — distinct means per-IP, not
one shared bucket).

The thing being prevented is not load on this pod. It is an open metasearch
instance being used as a free scraping proxy, where the cost lands on this
house's IP as captchas and blocks from the upstream engines.

`Local` is enforced per Envoy pod. The `apps` Gateway runs one replica today,
so per-pod == per-instance; scaling that Gateway out multiplies the effective
ceiling by the replica count.

## Not authenticated yet

The route is open. AGENT.md's rule is that anything with no authentication of
its own belongs on `lab.`, and this is on the apex because the request named
that hostname and the users are off the home network.

The intended end state is a gateway-native OIDC `SecurityPolicy` on the
HTTPRoute, exactly like `apps/hermes/dashboard/`. It is a **follow-up commit**
because it needs a Pocket ID client that only the Pocket ID UI can create:
`POCKET_ID_API_KEY` answers `401 {"code":"not_signed_in"}` on
`/api/oidc/clients`.

What the operator needs to create there:

- callback `https://search.kalitsune.net/oauth2/callback`
- `pkceEnabled: true` (Envoy 1.39+ always sends PKCE S256)

Then hand over the client ID and secret. Note that a `SecurityPolicy` gates
**every** path, which is fine here — the agents bypass the gateway entirely,
so gating the hostname does not touch them.

## Image pinning

SearXNG publishes no semver. Tags are `<date>-<commit>`, and `latest` is the
same digest as the newest dated tag. The manifest pins
`2026.10.7-6671d89be@sha256:cc026dbe…` (the multi-arch index). Bump tag and
digest together:

```bash
curl -sS "https://hub.docker.com/v2/repositories/searxng/searxng/tags?page_size=5" \
  | jq -r '.results[] | "\(.name)  \(.digest)"'
```

## uid 977

The image's `USER` is root; the entrypoint chowns the volumes and then runs
granian. It is run as uid/gid **977** here — the `searxng` account baked into
the image (`searxng:x:977:977` in `/etc/passwd`). As non-root the entrypoint
skips its `chown` and `update-ca-certificates` steps and prints one
`WARNING … is not owned by searxng:searxng` per volume. That is cosmetic: both
mounts are a read-only ConfigMap and an `emptyDir` that the kubelet already
sets to gid 977 via `fsGroup`.

## Verify

```bash
# in-cluster, the path the agents use
kubectl -n searxng port-forward svc/searxng 8080:8080
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/healthz
curl -sS 'http://127.0.0.1:8080/search?q=test&format=json' | jq '.results | length'

# through the gateway
curl -sSI https://search.kalitsune.net/
```

A `200` on `/healthz`, a non-zero result count on the JSON search, and a `200`
on the public hostname. If the JSON call returns `403`, `search.formats` lost
`json`.
