# Instagram/Meta setup guide

Instagram publishing uses the official **Meta Graph API** (no unofficial
cookie automation). It needs a **Business or Creator** Instagram account
linked to a Facebook Page, a Meta developer app, and a long-lived token with
the `instagram_basic` and `instagram_content_publish` permissions.

## Current status

`content-bot` ships an Instagram publisher (`content_bot/instagram.py`) that
publishes approved drafts to the configured Instagram account through the
Graph API:

- a single attached photo publishes as a photo,
- several attached photos publish as a **carousel** (2-10 photos),
- an attached MP4/MOV video publishes as a feed video,
- text-only posts cannot be published to Instagram.

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
reachable at a **public HTTPS URL**. The bot maps each local file under
`data/content-bot/media/` to `INSTAGRAM_MEDIA_PUBLIC_BASE_URL/<relative path>`.

Examples of public bases:

```text
https://media.locallab.ir/bot            -> https://media.locallab.ir/bot/<file>
https://cdn.example.com/content-bot      -> https://cdn.example.com/content-bot/<file>
```

Serve the `data/content-bot/media/` directory with any static web server
(Caddy, nginx, an object store, ...). The URL must be directly fetchable by
Meta, without login or bot protection.

## Configuration

Add these values to the bot's `.env` and restart the bot:

```text
INSTAGRAM_BUSINESS_ID=
INSTAGRAM_ACCESS_TOKEN=
INSTAGRAM_MEDIA_PUBLIC_BASE_URL=https://media.locallab.ir/bot
INSTAGRAM_API_VERSION=v23.0
INSTAGRAM_POLL_TIMEOUT_SECONDS=600
```

- `INSTAGRAM_BUSINESS_ID` - the numeric Instagram Business account id.
- `INSTAGRAM_ACCESS_TOKEN` - long-lived user or page access token with
  `instagram_basic` + `instagram_content_publish`.
- `INSTAGRAM_MEDIA_PUBLIC_BASE_URL` - public base URL that serves the media
  directory (required for publishing).
- `INSTAGRAM_API_VERSION` - Graph API version (default `v23.0`).
- `INSTAGRAM_POLL_TIMEOUT_SECONDS` - how long to wait for Meta media
  processing before failing (default 600).

`./manage.sh content-status` shows whether Instagram is configured without
revealing secrets.

## Manual checklist

1. **Convert the Instagram account** to Business or Creator and connect it to
   a Facebook Page: Instagram > Settings > Account type, then Linked accounts.
   Creator accounts can post feed content and Reels; Stories publishing needs
   a Business account linked to a Page.
2. **Create the Meta app**: sign in to
   `https://developers.facebook.com/apps/creation/` with the same Facebook
   account that administers the Page and choose **Business** as the app type.
3. **Add the Instagram Graph API product** from the app dashboard and connect
   the Instagram account to the app.
4. **Generate credentials**: create a long-lived access token with the
   `instagram_basic` and `instagram_content_publish` permissions, and note the
   Instagram Business account ID (`/me/accounts` then `/me` on the Page).
5. **Validate** with a test call before enabling the adapter, for example
   `GET /{instagram-business-id}?fields=id,username` in the Graph API Explorer.

Useful direct links:

- Meta developer apps: `https://developers.facebook.com/apps/`
- App creation: `https://developers.facebook.com/apps/creation/`
- Graph API Explorer: `https://developers.facebook.com/tools/explorer/`
- Page creation: `https://www.facebook.com/pages/create`

## If app creation fails

Meta rejects app creation for several common reasons:

- The Facebook account is not fully verified (confirm the email address and
  phone number and re-check the developer terms acceptance).
- The account is new, has no real profile activity, or is not an administrator
  of the linked Page.
- Regional or device restrictions: try another browser without ad blockers, an
  incognito window, or a different network, and retry after a few hours.
- **Iran-based accounts**: Meta generally cannot complete developer
  verification from Iran. If the error persists for your account/region, the
  Graph API path is unavailable regardless of what the installer does. Options
  in that case: use Meta Business Suite's manual scheduler, post through a
  partner that has a Meta-approved relationship, or keep Instagram manual until
  the situation changes. Unofficial cookie-based automation violates Meta terms
  and breaks without notice; this stack will not include it.

## Verify end to end

1. Send a link or topic to the bot and create a draft.
2. Choose **Send my image** (single) or **Send several images** (album), send
   one or more photos, and press **Done** to close the album.
3. Approve to Instagram (or to both Telegram and Instagram).
4. Check the Instagram account: a photo, carousel, or video appears with the
   plain-text caption (title, body paragraphs, source link once at the end).
