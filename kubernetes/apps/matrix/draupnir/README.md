# Draupnir — Matrix moderation bot

[Draupnir](https://github.com/the-draupnir-project/Draupnir) (the maintained
successor to Mjölnir) running in **bot mode** against continuwuity. It applies
bans and policy lists across rooms it protects, from commands typed in one
management room.

- Bot user: `@draupnir:kalitsune.net`
- Management room: `#draupnir:kalitsune.net` (encrypted, invite-only)
- Docs: <https://the-draupnir-project.github.io/draupnir-documentation/>

## Layout

| File | What it holds |
| --- | --- |
| `configmap.yaml` | the whole non-secret config, as `production.yaml` |
| `secret.yaml` | the bot's access token (SOPS-encrypted) |
| `pvc.yaml` | 1Gi `local-path` volume for the SQLite stores + crypto store |
| `deployment.yaml` | the bot pod |

No `httproute.yaml`, and no Service. The only listener is `/healthz` on 8080,
which the kubelet probes on the pod IP. Draupnir's `web` API (abuse-report
interception, synapse-http-antispam) is Synapse-specific and stays off.

## Finishing the deploy — a manual, two-sided step

A `git push` alone does **not** finish this. Three things live outside git:
the bot's account, its access token, and the management room. Until the token
is real the pod crashloops with

```
Failed to setup draupnir from the config /data/storage MatrixError: ...
GET /_matrix/client/v3/account/whoami: 401 Unauthorized -- {"errcode":"M_UNKNOWN_TOKEN"}
```

which is the expected placeholder state, not a misconfiguration.

Do these in order, from the `#admins` room on `kalitsune.net` and a Matrix
client signed in as `@maple:kalitsune.net`:

1. Create the account and mint a token:

   ```
   !admin users create draupnir <password>
   !admin users issue-token draupnir <password>
   ```

   Both take the password as a positional argument (`create` generates and
   prints one if omitted; `issue-token` *requires* it — it calls
   `check_password` before minting). Pick a long random one; it is only ever
   used for these two commands, since the bot authenticates with the token
   afterwards. Note these commands print the password and the token into the
   admin room.

   The bot must be a **local** account, not a Pocket ID user. This homeserver
   runs OIDC delegation, so `/_matrix/client/v3/login` returns
   `M_UNRECOGNIZED` and password login is impossible — but `issue-token`
   bypasses the login endpoint and mints a device directly, which is exactly
   why it works here and why the account must be local. Upstream's help text
   is explicit that it "will not work on shadow users, such as appservice
   puppets or accounts imported from an identity provider". Same constraint
   as the Hermes bots, see `../../hermes/hermes/README.md`.

   The token is also **clean** by construction — `issue-token` creates a
   brand new device that has never done E2EE, which is what
   `experimentalRustCrypto` (below) requires. A token lifted from an existing
   Element session would be rejected.

2. Put the token in `secret.yaml` and roll the pod:

   ```sh
   sops kubernetes/apps/matrix/draupnir/secret.yaml     # replace REPLACE_ME
   # then bump kalitsune.net/config-generation in deployment.yaml
   ```

   The bump is not optional: the token is a **mounted file**, read once at
   startup, so editing the Secret alone leaves the running process on the old
   value while everything reports `Ready`.

3. Create the management room, as `@maple`:

   - new room, **invite-only**, **encryption on**
   - publish the alias `#draupnir:kalitsune.net`
   - invite `@draupnir:kalitsune.net`
   - give it power level **50** once it joins

   Draupnir stores each protection's settings as state events in this room,
   and sending those needs PL 50. Without it, settings silently fail to save
   and `PolicyChangeNotification` creates a brand new notification room on
   every restart, littering the server with abandoned rooms.

   **Anyone in this room controls the bot.** Room membership is the entire
   access-control model; there is nothing finer.

4. Confirm: `!draupnir status` in the management room.

## Decisions worth knowing before you change something

