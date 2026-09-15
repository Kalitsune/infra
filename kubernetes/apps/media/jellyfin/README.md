# Jellyfin

Media server on `watch.kalitsune.net`. Login is Pocket ID via the SSO plugin
(no oauth2-proxy in front — it was removed in 938a691 because the plugin does
the OIDC flow natively and a proxy in front breaks native clients).

## Abyss theme

[Abyss](https://github.com/AumGupta/abyss-jellyfin) v1.2.3, applied without
touching the image.

Upstream's installer edits `jellyfin-web` in place: it writes files into
`<web>/ui/`, patches a `<script>` tag into `index.html`, and puts an
`@import` of jsDelivr into Custom CSS. All three are lost on the next image
pull, which is why upstream's own Docker guidance is "re-run the installer on
every container start".

Here the same result is assembled declaratively:

| Upstream installer step | What this repo does instead |
| --- | --- |
| copy `spotlight.*` into `<web>/ui/` | `jellyfin-abyss-web` ConfigMap, copied by an init container into an emptyDir mounted at `/jellyfin/jellyfin-web/ui` |
| patch `<script>` into `index.html` | [File Transformation](https://github.com/IAmParadox27/jellyfin-plugin-file-transformation) rewrites the response |
| `@import` jsDelivr into Custom CSS | the same transformation injects `<link href="ui/abyss.css">` |

Nothing on disk is modified, so an image bump cannot undo it.

### Two traps, both of which fail silently

Worth knowing before changing any of this, because neither produces an error
at any layer you would normally check — Flux `Ready`, pod `Ready`, init exit 0:

**`--` is illegal inside an XML comment.** Jellyfin's `BasePlugin`
deserialises plugin config in its constructor and, on a parse failure, writes
*defaults* back over the file. A comment containing `--` therefore replaced
the seeded transformation with `<Transformations />` at plugin load, and the
init container's own copy was long gone by the time anything could inspect it.
The tell is the config file's mtime matching the "Loaded plugin" log line.
Validate before committing:

```bash
python3 -c "import xml.etree.ElementTree as E; E.parse('abyss/Jellyfin.Plugin.FileTransformation.xml')"
```

**A ConfigMap cannot be mounted straight onto the web root.** Every key in a
ConfigMap volume is a symlink into `..data/`, and .NET's `FileInfo.Length`
returns the length of the *link path* for a symlink. Kestrel sets
`Content-Length` from that, so `abyss.css` came back `200 text/css` with
exactly 16 bytes — `strlen("..data/abyss.css")` — and the browser saw a
truncated stylesheet rather than an error. The init container `cp -L`s into an
emptyDir to dereference. Check with the size, not the status code:

```bash
kubectl -n media exec deploy/jellyfin -c jellyfin -- \
  curl -sS -o /dev/null -w '%{http_code} %{size_download}\n' http://127.0.0.1:8096/web/ui/abyss.css
```

### How the injection works

File Transformation 3.0.0.0 installs ASP.NET Core middleware that intercepts
`/web/*` responses and runs registered search/replace transformations over the
body before it reaches the client. It is already installed (plugin catalogue:
`https://www.iamparadox.dev/jellyfin/plugins/manifest.json`) and Moonfin
already uses it.

Its transformation list lives in
`/config/plugins/configurations/Jellyfin.Plugin.FileTransformation.xml` on the
PVC. The `abyss-theme` init container copies this repo's copy of that file over
it on every boot, so the config is git-owned. The plugin reads it in its own
constructor, which is why this is an init container and not a Job — the file
must exist before Jellyfin loads plugins.

**Editing the transformation in the plugin's dashboard page will be reverted on
the next pod restart.** Edit `abyss/Jellyfin.Plugin.FileTransformation.xml`.

The transformation appends to `</body>`, so Abyss' stylesheet lands after
jellyfin-web's own and wins cascade ties. It matches the file on disk, never
its own output, so it cannot double-inject. Moonfin registers a separate
`index.html` transformation that injects at `</head>`; both run, in one
pipeline, without overlapping.

Custom CSS (`Dashboard > Branding`) is deliberately **not** used: `branding.xml`
also holds `<LoginDisclaimer>`, which the SSO plugin rewrites to place the
Pocket ID login button. Seeding that file would fight the plugin for ownership
and drop the button.

### Updating Abyss

```bash
V=v1.2.4   # check https://github.com/AumGupta/abyss-jellyfin/releases/latest
cd kubernetes/apps/media/jellyfin/abyss
curl -fsSL -o abyss.css           "https://raw.githubusercontent.com/AumGupta/abyss-jellyfin/$V/abyss.css"
for f in spotlight.html spotlight.css spotlight-loader.js; do
  curl -fsSL -o "$f" "https://raw.githubusercontent.com/AumGupta/abyss-jellyfin/$V/scripts/spotlight/$f"
done
```

Then set `ABYSS_SPOTLIGHT_VERSION` in `spotlight.html` to the release number
without the `v`. Upstream tags the release *before* bumping that constant, so
the file at tag `vX.Y.Z` still says the previous version and the in-app update
toast nags every admin forever if you leave it.

Both ConfigMaps are `configMapGenerator`-built, so their names carry a content
hash — changing any file renames the ConfigMap, which changes the pod template
and rolls the Deployment. There is no checksum annotation to remember.

### What still leaves the cluster

`abyss.css` `@import`s Google Fonts and a material-icons `woff2` from jsDelivr,
and `spotlight.html` polls the GitHub releases API for update notices (admin
users only, at most every 6h). Without egress the theme falls back to system
fonts; it does not break.

### Not installed

The theme selector locks to Dark after upstream's installer runs, and its home
section ordering (Continue Watching → Next Up → My Media → Recently Added) is
a per-user display preference. Neither is applied here — both are per-user
state in Jellyfin's database, not server config, so set them once in
`Settings > Display` and `Settings > Home`. Abyss needs the Dark base theme to
render correctly.

## Storage

`jellyfin-config` is `local-path`, not `truenas-nfs`: Jellyfin keeps its
library in SQLite, which needs `fcntl()` locking that NFS does not provide
reliably. `media-storage` (the library itself) is NFS and shared with the
download stack.
