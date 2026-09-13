# Bale and Eitaa setup guide

Bale and Eitaa are the two automatic messengers of the stack. Unlike
Instagram, they need no review process: once the bot has a token and the
channel has the bot as an administrator, picking the platform on a draft
publishes it immediately, caption and media included.

Both channels appear on a draft only when the extra platforms are enabled:

```bash
CONTENT_PLATFORMS_ENABLED=true
```

The chooser then reads **More platforms...** and lists every platform from the
`platforms:` section of `editorial-policy.yaml`. A channel is marked `(auto)`
when its token and destination are configured, `(no token)` when it still
waits for them, and plain text when it hands over a copy-ready package
instead.

## Bale

Bale's bot API is built on the Telegram Bot API, so the stack talks to it with
the same calls (`sendMessage`, `sendPhoto`, `sendVideo`, `sendDocument`,
`sendMediaGroup`).

### 1. Create the bot

1. Open Bale and start a chat with **@botfather**.
2. Send `/newbot` and choose a name and a username for it.
3. BotFather answers with the token, in the shape
   `123456789:abcdIuZmK5qNEm2A1BhUaAg7MPJv1O9KCcBQB2ro`. Keep it secret; it is
   the password of the bot.

### 2. Prepare the channel

1. Create a channel (or reuse one) and open its **Administrators** settings.
2. Add the bot as an administrator and allow it to post messages.
3. Note the channel username from its public link: `bale.ai/<username>` means
   the id is `@username`. A numeric chat id works as well.

### 3. Verify before touching the stack

Run these from the server; the token never leaves your machine:

```bash
TOKEN='123456789:abcdIuZmK5qNEm2A1BhUaAg7MPJv1O9KCcBQB2ro'
CHANNEL='@your_channel'

curl -s "https://tapi.bale.ai/bot${TOKEN}/getMe"
curl -s -X POST "https://tapi.bale.ai/bot${TOKEN}/sendMessage" \
  -d "chat_id=${CHANNEL}" -d "text=Stack test"
```

`getMe` answers with the bot identity (`"ok":true`) and the second call posts
to the channel. A `403` means the token is wrong, a `400` with `chat not
found` means the channel id is wrong or the bot is not an administrator.

### 4. Configure the stack

```bash
CONTENT_BALE_TOKEN=123456789:abcdIuZmK5qNEm2A1BhUaAg7MPJv1O9KCcBQB2ro
CONTENT_BALE_CHAT_ID=@your_channel
CONTENT_BALE_API_BASE=https://tapi.bale.ai
```

## Eitaa

Eitaa publishes through the EitaaYar gateway (`eitaayar.ir`), which issues the
bot token and relays the calls to your channel.

### 1. Create the bot

1. Register or sign in at **https://eitaayar.ir**.
2. Create a bot for your account. EitaaYar shows its token in the panel; the
   shape is `123456:abcdefghijklmnopqrstuvwxyz`.
3. Add the bot to the Eitaa channel and make it an administrator so it may
   post.

### 2. Verify before touching the stack

```bash
TOKEN='123456:abcdefghijklmnopqrstuvwxyz'
CHANNEL='@your_channel'

curl -s "https://eitaayar.ir/api/${TOKEN}/getMe"
curl -s -X POST "https://eitaayar.ir/api/${TOKEN}/sendMessage" \
  -H 'Content-Type: application/json' \
  -d "{\"chat_id\":\"${CHANNEL}\",\"text\":\"Stack test\"}"
```

The stack uses two calls on this gateway: `sendMessage` (`chat_id`, `text`)
for text-only drafts and `sendFile` (`chat_id`, `file`, `caption`) for photos
and videos. If the gateway refuses the JSON body, the adapter retries the call
as form data automatically, so the same configuration covers both behaviours.

### 3. Configure the stack

```bash
CONTENT_EITAA_TOKEN=123456:abcdefghijklmnopqrstuvwxyz
CONTENT_EITAA_CHAT_ID=@your_channel
CONTENT_EITAA_API_BASE=https://eitaayar.ir/api
```

## Apply and check

```bash
cd /root/content-manager
docker compose up -d content-bot
docker exec content-bot sh -c 'echo $CONTENT_BALE_CHAT_ID $CONTENT_EITAA_CHAT_ID'
docker logs content-bot --tail 20
```

Open a draft in Telegram: **More platforms...** now shows `Bale (auto)` and
`Eitaa (auto)`. Picking one publishes the post and answers `Published to
Bale.` / `Published to Eitaa.`; the draft records the target, shows it as
`/ sent`, and refuses a second publish to the same channel.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Button reads `Bale (no token)` | Token or chat id is empty in the running container | Set both variables, `docker compose up -d content-bot` |
| `403` on `getMe` | Wrong or revoked token | Re-issue the token with the bot's owner account |
| `400 chat not found` | Wrong channel id, or the bot is not a member | Use `@username`, add the bot as an administrator |
| `HTTP 401` on Eitaa | Token not activated in the EitaaYar panel | Finish the bot setup there and copy the token again |
| Media not delivered | The messenger rejects the format or the size | Send the draft as text, or attach a smaller photo/video |
| `network error` in the log | The container cannot reach the messenger | Check the egress route; both hosts answer over HTTPS only |

A failed publish never drops the draft, so the post can be retried, sent to
another channel, or published to Telegram meanwhile.

## Security

- Both tokens are passwords: keep them in `.env` (never in git) and rotate
  them if they leak.
- The bots only need posting rights; do not grant more permissions than that.
- `./manage.sh backup-sections` includes `.env`, so a restored stack keeps the
  channel configuration without retyping the tokens.
