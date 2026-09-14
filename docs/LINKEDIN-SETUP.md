# LinkedIn setup guide

LinkedIn is an automatic channel. Once an access token and an author are
configured, picking the LinkedIn entry on a draft publishes it immediately
through the LinkedIn REST API - the text is adapted for the destination first
when adaptation is on, and one image from the draft is uploaded with it.

Two destination types are supported:

- **Personal profile** (`w_member_social`) - the post appears under the member
  who granted the token: `urn:li:person:<id>`.
- **Company page** (`w_organization_social`) - the post appears under the
  page: `urn:li:organization:<id>`.

A deployment can publish to both at once: one account per destination, each
one offered separately on the draft under **More platforms...**.

LinkedIn video and document (carousel) posts are not supported by this
adapter. A draft that carries a video is refused with a clear message instead
of failing silently, so publish the text and attach the video in the app.

## 1. Create the LinkedIn app

1. Open **https://www.linkedin.com/developers/apps** and press **Create app**.
2. Fill in the app name, the LinkedIn page to associate it with, an app logo,
   and a contact email.
3. Open the **Products** tab and request:
   - **Share on LinkedIn** - the personal-profile publisher
     (`w_member_social`). It is granted immediately for most apps.
   - **Sign In with LinkedIn using OpenID Connect** - recommended: it grants
     `openid` and `profile`, which is how the token reports the member id that
     the personal URN needs.
   - **Community Management API** - only for company-page posts
     (`w_organization_social`). LinkedIn reviews this request; the personal
     flow works without it.
4. Open the **Auth** tab and note the **Client ID** and **Client Secret**.
5. Under **OAuth 2.0 settings** add a redirect URL. For a one-time token mint
   `https://localhost:8765/callback` is enough - nothing has to listen there
   when you copy the `code` out of the browser address bar.

## 2. Mint an access token

LinkedIn's 3-legged flow issues a token that lives about **60 days**, and an
app that has not been approved for Marketing Developer Platform access gets no
refresh token. Expect to repeat this step when the token expires; the account
file's `expires_at` field is informational and can hold the date you minted
it.

1. Build the authorization URL with your Client ID and open it in a browser.
   The URL is the endpoint below; every value in it has to be URL-encoded, so
   replace `<CLIENT_ID>` in this one line and paste it into the address bar:

```text
https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=<CLIENT_ID>&redirect_uri=https%3A%2F%2Flocalhost%3A8765%2Fcallback&state=stack-linkedin-setup&scope=openid%20profile%20w_member_social
```

   For a company page add `%20w_organization_social` to the `scope` value
   (`...%20w_member_social%20w_organization_social`).

   To print the same link instead of editing it by hand - useful when the
   redirect URL is not localhost - fill in the values and run this in any
   terminal:

```bash
python3 - <<'PY'
import urllib.parse

query = urllib.parse.urlencode(
    {
        "response_type": "code",
        "client_id": "YOUR_CLIENT_ID",
        "redirect_uri": "https://localhost:8765/callback",
        "state": "stack-linkedin-setup",
        "scope": "openid profile w_member_social",
    },
    quote_via=urllib.parse.quote,
)
print(f"https://www.linkedin.com/oauth/v2/authorization?{query}")
PY
```

   The redirect URL must match one registered on the app's **Auth** tab. The
   localhost value above works for a one-time mint and nothing has to listen
   on it: the browser shows the `code` in the address bar even when the page
   itself cannot load. If LinkedIn refuses localhost for your app, register
   any HTTPS URL you control instead.

2. Sign in as the member who should own the post - or as an admin of the page -
   and approve the app. LinkedIn then redirects to the redirect URL with
   `?code=...`. Copy the code quickly: it is single-use and short-lived.
3. Exchange the code for the token:

```bash
CLIENT_ID='...'
CLIENT_SECRET='...'
REDIRECT_URI='https://localhost:8765/callback'
CODE='...'

curl -s -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "code=${CODE}" \
  --data-urlencode "redirect_uri=${REDIRECT_URI}" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "client_secret=${CLIENT_SECRET}"
```

