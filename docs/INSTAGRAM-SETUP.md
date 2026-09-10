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
media and Instagram is enabled:

- **Approve to Instagram** - publishes to Instagram only,
- **Approve to Telegram + Instagram** - publishes to the Telegram channel
  first, then to Instagram.

Media Studio generated images and operator-uploaded photos both work; the
brand chip is applied before any upload. Albums are also sent to the Telegram
channel as a media group.

## Media URLs

The Graph API cannot accept local files; every photo or video must be
reachable at a **public HTTPS URL**. The bot stores files under
`data/content-bot/media/` and maps them to `INSTAGRAM_MEDIA_PUBLIC_BASE_URL`
plus the path under the bot data directory, so the effective URL is
`<base>/media/<file>`.

Examples:

```text
https://media.locallab.ir/bot       -> https://media.locallab.ir/bot/media/<file>
https://cdn.example.com/content-bot -> https://cdn.example.com/content-bot/media/<file>
```

Serve the media directory with any static web server (Caddy, nginx, an object
store, ...). Expose only the `media/` subtree and keep the rest of the bot
data directory private. The URL must be directly fetchable by Meta, without
login or bot protection.

For a quick local test, serve the directory and expose it with a temporary
tunnel (for example `cloudflared tunnel --url http://127.0.0.1:8080`); use a
stable host for permanent operation.

## Configuration

Add these values to the bot's `.env` and restart the bot:

```text
INSTAGRAM_BUSINESS_ID=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.locallab.ir/bot
INSTAGRAM_API_BASE=https://graph.facebook.com
INSTAGRAM_API_VERSION=v26.0
INSTAGRAM_POLL_TIMEOUT_SECONDS=600
```

- `INSTAGRAM_BUSINESS_ID` - the numeric Instagram Business account id.
- `INSTAGRAM_ACCESS_TOKEN` - long-lived user or page access token with
  `instagram_basic` + `instagram_content_publish`.
- `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` - public base URL that serves the media
  directory (required for publishing).
- `INSTAGRAM_API_BASE` - API host. Keep `https://graph.facebook.com` for
  tokens minted through Facebook Login; use `https://graph.instagram.com` for
  tokens from **Instagram API with Instagram Login** (an app whose only job is
  to own the Instagram app id; the Instagram account authorises it directly).
- `INSTAGRAM_API_VERSION` - Graph API version (default `v26.0`).
- `INSTAGRAM_POLL_TIMEOUT_SECONDS` - how long to wait for Meta media
  processing before failing (default 600).

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
