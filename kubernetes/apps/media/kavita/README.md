# Kavita

Reading server for comics, manga and ebooks on `kavita.kalitsune.net`.
Login is Pocket ID (client `kavita`), with Kavita's own accounts still behind it.

## Storage

| Volume              | Class        | Why                                            |
| ------------------- | ------------ | ---------------------------------------------- |
| `kavita-config`     | `local-path` | SQLite (`/config/kavita.db`) — NFS corrupts it |
| `media-storage`     | `truenas-nfs`| shared library, mounted `subPath: Books`        |

`local-path` is node-local and has no TrueNAS snapshots: back up
`/config/kavita.db` at the app layer, and do not "fix" the class to the repo
default. `strategy: Recreate` for the same reason — `RollingUpdate`'s `maxSurge`
would briefly give two pods the same SQLite file.

The library mount is only the `Books` subtree, so the scanner never walks the
film and series directories. kubelet created `/medias/Books` empty on first
mount; put books there.

## OIDC

Kavita reads the OIDC **Authority, ClientId and Secret from
`/config/appsettings.json` only**, at process start. There is no environment
variable for them (`Kavita.Common/Configuration.cs`), and `Enabled` is derived —
`Authority` non-empty is what switches the whole scheme on
(`IdentityServiceExtensions.cs:79`). Everything else (provisioning, role sync,
auto-login) lives in the database and is editable in the admin UI.

So the `oidc-config` init container patches just the `OpenIdConnectSettings`
object in that file on every pod start, with `jq` from the app's own image, and
leaves the rest alone. **Do not seed the whole file**: it also holds `TokenKey`,
which Kavita generates on first run and which invalidates every existing session
if it changes.

On boot, `Seed.SetOidcSettingsFromDisk` copies those three values from the file
into the `ServerSetting` row, so the admin UI shows them as configured. Changing
the Authority there clears every stored external id by design.

Redirect URIs, fixed by Kavita and registered on the Pocket ID client:

| Purpose      | Path                      |
| ------------ | ------------------------- |
| callback     | `/signin-oidc`            |
| post-logout  | `/signout-callback-oidc`  |

The client is group-restricted to `app-media-admin` and `app-media`, matching
jellyfin's. PKCE is on; Kavita uses the authorization-code flow with a client
secret, which Pocket ID accepts alongside PKCE.

### What is NOT enabled

`ProvisionAccounts` defaults to `false`, so an OIDC login only succeeds for a
user whose **email already matches an existing Kavita account**
(`OidcService.LoginOrCreate`). That is deliberate for the first login: the
account created by `POST /api/account/register` is the only one that gets the
`Admin` role, and it must exist before anything is delegated to the IdP.

After the admin account exists, turn on *Provision accounts* (and optionally
*Sync user settings with OIDC roles*) in the admin UI. With sync enabled, roles
must arrive under the configured claim — Pocket ID sends groups in `groups`, so
set **Roles claim** to `groups` and grant at least the `Login` role, or nobody
can sign in. Note Kavita's default roles claim is the long
`http://schemas.microsoft.com/...` URI, not `roles`.

## First login

`GET /api/admin/exists` returns `false` until the first account is registered.
Register it through the UI (it becomes `Admin` + `Login`), then link it to
Pocket ID by logging in once via SSO with the same email.
