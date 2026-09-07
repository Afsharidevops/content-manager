# Instagram/Meta setup guide (pending platform)

Instagram publishing through the Meta Graph API needs a **Business or Creator**
Instagram account, a Facebook Page link, and a Meta developer app. The Content
Bot platform layer is designed so this becomes one adapter plus the credentials
below; the manual steps cannot be automated by the installer.

The current status is **pending**: the adapter ships in a later phase. When it
does, `./manage.sh content-connect-instagram` will validate the token and finish
provisioning.

## Manual checklist

1. **Convert the Instagram account** to Business or Creator and connect it to a
   Facebook Page: Instagram > Settings > Account type, then Linked accounts.
   Creator accounts can post feed content and Reels; Stories publishing needs a
   Business account linked to a Page.
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

## What the adapter will store

Once the manual step is complete, the installer/manager will keep these values
in `.env` (never committed): `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`,
`INSTAGRAM_LONG_TOKEN`, and `INSTAGRAM_BUSINESS_ID`, then validate with a live
Graph API call before enabling Instagram publishing in the approval flow.
