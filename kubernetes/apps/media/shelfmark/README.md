# Shelfmark

Book search and download front-end (ex `calibre-web-automated-book-downloader`).
Finds a book on Anna's Archive, downloads the file, writes it into the `Books`
subtree of `media-storage` — the same directory Kavita scans at
`read.kalitsune.net`, so a finished download is a library item with no copy step.

- URL: `https://download-stack.lab.kalitsune.net/shelfmark/`
- Image: `ghcr.io/calibrain/shelfmark` (the full image, not `-lite`)

## Why the full image

Anna's Archive sits behind a Cloudflare/DDoS-Guard challenge. The standard image
ships a Chromium and solves it in-process; `shelfmark-lite` has no browser and
needs `EXT_BYPASSER_URL` pointed at a FlareSolverr. There *is* a flaresolverr
container in the cluster, but it lives inside the `download-stack` pod sharing
tailscale's network namespace and has no Service port — giving it one widens that
pod's surface for no gain here.

That browser is also the only reason this Deployment has a memory limit. Starved
of RAM, Chromium does not error: it fails to launch and every download ends as
`403 detected; switching to bypasser` followed by `No download URL found`.
Upstream calls 2 GiB the safe floor, hence `limits.memory: 3Gi`. If downloads
start failing that way, check the pod's memory before suspecting DNS or the WAN.

## Free tier — expect slow downloads

No `AA_DONATOR_KEY` is set, deliberately. Without one Shelfmark uses Anna's
Archive's throttled *slow* download links, which is what the bundled browser is
for. The members-only `/dyn/api/fast_download.json` endpoint — what
LazyLibrarian's `annas.py` and every `annas-*` MCP server call — is never
touched, so a paid donation account is not required.

Consequence: a download can sit in the queue for minutes, and AA enforces a
per-day cap on the free path. `MAX_CONCURRENT_DOWNLOADS` defaults to 3; raising
it does not raise the cap.

## Authentication

`AUTH_METHOD=none` in the container. Every request is already authenticated by
the Pocket ID `SecurityPolicy` on the gateway, which is why this app has no
`httproute.yaml` of its own: a `SecurityPolicy` targets an `HTTPRoute` *by name*,
so a separate hostname would need a second OIDC client and a second callback
registered in Pocket ID. Instead `/shelfmark` is a rule on
`../download-stack-dashboard/httproute.yaml` and inherits that gate.

**Do not give this app its own route or expose the Service without carrying that
`SecurityPolicy` across.** With `AUTH_METHOD=none` and no proxy in front, the
admin UI — including the download queue and every setting — is open to anyone who
reaches the port.

`URL_BASE=/shelfmark/` is what makes the subpath work; Shelfmark builds its own
links from it, so there is no `URLRewrite` filter on that route rule (unlike
`/torrent`). Dropping `URL_BASE` breaks every asset URL.

## Egress

Through the pod's own network, **not** the Mullvad tunnel. Only the
`download-stack` pod routes through tailscale, and joining this one to it would
mean merging it into that StatefulSet. If AA traffic must leave over Mullvad,
that is a tailscale sidecar here — `NET_ADMIN`, `TS_DEBUG_FIREWALL_MODE=nftables`
and the same `postStart` exit-node dance as `download-stack/app.yaml`.

## Storage

| Path             | Claim                      | Class        |
| ---------------- | -------------------------- | ------------ |
| `/config`        | `shelfmark-config`         | `local-path` |
| `/books`         | `media-storage` (`Books`)  | `truenas-nfs`|
| `/tmp/shelfmark` | `emptyDir`, 20 GiB cap     | node         |

`/config` is SQLite (users, settings, request queue, cover cache), so it is
`local-path` and the Deployment is `strategy: Recreate` — see AGENT.md. Back it
up at the app layer; there are no TrueNAS snapshots on that class. Staging lives
on an `emptyDir` so a large audiobook cannot fill the node's ephemeral storage,
and so Kavita never scans a half-written file.

## First run

The onboarding wizard asks for sources and destinations. The env vars already
set `DIRECT_DOWNLOAD_ENABLED`, the AA mirrors and `/books`; the wizard mostly
confirms them. `annas-archive.is` is dead as a source (upstream README, checked
Aug 2026) — `annas-archive.org` and `annas-archive.gl` are the live ones.

Other free sources worth enabling in Settings, none of which need the browser:

- **Prowlarr** — `http://download-stack.media.svc.cluster.local:9696`, API key
  from Prowlarr's Settings → General. Reaches book indexers the existing stack
  already has configured.
- **IRC** — `irc.irchighway.net`, channel `#ebooks`. Leave the separate
  audiobook channel blank; the same channel serves both.
- **Libgen** — `LIBGEN_SEARCH_ENABLED`, covers CBZ/CBR comics AA does not index.