Three things have to line up or LinkedIn answers
`invalid_request: appid/redirect uri/code verifier does not match`:

- `REDIRECT_URI` here is the **plain** value exactly as registered on the
  app's Auth tab - `https://localhost:8765/callback`, not the
  `https%3A%2F%2F...` form from the authorization URL. `--data-urlencode`
  encodes it; passing the already-encoded value double-encodes it and the
  two requests no longer match.
- The `CLIENT_ID` and `CLIENT_SECRET` belong to the same app that issued the
  code.
- The `code` is fresh and unused. It is single-use: re-running the exchange
  with the same code fails, and a failed attempt may already have consumed
  it, so always reopen the authorization link and copy a new one.

The answer carries the token and its lifetime:

```json
{"access_token":"AQX...","expires_in":5184000,"scope":"openid profile w_member_social"}
```

If it is easier, let one script do both halves with the same values, so a
mismatch cannot slip in:

```bash
CLIENT_ID='...'
CLIENT_SECRET='...'
REDIRECT_URI='https://localhost:8765/callback'   # plain value, as registered
SCOPES='openid profile w_member_social'

python3 - "$CLIENT_ID" "$REDIRECT_URI" "$SCOPES" <<'PY'
import sys
import urllib.parse

client_id, redirect_uri, scopes = sys.argv[1:4]
query = urllib.parse.urlencode(
    {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": "stack-linkedin-setup",
        "scope": scopes,
    },
    quote_via=urllib.parse.quote,
)
print("Open this link, approve the app, and copy ?code=... from the address bar:")
print(f"https://www.linkedin.com/oauth/v2/authorization?{query}")
PY
read -r -p 'Paste the code: ' CODE
curl -s -X POST https://www.linkedin.com/oauth/v2/accessToken \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=authorization_code' \
  --data-urlencode "code=${CODE}" \
  --data-urlencode "redirect_uri=${REDIRECT_URI}" \
  --data-urlencode "client_id=${CLIENT_ID}" \
  --data-urlencode "client_secret=${CLIENT_SECRET}"
```

4. Find the author id for the destination.

**Personal profile** - call the OpenID userinfo endpoint with the token (this
needs the `openid profile` scopes from step 1):

```bash
ACCESS_TOKEN='AQX...'

curl -s https://api.linkedin.com/v2/userinfo \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

`sub` is the member id, so the author is `urn:li:person:<sub>`.

**Company page** - the numeric id is visible in the page admin URL:
`https://www.linkedin.com/company/<id>/admin/dashboard/` means the author is
`urn:li:organization:<id>`. With `rw_organization_admin` (part of the
Community Management API) the admin roles are also readable:

```bash
curl -s 'https://api.linkedin.com/v2/organizationAcls?q=roleAssignee&role=ADMINISTRATOR&state=APPROVED&projection=(elements*(organization~(id,localizedName)))' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

## 3. Verify before touching the stack

The check the operator console runs reserves one image upload slot. It
validates the token, the `LinkedIn-Version` header, and the author permission
in one call, and publishes nothing:

```bash
ACCESS_TOKEN='AQX...'
AUTHOR='urn:li:person:abc123'

curl -s -X POST 'https://api.linkedin.com/rest/images?action=initializeUpload' \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H 'LinkedIn-Version: 202601' \
  -H 'X-Restli-Protocol-Version: 2.0.0' \
  -H 'Content-Type: application/json' \
  -d "{\"initializeUploadRequest\":{\"owner\":\"${AUTHOR}\"}}"
