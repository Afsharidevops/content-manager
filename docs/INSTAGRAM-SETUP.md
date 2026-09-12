# Instagram/Meta setup guide

Instagram publishing uses the official **Meta Graph API** (no unofficial
cookie automation). It needs a **Professional** Instagram account (Business or
Creator), a Meta developer app, and a long-lived access token. The token can
come from the **Instagram API with Instagram Login** path (no Facebook Page
required) or from Facebook Login, in which case the account must also be
linked to a Facebook Page.

If the Meta developer console cannot be reached from your network, see
[Meta developer console access](#meta-developer-console-access) for the free
Cloudflare WARP workaround used to create the app and the token.

## Current status

`content-bot` ships an Instagram publisher (`content_bot/instagram.py`) that
publishes approved drafts to the configured Instagram account through the
Graph API:

- a single attached photo publishes as a photo,
- several attached photos publish as a **carousel** (2-10 photos),
- an attached MP4/MOV video publishes as a feed video,
- text-only posts cannot be published to Instagram,
- captions stay readable in Persian: the caption head carries an
  invisible right-to-left mark so the inline account name cannot flip
  the first paragraph to LTR.

The Telegram approval flow offers two extra buttons whenever the draft has
media, Instagram is configured, and automatic publishing is enabled
(`INSTAGRAM_AUTO_PUBLISH=true`, the default):

- **Approve to Instagram** - publishes to Instagram only,
- **Approve to Telegram + Instagram** - publishes to the Telegram channel
  first, then to Instagram.

Media Studio generated images and operator-uploaded photos both work; the
brand chip is applied before any upload. Albums are also sent to the Telegram
channel as a media group.

## Manual mode (post package)

Set `INSTAGRAM_AUTO_PUBLISH=false` to keep the two Instagram buttons out of the
Telegram keyboards and the operator console while the credentials stay in
`/.env`. Nothing else changes: the caption builder, the media host, and the
token refresh keep working, so flipping the switch back to `true` restores the
API buttons.

Manual mode exists for accounts that cannot publish through the Graph API
(Meta review, a disabled account, a revoked permission) but can still post from
the Instagram app. The operator console then shows a **Post package** action
for every publishable draft: the bot re-sends the stored media file as an
untouched Telegram document and follows it with the exact caption inside a
code block. Telegram renders that block with a **copy** button, so the
operator copies the caption, saves the file, and posts both from the Instagram
app. Text-only drafts get the caption package alone.

The same package is useful with a working API: use it when the post should go
out by hand, for example with a first comment or a collaboration tag that the
Graph API cannot set.

## Media URLs

The Graph API cannot accept local files; every photo or video must be
reachable at a **public HTTPS URL**. The bot stores files under
`data/content-bot/media/` and maps them to the media base URL plus the path
under the bot data directory, so the effective URL is `<base>/media/<file>`.

Examples:

```text
https://media.example.com/bot       -> https://media.example.com/bot/media/<file>
https://cdn.example.com/content-bot -> https://cdn.example.com/content-bot/media/<file>
```

Serve only the `media/` subtree and keep the rest of the bot data directory
private. The URL must be directly fetchable by Meta, without login or bot
protection.

`./install.sh` asks once for the zone it should suggest host names from and
stores it as `STACK_BASE_DOMAIN` (for example `stack.example.com`, which names
this host `media.stack.example.com`). The value only drives suggestions;
`INSTAGRAM_MEDIA_PUBLIC_BASE_URL` stays authoritative.

### The bundled media host (compose profile "ig-media")

The stack can publish the media directory itself. `./manage.sh
instagram-media-enable` starts two containers:

- `ig-media` - nginx serving `data/content-bot/media` read-only on
  `127.0.0.1:8099` (`IG_MEDIA_BIND_IP` / `IG_MEDIA_PORT`),
- `ig-media-tunnel` - a cloudflared **quick tunnel** that gives that server a
  public HTTPS address and writes the hostname it received to
  `data/content-bot/tunnel/trycloudflared.log`.

Because a quick tunnel hostname changes on every restart, the bot does not
trust a hostname copied into `.env`. It resolves the public base URL in this
order, using the first usable value:

1. `data/content-bot/media-base-url.txt` - a hostname you pinned explicitly
   (one `https://...` line, `#` starts a comment);
2. `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` - used only when it is **not** a
   `*.trycloudflare.com` hostname, so a real domain always wins;
3. the most recent hostname in `data/content-bot/tunnel/trycloudflared.log`.

A tunnel restart therefore never leaves a dead URL behind: the bot reads the
new hostname on its next publish and rebuilds the Graph client against it.
`/instagram` in Telegram and `./manage.sh instagram-media-status` both print
the URL currently in effect.

Commands:

```bash
./manage.sh instagram-media-enable              # nginx plus the public tunnel
./manage.sh instagram-media-enable --quick      # force the quick tunnel
./manage.sh instagram-media-enable --named      # force the named tunnel (needs a token)
./manage.sh instagram-media-enable --nginx-only # nginx only, for an external proxy
./manage.sh instagram-media-status              # show profile state and public URL
./manage.sh instagram-media-tunnel-off          # stop only the tunnel, keep nginx
./manage.sh instagram-media-disable             # stop the host and disable the profile
./manage.sh instagram-media-verify              # can Meta download the file?
```

### A permanent address

A quick tunnel is fine for testing, but its hostname changes on every restart.
The stack can keep one permanent HTTPS address instead. Whichever route you
take, the value that ends up in the bot is the same `<base>` that precedes
`/media/<file>`.

**Route A - named Cloudflare tunnel (no server, no open ports).** Cloudflare
only serves a hostname it manages DNS for, so either move the zone to
Cloudflare or delegate just the media subdomain:

1. In ArvanCloud (or wherever `example.com` resolves) add NS records for the
   subdomain, for example `media` -> the two nameservers Cloudflare assigns
   when you add `media.example.com` as its own zone. The rest of `example.com`
   stays where it is.
2. In the Cloudflare dashboard create a tunnel (Zero Trust -> Networks ->
   Tunnels), copy its token, and add a public hostname `media.example.com`
   whose service is `http://ig-media:80`.
3. In `.env` of this stack set:

```text
IG_MEDIA_TUNNEL_TOKEN=<token from the dashboard>
INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.example.com
```

4. Run `./manage.sh instagram-media-enable`. It enables the
   `ig-media-named` profile (nginx plus the named tunnel) instead of the quick
   tunnel, and the hostname never changes again. Check with
   `./manage.sh instagram-media-status`.
5. Test: `curl -I https://media.example.com/media/<file>` must answer `200`.

**Route B - reverse proxy that can reach the stack over the LAN.** This is
the normal shape when the stack runs on a machine in your own network and the
public address lives on the router (a router with a static IP and its own
proxy container). Nothing tunnels and no port is opened on the stack host:

1. DNS: point `media.example.com` (A record) at the router's public IP. With
   ArvanCloud, start with the proxy toggle **off** so the router's proxy can
   obtain its own certificate; turn the CDN on only if you want ArvanCloud to
   terminate TLS instead.
2. On the stack machine, bind the media host to the LAN address. The installer
   asks *"Will a reverse proxy on another host publish the media directory?"*
   and offers the detected LAN address as the default; by hand, set the value
   and start the host without a tunnel:

```text
IG_MEDIA_BIND_IP=192.168.1.50   # this host's LAN address
IG_MEDIA_PORT=8099
```

```bash
./manage.sh instagram-media-enable --nginx-only
```

3. On the router, add the site to the proxy configuration and point it at the
   stack machine:

```caddyfile
media.example.com {
	encode zstd gzip
	reverse_proxy 192.168.1.50:8099
}
```

   Only `/media/...` is served; nginx answers `404` for `/` and for directory
   listings, so the rest of the bot data directory stays private. If you bind
   beyond loopback, allow only the router (or your proxy host) on that port.
4. In `.env` set `INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.example.com`
   (or write that line into `data/content-bot/media-base-url.txt`, which wins
   over `.env`).
5. Test from outside the network: `curl -I https://media.example.com/media/<file>`
   must answer `200`, then run `./manage.sh instagram-media-verify`, which asks
   the Graph API to download a real file from the media directory without
   publishing anything. That single command is the decisive check.

**Route B2 - reverse proxy on a remote server (no LAN path).** Same idea, but
the proxy cannot reach the stack directly, so the stack dials out:

1. DNS: point the hostname at that server's public IP.
2. From the stack machine keep a reverse tunnel to it:

```bash
autossh -M 0 -N -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:18099:127.0.0.1:8099 user@server
```

   The right-hand side is the local media host (`IG_MEDIA_BIND_IP` /
   `IG_MEDIA_PORT`); the left-hand side is what the server's proxy points at -
   never the stack machine's own address, which is not reachable from outside.
   Run it under systemd so a reboot does not break publishing.
3. On the server, proxy the hostname to `127.0.0.1:18099`.
4. `./manage.sh instagram-media-tunnel-off` retires the quick tunnel while
   nginx keeps serving for the reverse tunnel.
5. Test: `curl -I https://media.example.com/media/<file>` must answer `200`.

**Route C - do nothing.** Keep the bundled quick tunnel. The bot re-reads the
hostname on every publish, so a restart no longer breaks publishing; the only
costs are a random hostname and a dependency on Cloudflare's test service.

**Route D - stack on a server that has a public address.** Nothing tunnels:
the reverse proxy runs next to the origin. Use the built-in Caddy profile:

- during installation, answer yes to *"Publish the Instagram media directory
  with a domain"* - the wizard writes the Caddy site, enables the `ig-media`
  profile and sets `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` for you;
- by hand, add this block to `data/caddy/Caddyfile` and enable the `caddy`
  profile plus `ig-media` in `COMPOSE_PROFILES`:

```caddyfile
media.example.com {
	encode zstd gzip
	reverse_proxy ig-media:80
}
```

Point an `A` record for that hostname at the server, open inbound TCP 80/443
(and UDP 443) so Caddy can issue its certificate, then
`./manage.sh instagram-media-status` shows the URL. No tunnel container is
needed: `./manage.sh instagram-media-tunnel-off` stops the quick tunnel while
leaving nginx running for the proxy.

Whatever the route, only the media subtree is published and the rest of the
bot data directory stays private.

The reverse proxy in every route only ever needs the media origin
(`/media/...`). It must not expose the rest of the bot data directory: the
bundled nginx already answers `404` for `/` and for directory listings.

## Automatic token refresh

Instagram Login long-lived tokens expire after 60 days. The bot extends them on
its own instead of waiting for a failed publish:

- at startup, and then at most once an hour, it checks the stored token;
- when the stored copy is older than seven days it calls
  `GET /refresh_access_token?grant_type=ig_refresh_token` (Instagram Login) or
  the `fb_exchange_token` grant (Facebook Login, requires
  `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`), and a successful extension
  is valid for another 60 days;
- the refreshed token and its expiry are stored in
  `data/content-bot/instagram-token.json` (mode 0600) so a container restart
  keeps publishing, and the Graph client picks the stored token up
  automatically;
- failures never break publishing: the previous token stays in use, the error
  is recorded, and the operator gets one Telegram notice (repeated identical
  failures are throttled to one notice every six hours).

Send `/instagram` in Telegram to see the remaining days, the login variant,
and the last error, and to force a refresh immediately. The operator console
shows the same information on the Overview tab and offers a **Refresh token
now** button. `INSTAGRAM_ACCESS_TOKEN` in `.env` is still the seed value: when
the account owner mints a new token, put it there and restart the bot.

## Configuration

Add these values to the bot's `.env` and restart the bot:

```text
INSTAGRAM_BUSINESS_ID=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.example.com/bot
INSTAGRAM_API_BASE=https://graph.facebook.com
INSTAGRAM_API_VERSION=v26.0
INSTAGRAM_POLL_TIMEOUT_SECONDS=600
INSTAGRAM_DISABLE_REFRESH=false
INSTAGRAM_AUTO_PUBLISH=true
IG_MEDIA_BIND_IP=127.0.0.1
IG_MEDIA_PORT=8099
```

- `INSTAGRAM_BUSINESS_ID` - the numeric Instagram Business account id.
- `INSTAGRAM_ACCESS_TOKEN` - long-lived user or page access token with
  `instagram_basic` + `instagram_content_publish`.
- `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` - public base URL that serves the media
  directory. Keep it empty to let the bundled `ig-media` profile provide one;
  a `*.trycloudflare.com` value here is ignored in favour of the live tunnel
  hostname.
- `INSTAGRAM_API_BASE` - API host. Keep `https://graph.facebook.com` for
  tokens minted through Facebook Login; use `https://graph.instagram.com` for
  tokens from **Instagram API with Instagram Login** (an app whose only job is
  to own the Instagram app id; the Instagram account authorises it directly).
- `INSTAGRAM_API_VERSION` - Graph API version (default `v26.0`).
- `INSTAGRAM_POLL_TIMEOUT_SECONDS` - how long to wait for Meta media
  processing before failing (default 600).
- `INSTAGRAM_AUTO_PUBLISH` - `true` (default) publishes through the Graph API
  when the credentials are set; `false` hides every Instagram API button and
  keeps the manual **Post package** action in the operator console.
- `INSTAGRAM_DISABLE_REFRESH` - `true` stops the scheduled long-lived token
  refresh entirely; use it while an account is under review so the bot does
  not call the token endpoints on its own.
- `IG_MEDIA_BIND_IP` / `IG_MEDIA_PORT` - loopback address and port the bundled
  nginx media host listens on (defaults `127.0.0.1:8099`).

`./manage.sh content-status` shows whether Instagram is configured without
revealing secrets.

## Meta developer console access

Signing in to `developers.facebook.com` can fail with **"Meta for Developers
is not available in this location."** while the normal Facebook pages keep
working in the same browser. This is not purely a country check: Meta also
rejects logins from many datacenter and VPN exit addresses, and the rejection
happens after the password step. Changing the email address or the phone
number of the account does not change the outcome.

The workaround used here leaves the rest of the system untouched and points
**one dedicated browser profile** at the free Cloudflare WARP client running
in SOCKS5 proxy mode. WARP is free, needs no account, and only the
applications that explicitly use the proxy leave through it.

1. Install the WARP client (Debian/Ubuntu):

   ```sh
   curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg \
     | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(. /etc/os-release; echo "$VERSION_CODENAME") main" \
     | sudo tee /etc/apt/sources.list.d/cloudflare-client.list
   sudo apt-get update
   sudo apt-get install -y cloudflare-warp
   sudo systemctl enable --now warp-svc
   ```

2. Register anonymously and switch the client to proxy mode:

   ```sh
   warp-cli --accept-tos registration new
   warp-cli --accept-tos mode proxy
   warp-cli --accept-tos connect
   ```

   The SOCKS5 proxy then listens on `127.0.0.1:40000`.

3. Verify that the exit address belongs to Cloudflare (AS13335):

   ```sh
   curl -x socks5h://127.0.0.1:40000 https://ipinfo.io/json
   ```

4. Give one dedicated browser profile the proxy. For Firefox, create a
   profile directory containing this `user.js`:

   ```js
   user_pref("network.proxy.type", 1);
   user_pref("network.proxy.socks", "127.0.0.1");
   user_pref("network.proxy.socks_port", 40000);
   user_pref("network.proxy.socks_version", 5);
   user_pref("network.proxy.socks_remote_dns", true);
   ```

   and start it with:

   ```sh
   firefox --new-instance --profile /path/to/that/profile
   ```

5. Create the app, add the use case, and generate the token in that browser.
   Everything else on the machine keeps using the normal connection.

Notes:

- The proxy is per application: SSH, the normal browser, and any existing
  tunnel are not affected.
- The same pattern works on a headless machine: run WARP there, point a
  Firefox profile (or an enterprise `policies.json` policy) at the local
  SOCKS port, and drive the browser on that machine.
- If the console still refuses to open, clear the cookies of the WARP profile
  and sign in again; the address check from step 3 must show Cloudflare.
- To stop using it, run `warp-cli --accept-tos disconnect`. In proxy mode the
  client only carries the traffic of the profile above, so leaving it
  installed is harmless.

### When WARP is needed, and when it is not

WARP exists here for one reason only: the Meta developer console rejects the
network used to **create the app and the token**. It is a browser-side
workaround for that single page.

It is not part of the stack:

- No stack container runs WARP and nothing in `docker-compose.yml` depends on
  it. Outbound API calls (Instagram Graph, Telegram, model providers, image
  registries) use the host network and its router.
- With `INSTAGRAM_AUTO_PUBLISH=false` the stack publishes through the manual
  package, which never calls Meta, so WARP is not needed at all in that mode.
- WARP becomes relevant again only when a Meta app or token has to be created
  or replaced, and only on the machine in front of that browser.

## Manual checklist

1. **Professional account**: in the Instagram app, Settings > Account type
   and tools > Switch to professional account, and pick Business or Creator.
2. **Create the Meta app**: sign in at
   `https://developers.facebook.com/apps/creation/` (use the WARP browser if
   the console blocks your network) and create an app with the
   **Manage messaging & content on Instagram** use case.
3. **Open the Instagram API setup page** of the use case. This stack supports
   both setup pages:
   - **Instagram API with Instagram Login** - the page shows an Instagram app
     id and app secret; no Facebook Page is needed. Use
     `INSTAGRAM_API_BASE=https://graph.instagram.com`.
   - **Instagram API with Facebook Login** - the account must be linked to a
     Facebook Page and the app needs the Page permissions as well. Use
     `INSTAGRAM_API_BASE=https://graph.facebook.com`.
4. **Assign the Instagram Tester role** while the app is in development mode:
   App roles > Roles > Add people > **Instagram Tester** > enter the
   Instagram **username** (not the display name). Then accept the invite on
   Instagram: Settings > Website permissions > Apps and websites >
   Tester invites. Until it is accepted the role stays in the pending list,
   and no "Tester invites" entry exists on Instagram before the invite is
   sent from the console.
5. **Add the account and generate the token**: back on the API setup page
   click **Add account**, sign in with the Instagram professional account,
   then click **Generate token** next to the account. Webhooks are not
   needed for publishing; they are only for incoming events (comments and
   messages).
6. **Collect the four values**: Instagram app id, Instagram app secret
   (Show button), Instagram account id, and the access token.
7. **Validate the token** before touching the bot:

   ```sh
   curl "https://graph.instagram.com/me?fields=user_id,username,account_type&access_token=TOKEN"
   ```

   The call must return the account (`user_id`, `username`, `account_type`).
8. **Configure and restart**: put the values in `.env` (see
   [Configuration](#configuration)) and restart the bot.

Instagram Login tokens are valid for 60 days. Refresh one before it expires
with the app secret:

```text
GET https://graph.instagram.com/refresh_access_token
  ?grant_type=ig_refresh_token
  &access_token=<long-lived token>
```

If a publish attempt fails with a permission error, open the use case page
again and make sure the content-publish permission
(`instagram_business_content_publish` for Instagram Login or
`instagram_content_publish` for Facebook Login) is added to the app, then
generate a fresh token and update `.env`.

Useful direct links:

- Meta developer apps: `https://developers.facebook.com/apps/`
- App creation: `https://developers.facebook.com/apps/creation/`
- Graph API Explorer: `https://developers.facebook.com/tools/explorer/`
- Page creation: `https://www.facebook.com/pages/create`
- Instagram tester invites: `https://www.instagram.com/accounts/manage_access/`

## If app creation fails

Meta rejects app creation for several common reasons:

- The Facebook account is not fully verified (confirm the email address and
  phone number and re-check the developer terms acceptance).
- The account is new, has no real profile activity, or is not an administrator
  of the linked Page.
- Regional or device restrictions: try another browser without ad blockers, an
  incognito window, or a different network, and retry after a few hours.
- **Location blocked**: when the console itself reports "Meta for Developers
  is not available in this location", the network exit address is the problem,
  not the account. Use the WARP workaround above; changing the phone number or
  the email address does not help. Unofficial cookie-based automation
  violates Meta's terms and breaks without notice; this stack will not
  include it.

## Verify end to end

1. Send a link or topic to the bot and create a draft.
2. Choose **Send my image** (single) or **Send several images** (album), send
   one or more photos, and press **Done** to close the album.
3. Approve to Instagram (or to both Telegram and Instagram).
4. Check the Instagram account: a photo, carousel, or video appears with the
   plain-text caption (title, body paragraphs, source link once at the end).
