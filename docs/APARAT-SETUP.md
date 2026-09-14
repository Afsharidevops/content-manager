# Aparat setup guide

Aparat is a **video-only automatic channel**. Once a browser session is stored,
every draft that carries a video shows an **Aparat (auto)** entry under
**More platforms...**, and picking it uploads the video and publishes it on the
channel. Nothing else changes: text drafts, images, the daily proposals, and
the scheduled routines keep working exactly as before, and Aparat only receives
a post when the operator picks it on a draft that has a video.

Two things make Aparat different from Bale, Eitaa, or LinkedIn:

- **There is no upload API key.** Aparat issues no self-service token, so the
  bot reuses the session of a browser you signed in with: a JWT or the cookie
  header. The session is a credential - treat it like a password.
- **Aparat accepts videos only.** It has no text post and no photo post, so the
  adapter refuses those drafts and points back at the copy-ready package
  (title, description, tags, and the media file) instead of failing silently.

## 1. Sign in and copy the session

1. Open **https://www.aparat.com** in a browser and sign in with the account
   that owns the channel the uploads should land on.
2. Open the browser developer tools (`F12` or `Ctrl+Shift+I`) and switch to the
   console.
3. Paste this line and press Enter:

   ```javascript
   copy(localStorage.getItem('jwt'))
   ```

   The value is now on your clipboard. It is the **session token** and is the
   preferred credential. It lives as long as the browser session does; when
   Aparat eventually rejects it, repeat this step and store the new value.

If `jwt` is empty (an older session, or a browser that blocks local storage),
copy the cookie instead:

1. Open the developer tools, switch to **Network**, and reload
   `https://www.aparat.com/uploadvideo`.
2. Click the document request for `uploadvideo` and find the **Request
   Headers** section.
3. Copy the whole value of the **Cookie** header - it is one long line that
   starts with something like `AuthV1=...`.

Never paste the session into a chat message, an issue, or a shared screen: it
can publish videos on the account until it expires.

## 2. Store the session

Either use the operator panel, or edit `.env` and restart the bot.

### Panel

Open **Platforms -> Aparat** and fill in **Session token** (or **Session
cookie**). The card reports `ready` as soon as one of the two is stored, and
its **Test** button proves the session against Aparat before you publish
anything.

### .env

```bash
CONTENT_APARAT_TOKEN=jwt-value-from-the-browser
# or, when the cookie is the only thing you have:
# CONTENT_APARAT_COOKIE=AuthV1=...; AFCN=...; m_id=...
```

Then apply it:

```bash
./manage.sh start                # or: ./manage.sh content-aparat-check
```

`CONTENT_PLATFORMS_ENABLED=true` is required for the **More platforms...**
button to appear on a draft at all - that switch is shared with every extra
platform.

## 3. Verify

```bash
./manage.sh content-aparat-check
# Aparat session is valid (upload server https://...).
```

The same check from the panel is **Platforms -> Aparat -> Test**. Both only ask
Aparat for an upload server; no video is uploaded and nothing is published.

`./manage.sh content-channels` reports the channel beside Bale, Eitaa, and
LinkedIn:

```text
Aparat: session token stored (video drafts publish when picked)
```

## 4. Publish a video

1. Get a video onto the draft, in one of the three usual ways:
   - **Send a clip to the bot** while the draft waits for media (or right after
     a proposal, when the media question is up). Files up to 20 MB can be
     downloaded by the bot; a larger file stays on Telegram and cannot be
     uploaded to Aparat - send a smaller copy.
   - **Generate one with Media Studio**: on the media question pick the video
     prompt entry, answer the character/style questions, and the finished clip
     is downloaded to the bot's own storage, so size is not an issue.
   - **Let the editor normalise your clip**: after sending a video the bot asks
     "Edit it / Publish as-is". The edited result is stored the same way.