```

An answer that contains `value.uploadUrl` means everything is in place. A
`401` means the token is expired or revoked, and a `403` means the token does
not carry the write scope for that author.

## 4. Configure the stack

### Account file (recommended)

`social-accounts.yaml` in the policy directory declares every destination.
The deployed working copy lives at `data/content-manager/config/`, mounted
read-only at `/policy`; the directory is git-ignored, so tokens never reach
the repository:

```yaml
accounts:
  linkedin:
    personal:
      type: person
      label: "LinkedIn (personal)"
      access_token: "AQX..."
      person_id: "abc123"
      expires_at: "2026-11-13"
    company:
      type: organization
      label: "LinkedIn (company page)"
      access_token: "AQX..."
      organization_id: "12345678"
```

Every key under `linkedin:` becomes one destination: the button label comes
from `label` (or `LinkedIn (<account>)`), and the target key is
`linkedin_<account>`. Instead of `person_id` / `organization_id` you may write
`author_urn: "urn:li:person:abc123"` - an explicit URN always wins.

The same file path is configurable with `CONTENT_SOCIAL_ACCOUNTS_FILE` (a path
inside the container, for example `/data/social-accounts.yaml`, which works
without touching the policy directory).

### Environment fallback

A deployment with exactly one account can skip the file:

```bash
CONTENT_LINKEDIN_ACCESS_TOKEN=AQX...
CONTENT_LINKEDIN_ACCOUNT=personal
CONTENT_LINKEDIN_ACCOUNT_TYPE=person
CONTENT_LINKEDIN_PERSON_ID=abc123
# or, for a company page:
# CONTENT_LINKEDIN_ACCOUNT_TYPE=organization
# CONTENT_LINKEDIN_ORGANIZATION_ID=12345678
# or an explicit URN instead of the id:
# CONTENT_LINKEDIN_AUTHOR_URN=urn:li:person:abc123
```

The file wins when both are present for the same account key. A single
`CONTENT_LINKEDIN_ACCOUNTS` value may also hold the whole mapping as JSON or
YAML.

### Quick command and console

```bash
./manage.sh content-connect-linkedin \
  --token 'AQX...' --type person --person-id 'abc123' --verify
```

`--verify` runs the upload-slot probe from step 3 without publishing anything,
and the command recreates `content-bot` afterwards so it reads the new values.
There is no `--test` publish: the first real draft is the publish test.

The operator console shows the same keys as a **LinkedIn** card
(**Platforms -> LinkedIn**) with **Test connection** and **Apply changes**;
`docs/PANEL.md` describes that view.

## 5. Tone, targets, and prompts

A draft is written once and adapted per destination before publishing. The
LinkedIn tones ship with the bot:

- `linkedin_personal` - first person, technical, opinionated, founder voice.
- `linkedin_company` - educational, neutral, brand voice.
- `telegram` - concise and news-shaped.

The tone of a LinkedIn account follows its type: `organization` adapts through
`linkedin_company`, `person` through `linkedin_personal`.

Override a tone, its style, or its length in the `tones:` section of
`editorial-policy.yaml`; replace the rewrite prompt itself with a file named
`<tone>.md` in `prompt-templates/` inside the policy directory
(`data/content-manager/config/prompt-templates/linkedin_personal.md`), or
point elsewhere with `CONTENT_PROMPT_DIR`. Set `CONTENT_ADAPT_ENABLED=false`
to publish the draft text unchanged everywhere.

Targets decide where a draft goes:

- A draft may carry `targets: [linkedin_personal, linkedin_company, bale]`
  (written as `platform_account`, or as
  `{platform: linkedin, account: personal}`).
- Without draft targets, the `publishing:` section of `editorial-policy.yaml`
  supplies defaults:

```yaml
publishing:
  targets:
    - linkedin_personal
    - bale
