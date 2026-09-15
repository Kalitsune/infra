# Hookshot — inbound webhook bridge for Matrix

[matrix-hookshot](https://github.com/matrix-org/matrix-hookshot) running as an
appservice against continuwuity. Only the **generic (inbound) webhook** service
is enabled: an external system `POST`s JSON to a secret URL and the payload
appears as a message in a Matrix room.

- Bot user: `@webhook:kalitsune.net`
- Webhook endpoint: `https://webhooks.lab.kalitsune.net/webhook/<hook-id>`
- Upstream docs: <https://matrix-org.github.io/matrix-hookshot/latest/>

GitHub, GitLab, Jira, Figma, feeds, OpenProject and outbound webhooks are all
off. Each is a separate config section; enabling one means adding its section
*and* reserving its ghost-user regex in the registration (below).

## Layout

| File | What it holds |
| --- | --- |
| `configmap.yaml` | the whole non-secret config, as `config.yml` |
| `secret.yaml` | `registration.yml` (appservice tokens) + `passkey.pem` (SOPS-encrypted) |
| `deployment.yaml` | the bridge pod, its Service, and both listeners |
| `httproute.yaml` | `webhooks.lab.kalitsune.net/webhook/` → `hookshot:9000` |

## The two ports are not interchangeable

| Port | Direction | Exposed? |
| --- | --- | --- |
| `9993` appservice | homeserver **→** bridge | **no**, cluster-internal only |
| `9000` webhooks | internet **→** bridge | yes, via the HTTPRoute |

Port 9993 is authenticated solely by the `hs_token` in the registration and
accepts arbitrary room events on the bridge's behalf. It must never gain a
route. The `url:` inside `registration.yml` therefore points at the in-cluster
Service (`http://hookshot.matrix.svc.cluster.local:9993`), which is the address
**continuwuity** dials — not an address any client uses.

## Registering the appservice — a manual, two-sided step

Continuwuity has no config file entry for appservices. The registration is
submitted at runtime, from the admin room, and is stored in its database. So a
`git push` alone does **not** finish this deploy.

The generated registration is dropped at
`/opt/data/export/hookshot-registration.yml`. In the `#admins` room on
`kalitsune.net`, send:

```
!admin appservices register
```
<paste the whole file contents in a code block>
```
```

Confirm with `!admin appservices list` — it should answer `hookshot`.

Then delete the drop-box copy: it contains both tokens in plaintext and
`/opt/data/export/` is a persistent NFS volume.

The tokens exist in exactly two places — the SOPS-encrypted `secret.yaml` and
continuwuity's database. **Rotating them means doing both**: `sops` the file,
bump `kalitsune.net/config-generation` in `deployment.yaml`, push, then
`!admin appservices unregister hookshot` and re-register the new file.

## Adding a webhook

1. Invite `@webhook:kalitsune.net` to the room.
2. Give it **Moderator** power — it must be able to send state events, which
   is how a connection is stored.
3. `!hookshot webhook <name>`
4. The bot replies with the full URL. That URL is a **bearer credential**:
   anyone holding it can post to the room as that hook.

Messages arrive from `@_webhook_<name>:kalitsune.net`, a ghost user reserved by
the registration's `@_webhook_.*` namespace. Changing `userIdPrefix` in the
ConfigMap without changing that regex breaks hook creation.

Payload handling: a `text` key becomes the message body (Markdown → HTML); a
`html` key overrides the formatting; anything else posts the raw payload. See
the [webhook docs](https://matrix-org.github.io/matrix-hookshot/latest/setup/webhooks.html).

## Decisions worth knowing before you change something

### `enableHttpGet` is false, and should stay that way

With `GET` enabled, **anything that generates a link preview fires the
webhook** — a Matrix client, a chat app, a crawler. Webhook URLs get pasted
into rooms; that is their whole purpose. `POST`/`PUT` only.

### `allowJsTransformationFunctions` is false

It lets a room moderator supply JavaScript that hookshot executes (in a
QuickJS sandbox) against every incoming payload. Genuinely useful for turning
a noisy payload into one line, but it is code execution exposed as a room
setting. Turn it on as a deliberate decision, not as a default.

### `permissions` is set explicitly, because the default is dangerous

If the `permissions` key is **absent**, hookshot grants every user on
`bridge.domain` full admin over the bridge, and only logs a warning. This
homeserver has open OIDC registration against Pocket ID, so "every user on
kalitsune.net" is not a closed set. The config names `@maple:kalitsune.net`
and nobody else.

### `lab.` hostname despite being internet-facing

`*.lab.kalitsune.net` is a CNAME to the single dynamic A record like every
other name here, so it resolves and is reachable publicly — the suffix marks
intent and picks up the existing wildcard certificate, it does not restrict
access. The route exposes exactly one path prefix (`/webhook/`), so the
hostname serves nothing but hook deliveries; the `/live` and `/ready`
endpoints on the same listener stay unroutable.

### No storage, no PVC

No `cache.redisUri`, so hookshot uses in-memory storage; connections live in
Matrix **room state**, not on disk, and survive restarts because the homeserver
holds them. The root filesystem is read-only as a result.

This is also why encryption is off: `encryption.storagePath` requires the Redis
cache (`BridgeConfigEncryption` throws otherwise). Hookshot cannot post to
encrypted rooms without it — if a webhook room needs E2EE, that means adding
Redis and a crypto-store volume, not just flipping a flag.

### One replica, `Recreate`

Two instances both receive pushed transactions and would double-post into
rooms. `RollingUpdate`'s `maxSurge: 1` creates exactly that overlap on every
image bump, so the strategy is `Recreate`.

## Verifying

```bash
kubectl -n matrix rollout status deploy/hookshot --timeout=5m

# /ready only returns 200 after the bridge has reached the homeserver
kubectl -n matrix port-forward svc/hookshot 9000:9000
curl -sS -w '\n%{http_code}\n' http://127.0.0.1:9000/ready

# an unknown hook id must 404, and GET must be refused
curl -sS -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'content-type: application/json' -d '{"text":"hi"}' \
  https://webhooks.lab.kalitsune.net/webhook/00000000-0000-4000-8000-000000000000
```

`404 {"ok":false,"error":"Webhook not found"}` from the public URL is the
correct healthy answer for a hook that does not exist — it proves routing,
TLS and the listener without needing a real hook.

## Troubleshooting

| Symptom | Look at |
| --- | --- |
| Pod never becomes ready, logs loop `Failed to connect to homeserver, retrying in 5s` | `bridge.url` unreachable, or continuwuity down |
| `M_UNKNOWN_TOKEN` / homeserver rejects transactions | the registration in continuwuity's DB and `secret.yaml` have diverged — re-register |
| `!hookshot webhook` says the bot cannot act | bot lacks power to send state events in that room |
| Hook URL 404s but the hook exists | `generic.urlPrefix` and the HTTPRoute path disagree |
| Webhook fires on its own when the link is posted | `enableHttpGet` got turned on |
| Nothing appears in an encrypted room | expected — encryption needs Redis, see above |
