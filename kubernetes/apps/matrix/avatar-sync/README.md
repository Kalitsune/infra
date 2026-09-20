# avatar-sync

Copies each SSO user's Pocket ID profile picture into their Matrix profile,
hourly.

## Why a CronJob and not a login hook

The obvious design — "when a user logs in through MAS, push their IdP avatar to
Synapse" — cannot be built. MAS has no hook surface at all: no webhooks, no
notification sink, no plugin API. The only outbound integration in its config
crate is Sentry.

The avatar plumbing exists on both sides and is simply never wired:

- MAS's `ProvisionRequest` carries `set_avatar_url` / `unset_avatar_url`.
- Synapse's `POST /_synapse/mas/provision_user` accepts those fields.
- `ProvisionUserJob::run()` (`crates/tasks/src/matrix.rs`) only ever calls
  `set_displayname`.

And that job is scheduled on **register / admin edit / email change**, never on
an ordinary login — so even patching MAS to pass the avatar through would buy
registration-time sync only, not "update on login".

MAS already stores the IdP ↔ Matrix mapping, so reconciling from its database
needs neither a hook nor an OIDC round-trip:

```sql
SELECT u.username, l.subject FROM upstream_oauth_links l
  JOIN users u ON u.user_id = l.user_id;
```

`l.subject` is the Pocket ID user UUID.

## Bots are excluded structurally

Every bot here (`draupnir`, `hermes`, `hermes-homelab-expert`, `avatar-sync`
itself, and the bridge ghosts) was created with `mas-cli manage register-user`
and has **no `upstream_oauth_links` row**. The join above therefore cannot
return one. There is no name-based skip list to keep in sync, and none should
be added — the join *is* the safety property. Listing users from Synapse
instead and filtering by name would reintroduce exactly the bug this avoids.

## How it works

1. Query MAS for live OIDC-linked accounts (not deactivated, not locked).
2. `GET {pocket-id}/api/users/{uuid}/profile-picture.png`. That route has no
   auth middleware in Pocket ID, so no IdP API key is involved.
3. Download the user's current avatar via authenticated media and compare
   **bytes**, not timestamps: Pocket ID synthesises an initials PNG for users
   who never uploaded one, and it is byte-stable once cached, so hashing is
   what stops an hourly re-upload of the same image.
4. On a difference: `POST /_matrix/media/v3/upload`, then
   `PUT /_matrix/client/v3/profile/{user}/avatar_url` with the returned
   `mxc://`. Synapse stores `avatar_url` verbatim — an `https://` URL is
   accepted and rendered by no client, so the upload step is mandatory.

A failure on one user is logged and the rest continue; the job exits non-zero
if any user failed.

## The notice

After a successful change the user gets an `m.notice` in their Server Notices
room via `POST /_synapse/admin/v1/send_server_notice`. Only on an actual
change — the steady-state hourly run sends nothing.

Server notices are enabled in `apps/matrix/synapse/helmrelease.yaml`
(`synapse.additional.0-server-notices`); without that config key the endpoint
answers `400 "Server notices are not enabled on this server"`. The sender is
`@notices:kalitsune.net`, which is **not a registered account and must never
become one** — Synapse puppets it internally with `create_requester()` and
`check_user_id_not_appservice_exclusive()` actively refuses to register that
localpart. It has no MAS row, no password and no token, so avatar-sync's own
bot-exclusion join cannot see it either.

The notice is best-effort: `notify()` swallows its own exceptions, because the
avatar is already written by the time it runs and a failed message must not
make the job retry the upload. A failure prints `notice failed (avatar IS
set)` to stderr and does not count as a failed user.

`m.notice` rather than `m.text` is deliberate — clients render it muted and
bots are required to ignore it, so a notice cannot start a reply loop with a
bridge or with Draupnir. The room is created read-only (Synapse sets
`users_default: -10`), so replies are not possible anyway.

## The token

`SYNAPSE_ADMIN_TOKEN` is a MAS compatibility token for
`@avatar-sync:kalitsune.net` carrying `urn:synapse:admin:*`. Cross-user profile
writes are admin-gated, which is the single capability it is for.

`@avatar-sync:kalitsune.net` shows **`admin: false`** in Element Admin and that
is correct — under MAS, `is_server_admin()` is literally
`"urn:synapse:admin:*" in requester.scope` (`synapse/api/auth/mas.py`) and never
reads the `users.admin` column. Setting `admin = 1` would grant nothing and
only widen the blast radius in a second place. Verify the grant by exercising
an endpoint instead:

```sh
# 200 => the scope is present. Do NOT use GET /_synapse/admin/v2/users for
# this: it returns 400 M_INVALID_PARAM ("guests parameter is not supported
# when delegating to MAS") even for a perfectly-scoped token.
curl -so /dev/null -w '%{http_code}\n' -K /tmp/curlrc \
  http://synapse-synapse.matrix.svc:8008/_synapse/admin/v2/users/@maple:kalitsune.net
```

Rotation is not a git-only operation — see the note in `kustomization.yaml`.

## Runtime

The job runs the stock `oci.element.io/synapse` image as a plain Python 3.13
runtime: it already ships `requests` and `psycopg2`, so there is no image to
build, host or patch for ~150 lines of glue, and it is already pulled on the
node. Bump the tag alongside the chart's Synapse version, or don't — nothing
here depends on Synapse internals.

## Checking a run

```sh
kubectl -n matrix get cronjob avatar-sync
kubectl -n matrix logs -l job-name=$(kubectl -n matrix get jobs \
  -l app.kubernetes.io/name=avatar-sync -o name | tail -1 | cut -d/ -f2)
```

Expected steady state is one `up to date` line per linked user. To preview
without writing, set `DRY_RUN=1` in the env block.