```

- Without either, **All targets** publishes to every configured automatic
  channel.

Target keys name the channels the bot publishes through its channel adapters:
`bale`, `eitaa`, and one key per LinkedIn account (`linkedin_<account>`).
Telegram and Instagram keep their own approve-time paths (the Approve button
publishes to the Telegram channel, and Instagram follows its own Graph or
package flow), so they are not written as targets.

## 6. Publish flow

1. **More platforms...** on a draft lists each configured LinkedIn account as
   `<label> (auto)` next to the other channels. The generic `LinkedIn` package
   entry stays beside them for hand-offs the API adapter refuses (a video post,
   for example); add `linkedin: null` to the `platforms:` section of the policy
   to hide that entry once accounts are configured.
2. Picking one adapts the body for that tone, uploads the draft's first image
   when there is one, creates the post, and answers with the post URN.
3. **All targets** publishes to every configured automatic channel and reports
   one summary line per target; a failure on one destination never stops the
   others.
4. Every attempt is stored in `data/content-bot/state.json`: `publications`
   rows carry `id`, `content_id`, `platform`, `account`, `status`
   (`published` / `failed` / `skipped`), `remote_id`, `published_at`, and a
   capped `error`; `content_variants` keeps the adapted text per target so a
   retry never asks the writer twice.
5. The draft records its `published_targets`, so the same post is never sent
   to the same destination twice.

## 7. Token rotation

Repeat step 2 before the token expires and replace `access_token` (and
`expires_at`) in the account file or the `.env` values; the bot reads the file
on start, so run `./manage.sh restart content` after editing it. A revoked or
expired token surfaces as `HTTP 401` in the publish message and in the
publication row, and LinkedIn throttling (`429`) is retried with backoff before
it is reported.

## 8. Configuration reference

| Key | Default | Meaning |
| --- | --- | --- |
| `CONTENT_LINKEDIN_ACCESS_TOKEN` | - | Fallback account token |
| `CONTENT_LINKEDIN_ACCOUNT` | `personal` | Fallback account key |
| `CONTENT_LINKEDIN_ACCOUNT_TYPE` | `person` | `person` or `organization` |
| `CONTENT_LINKEDIN_PERSON_ID` / `CONTENT_LINKEDIN_ORGANIZATION_ID` | - | Ids that build the author URN |
| `CONTENT_LINKEDIN_AUTHOR_URN` | - | Explicit URN; wins over the ids |
| `CONTENT_LINKEDIN_LABEL` | - | Button label override |
| `CONTENT_LINKEDIN_API_BASE` | `https://api.linkedin.com` | Change only behind a proxy |
| `CONTENT_LINKEDIN_API_VERSION` | `202601` | Pinned `LinkedIn-Version` month |
| `CONTENT_LINKEDIN_TIMEOUT` | `60` | Per-request timeout in seconds |
| `CONTENT_LINKEDIN_RETRIES` | `3` | Attempts per request (429 and 5xx) |
| `CONTENT_SOCIAL_ACCOUNTS_FILE` | `social-accounts.yaml` | Account file name inside the policy directory |
| `CONTENT_ADAPT_ENABLED` | `true` | Turn the per-destination rewrite off |
| `CONTENT_PROMPT_DIR` | `<policy>/prompt-templates` | Prompt template directory |

## 9. Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `401` when publishing | Token expired (~60 days) or was revoked; mint a new one |
| `403` when publishing | The app lacks `w_member_social` (personal) or `w_organization_social` (page), or the member is not an admin of the page |
| `403` only for the company page | Community Management API still under review, or the member is not a page admin |
| Nothing appears under **More platforms...** | `CONTENT_PLATFORMS_ENABLED=true` is required; also check the account has both a token and an author |
| The LinkedIn entry is missing while others show | The account was skipped at startup: the log names the reason (missing token, unreadable file) |
| `invalid_request: appid/redirect uri/code verifier does not match` | The token request and the authorization request disagree: pass the plain `redirect_uri` (not the percent-encoded form), use the same app's client id and secret, and copy a fresh single-use `code` |
| The Auth tab refuses to save the redirect URL | Register an HTTPS URL you control instead of localhost; the page does not have to exist, the `code` is visible in the address bar |
| `LinkedIn did not return an image upload URL` | The author URN or the write scope is wrong; re-run the step-3 probe |
| Post looks like the draft, not the tone | Adaptation is off, the writer is not configured, or the rewrite failed - the bot falls back to the draft text and logs the reason |