### E2EE is on, against upstream's advice

Repo policy (`../README.md`) is that every Matrix service supports
encryption, so the management room is encrypted and the bot needs a
crypto-capable device — `experimentalRustCrypto: true`.

Upstream
[recommends against it](https://the-draupnir-project.github.io/draupnir-documentation/concepts/encryption):
their argument is that E2EE buys a moderation bot little, because Draupnir
does not enforce cross-signing and therefore cannot resist impersonation of
itself or of a moderator, and because command metadata is unencrypted anyway.
That is an argument about *usefulness*, not about whether it works — it runs,
and local policy wins. Recorded here so the upstream page alone does not
reopen the decision.

Two constraints ride along: `pantalaimon.use` must stay `false` (Draupnir
throws at startup if both are set), and the access token must be clean.

### E2EE needs a shim, and that shim is load-bearing

`shim-configmap.yaml` exists because Draupnir v3.1.0 cannot read its own
prompts in an encrypted room, which breaks every reaction-confirmed command
(`watch`, `ban`, `takedown`, …): you press OK and nothing happens, and the log
shows `TypeError: Something has changed upstream in vector bot sdk`. Upstream
[issue #1104](https://github.com/the-draupnir-project/Draupnir/issues/1104).

It is a type mismatch between two upstream projects, not a misconfiguration
here. `@vector-im/matrix-bot-sdk`'s `MatrixClient.getEvent()` returns a
`RoomEvent` wrapper for a plaintext room but a **bare** event object for an
encrypted one — the encrypted branch ends in `decryptRoomEvent(...).raw`,
which unwraps it. Draupnir's `extractRawRoomEvent()` requires the wrapper and
throws without it. So the failure fires *only* on the encrypted path, which is
why turning E2EE off "fixed" reactions and why that was the wrong fix.

The shim normalises that one method back to always returning a wrapper. It is
preloaded with `NODE_OPTIONS=--require`, before any Draupnir code runs, and
every caller of `extractRawRoomEvent` goes through `getEvent`, so one patch
covers the reaction handler, the report manager and the web API alike.

**`experimentalRustCrypto: true` and the `NODE_OPTIONS` env var move
together.** Removing the mount while leaving E2EE on returns the bot to
silently ignoring every prompt — the worst failure mode available, because it
looks alive. The shim ships a self-check for exactly that; run it against the
running pod:

```
kubectl exec -n matrix deploy/draupnir -- sh -c 'NODE_OPTIONS= node /shim/getEvent-shim.js'
```

It asserts both event shapes survive `extractRawRoomEvent`, *and* that the
unshimmed encrypted shape still throws. Note the `NODE_OPTIONS=` reset: the
pod already preloads this exact file, so without clearing it node finds the
module cached, never evaluates it as the main module, and the self-check
exits 0 having printed nothing — a pass and a no-op look identical. Three
`ok` lines is a pass.

When the last assertion starts failing, upstream has fixed the bug: delete
`shim-configmap.yaml`, the `NODE_OPTIONS` env var, the `/shim` mount and this
section.

### `managementRoom` is an alias, and Zero Touch is not used

v3.1.0 added "Zero Touch Deployment": set `initialManager` instead of
`managementRoom` and the bot creates its own management room and invites you.
Convenient — but the room it creates is **unencrypted**, and the two options
are mutually exclusive (setting both throws at config read). So the room is
created by hand and named by alias, which survives the room being recreated.

If the alias does not resolve, or the bot was never invited, Draupnir exits
non-zero at startup rather than entering safe mode: room resolution happens
before the safe-mode toggle exists. `CrashLoopBackOff` with
`Failed to load or create the Draupnir management room` in the log means
step 3 above is incomplete.

### It talks to the homeserver over the in-cluster Service

`homeserverUrl` is `http://continuwuity.matrix.svc.cluster.local:8008`, not
`https://matrix.kalitsune.net`. Every `/sync` would otherwise hairpin out
through Envoy and the WAN. `rawHomeserverUrl` is set to the same value; it is
only used for Synapse admin endpoints, which continuwuity does not implement.

### Abuse reports are off; antispam is available but deliberately not on

`pollReports` needs Synapse's `/_synapse/admin/v1/event_reports`, which
continuwuity does not implement, and the intercepting variant needs a reverse
proxy rewriting the client report endpoint. So reports land in the admin room
instead. `admin.enableMakeRoomAdminCommand` is left at its default `false` for
the same Synapse-only reason.

Draupnir's `web` API is a different story, and **not** a compatibility gap:
continuwuity v26.8.1 speaks Draupnir's antispam protocol natively
(`[global.antispam.draupnir]` with `base_url` + `secret`, implemented in
`src/service/antispam`), calling `user_may_invite` and `user_may_join_room` on
the bot. Enabling it would block spammers *at the join*, before they are ever
in a room, and is what room takedown policies require.

It stays off because the homeserver side is **fail-closed**: upstream's own
comment says "if an error is returned, the invite should be blocked — the
antispam service was unreachable, or refused". With Draupnir a single replica
on one node, every restart, image bump and crashloop would become a
server-wide outage of joins and invites. Turn it on once the bot has proven
stable here, and treat it as a change to *continuwuity's availability*, not
just to moderation. It needs `web.enabled: true`,
`web.synapseHTTPAntispam.{enabled,authorization}`, a Service, and the same
shared secret on continuwuity's side.

### Storage is `local-path`, not `truenas-nfs`

Everything on the volume is SQLite: the room-state backing store, the hash
store, two audit logs, and the rust crypto store. SQLite coordinates writers
with `fcntl()` locks and WAL, neither reliable over NFS — the failure mode is
a corrupted store noticed long after the fact. See the storage section of
`AGENT.md`.

Losing this volume costs the crypto device identity (the bot re-joins with a
new device; old encrypted history stays unreadable to it) and the room-state
cache (refetched). It does **not** cost policy data — bans and policy lists
are state events in Matrix rooms, not local files.

### `/healthz` is a real readiness signal

Draupnir answers `418` until it has authenticated, resolved the management
room and started monitoring, then `200`. `httpGet` treats `418` as a failure,
so the probe proves the bot is wired to Matrix rather than proving `node` is
running. That is also why the startup probe is generous: on a cold start it
waits for continuwuity, syncs, and verifies permissions in every protected
room.

### One replica, always

`strategy: Recreate`. The crypto store is a single Matrix **device**; two pods
would both claim that `device_id` and corrupt Olm sessions, on top of two
writers on the same SQLite files. A `RollingUpdate`'s default `maxSurge: 1`
creates exactly that overlap on every image bump.

## Using it

Commands go in the management room, prefixed `!draupnir` (`allowNoPrefix` is
`false`, so bare `!ban` does nothing — deliberate, since this bot shares rooms
with others).

```
!draupnir status
!draupnir rooms add #some-room:kalitsune.net    # start protecting a room
!draupnir list create my-bans my-bans-bl        # create a policy list
!draupnir ban @spammer:example.org my-bans Spam
!draupnir watch #community-moderation-effort-bl:neko.dev   # subscribe to a list
!draupnir protections
```

Draupnir needs to be **invited and given PL 50+** in every room it protects,
and it only accepts invites from members of the management room
(`autojoinOnlyIfManager: true`). `protectAllJoinedRooms` is `false`, so rooms
are protected deliberately rather than by being in them.

Moderator guide:
<https://the-draupnir-project.github.io/draupnir-documentation/moderator/setting-up-and-configuring>

## Upgrading

Bump the tag **and** the digest comment in `deployment.yaml` together. Check
the release notes for store migrations — the SQLite stores self-migrate on
first start (`Migrated database version from N to N+1`), which is one-way:
rolling back to an older image after a migration is not supported.
