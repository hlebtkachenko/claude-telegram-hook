#!/bin/bash
# Send a Telegram message from the configured bot. Usage: bash tg-ping.sh "text"
# tg-away.py uses it for its pings; you can also call it from your own scripts.
# Text is Markdown, sent as a Telegram rich message (sendRichMessage, Bot API 10.1+); if
# Telegram rejects it, the same text goes out as a plain message.
# TG_PING_RAW=1: text is a complete message (tg-away.py builds its own header); otherwise
# the project name is prepended.
# Credentials: plugin options (CLAUDE_PLUGIN_OPTION_BOT_TOKEN / _CHAT_ID, set in hook processes),
# else TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment.
# Test mode: TG_PING_DRYRUN_FILE=<file> appends the message and a "===" line there instead of sending.
TEXT="${1:?usage: tg-ping.sh \"text\"}"
if [ -n "${TG_PING_RAW:-}" ]; then MSG="$TEXT"; else MSG="**$(basename "${CLAUDE_PROJECT_DIR:-$PWD}")** · $TEXT"; fi
if [ -n "${TG_PING_DRYRUN_FILE:-}" ]; then printf '%s\n===\n' "$MSG" >>"$TG_PING_DRYRUN_FILE"; exit 0; fi
TOKEN="${CLAUDE_PLUGIN_OPTION_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
CHAT="${CLAUDE_PLUGIN_OPTION_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
[ -n "$TOKEN" ] && [ -n "$CHAT" ] || { echo "tg-ping: bot token or chat id missing" >&2; exit 1; }
# URL (with token) goes through curl's stdin config, never argv or the log.
send() { # method, JSON body
  printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$TOKEN" "$1" |
    curl -fsS -o /dev/null --max-time 15 -K - -H 'Content-Type: application/json' --data-binary "$2"
}
body() { python3 -c 'import json,sys; c,m,k=sys.argv[1:]; print(json.dumps({"chat_id":c, "link_preview_options":{"is_disabled":True}, **({"rich_message":{"markdown":m}} if k=="rich" else {"text":m})}))' "$CHAT" "$MSG" "$1"; }
send sendRichMessage "$(body rich)" 2>/dev/null || send sendMessage "$(body plain)" || { echo "tg-ping: send failed" >&2; exit 1; }
