# Music Assistant

Music library manager and streaming server: connects streaming providers
(Spotify, Tidal, Deezer, …) plus local files to AirPlay / Chromecast / DLNA /
Sonos speakers. Web UI at <https://music.lab.kalitsune.net>.

## First boot — do this immediately

A fresh instance has **no users**, and in that state `/setup` is unauthenticated
and mints the first admin account. `*.lab.kalitsune.net` resolves publicly and
the `apps` Gateway answers on the WAN, so claim it as soon as the pod is Ready:

```
https://music.lab.kalitsune.net/setup
```

After that, `/setup` refuses and normal login applies. Users, roles
(admin/user/guest) and per-user provider access are managed inside MA, not in
this repo.

## Why there is no oauth2-proxy in front

Unlike the *arr stack, MA has its own authentication — bcrypt password hashing,
rate-limited login, token sessions, four roles. Adding Pocket ID in front would
mean two login prompts, and would break the Home Assistant integration and the
mobile/PWA clients: they authenticate with MA long-lived tokens and cannot
complete an OIDC browser redirect.

MA can also federate to a Home Assistant instance as an auth provider
(Settings → self-registration), which is the upstream path for multi-user
households.

## `hostNetwork: true` is not optional

MA discovers players over mDNS and SSDP — multicast, which does not cross the
pod network — and streams to devices that open **random** TCP/UDP ports back at
the server. A Service cannot express that, so upstream requires host networking
or macvlan. It binds more than the declared 8095/8097 (8927 sendspin, ephemeral
AirPlay ports); the declared `hostPort`s exist so the scheduler at least knows
8095/8097 are taken.

Practical effect: the pod binds `192.168.1.171` directly, MA auto-detects that
as its stream URL, and players on the flat `192.168.1.0/24` reach it without
going through the gateway. The gateway route only serves the web UI.

The pod is therefore pinned to whichever node is free of those ports — with the
control plane tainted, that is always the worker today.

## Storage

| Volume | Claim | Class | Why |
| --- | --- | --- | --- |
| `/data` | `music-assistant-data` | `local-path` | SQLite: library DB, **user accounts**, auth tokens, provider credentials |
| `/media` | `media-storage` (shared, read-only) | `truenas-nfs` | local music files |

`local-path` is a deliberate exception to the repo default: SQLite needs
`fcntl()` locking and WAL semantics NFS does not provide, and the failure mode
is silent corruption. No TrueNAS snapshot covers it — losing that volume loses
every account and provider login, and `/setup` is the only recovery.

`replicas: 1` + `strategy: Recreate` for the same reason (and because two pods
cannot both bind 8095).

There is no music directory on the share yet — `/medias` currently holds
`Movies`, `Shows`, `Anime`, `EPITA`. Create `Music/` and point MA's "Local
files" provider at `/media/Music`.

## Upgrades

The image is pinned `tag@sha256:…`; bump **both**. Never switch between the
`stable` and `beta` tags: upstream states the `/data` directory is not
compatible across channels and there is no way back.