2. Open **More platforms...** on the draft (or on the media preview).
3. Press **Aparat**.
   - With a video attached, the upload starts immediately and the chat answers
     with the watch link as soon as Aparat returns one.
   - Without a video, the bot asks for the file: send the clip and the upload
     starts the moment it lands. The request stays pending until a video
     arrives, and **Approve**/**Reject** keep working meanwhile.
4. Aparat processes the upload after the transfer; the video appears on the
   channel page when that finishes. The publication row records the target, the
   status, and the watch URL under the draft.

A video draft stays publishable to the other targets (Telegram, Bale, Eitaa,
LinkedIn) on its own; **Approve** publishes to the normal destinations and
never uploads to Aparat by itself. **All targets** skips Aparat unless the
draft carries a video.

Once Aparat accepted a draft, the entry reads **Aparat / sent** and a second
attempt is refused, so the same clip is never uploaded twice.

## Settings

Every key below lives in `.env` (or in the panel card). Only the first two are
required.

| Key | Default | What it does |
| --- | --- | --- |
| `CONTENT_APARAT_TOKEN` | - | Browser session JWT from `localStorage.getItem('jwt')`. Preferred. |
| `CONTENT_APARAT_COOKIE` | - | Alternative session: the whole `Cookie` request header of a signed-in tab. |
| `CONTENT_APARAT_LABEL` | `Aparat` | Button label in the **More platforms...** chooser. |
| `CONTENT_APARAT_CATEGORY` | `10` | Aparat category id of every upload (see the table below). |
| `CONTENT_APARAT_TAGS` | `technology,video,tutorial` | Default tags, used when the draft and the platform profile carry fewer than three. |
| `CONTENT_APARAT_WATERMARK` | `1` | `1` keeps the Aparat watermark, `0` turns it off. |
| `CONTENT_APARAT_VIDEO_PASS` | `0` | `0` publishes the upload, `1` leaves it unpublished for a manual publish. |
| `CONTENT_APARAT_API_BASE` | `https://www.aparat.com` | Change it only behind a proxy. |
| `CONTENT_APARAT_TIMEOUT` | `120` | Seconds per HTTP call, including one chunk. |
| `CONTENT_APARAT_CHUNK_BYTES` | `3145728` | Upload chunk size (3 MiB). |

Title, description, and tags come from the draft: the title is the draft title
cut to 100 characters, the description is the adapted post text with the source
link and the configured hashtags (cut to 4000 characters), and the tags are the
hashtags of the text plus `CONTENT_APARAT_TAGS`. The same per-destination
adaptation that rewrites text for LinkedIn applies here, so a Persian draft
stays Persian and an English profile keeps its tone.

### Category ids

| Id | Category | Id | Category |
| --- | --- | --- | --- |
| 3 | Education and learning | 16 | Business |
| 10 | Technology and computers | 17 | Culture and art |
| 12 | Accidents | 22 | Video games |
| 15 | Miscellaneous | 28 | Finance and economy |

The full list is served by Aparat at
`https://www.aparat.com/etc/api/categories`.

## How the upload works

Knowing the four steps makes the failure messages readable:

1. `GET /api/fa/v1/video/upload/upload_config` - asks Aparat for an upload
   server. A `401` here means the stored session is expired or wrong.
2. `POST /api/fa/v1/video/upload/upload_url` - reserves one upload slot and
   returns its token and id.
3. `POST <upload server>/upload` - sends the file in chunks (3 MiB by default)
   with an `X-Token` header, then `POST <upload server>/chunksdone` closes it.
   A failure here is a transfer problem, not a credential problem.
4. `POST /api/fa/v1/video/upload/upload/uploadId/<id>` - submits the title,
   description, tags, and category that turn the file into a video.

Nothing is published until step 4 succeeds, and a draft is only marked as sent
after that.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Aparat refused the stored session ... CONTENT_APARAT_TOKEN` | The JWT expired or was copied with quotes. Sign in again, copy `localStorage.getItem('jwt')` (without the surrounding quotes), store it, and rerun `./manage.sh content-aparat-check`. |
| `Aparat session is not configured` | Neither key is set in the container. Store one and restart the bot (`./manage.sh start`). |
| `Aparat publishes videos only; attach a video to this draft first` | The draft has no video. Send a clip, generate one with Media Studio, or pick the copy-ready package from **More platforms...**. |
| The video is over the 20 MB Telegram bot download limit | Telegram only serves bot downloads up to 20 MB, so the file never reaches the bot's storage. Send a smaller copy or produce the video with Media Studio. |
| `Aparat could not close the upload` / `Aparat chunk 2/5 failed` | The transfer to the upload server broke. Retry the platform; if it repeats, lower `CONTENT_APARAT_CHUNK_BYTES`. |
| `Aparat did not return an upload server` | Aparat answered without a server, usually an account that may not upload (blocked, or a session of a different sub-domain). Sign in on `www.aparat.com` and copy the session again. |
| The video uploads but stays unpublished | `CONTENT_APARAT_VIDEO_PASS=1` is set. Set it to `0` and publish again. |
| Uploads stopped after weeks of working | The session expired; repeat step 1 and store the new value. |
| Aparat wants tags | A draft without hashtags falls back to `CONTENT_APARAT_TAGS`; fill that key with three tags that fit the channel. |

## Notes and limits

- The session is a full account credential: anyone who can read the container
  environment can publish to the channel. Rotate it by signing out of the
  browser session and copying a fresh one.
- Uploads are one way. The bot never deletes, edits, or lists videos from
  Aparat; use the Aparat dashboard for that.
- The adapter does not re-upload: a draft that already published to Aparat is
  skipped with `already published` until its `published_targets` entry is
  cleared.
- Aparat limits uploads per account on its own side (rate limits, video size,
  and duration). When Aparat refuses a file, the message it returns is shown
  unchanged in the chat.
