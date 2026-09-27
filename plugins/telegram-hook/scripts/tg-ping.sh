#!/bin/bash
# Send a Telegram message from the configured bot.
# Usage: printf '%s' "text" | bash tg-ping.sh    (text on stdin: stays out of the process list)
#    or: bash tg-ping.sh "text"                   (text in argv: visible to other local processes)
# tg-away.py uses it for its pings; you can also call it from your own scripts.
# Text is Markdown, sent as a Telegram rich message (sendRichMessage, Bot API 10.1+); if
# Telegram rejects it, the same text goes out as a plain message. Link previews are off.
# TG_PING_RAW=1: text is a complete message (tg-away.py builds its own header); otherwise
# the project name is prepended.
# Credentials: CLAUDE_PLUGIN_OPTION_BOT_TOKEN / _CHAT_ID, else TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.
# Claude Code does not pass the secret bot token to hooks, so tg-away.py sets TELEGRAM_BOT_TOKEN for this script.
# Test mode: TG_PING_DRYRUN_FILE=<file> appends the message and a "===" line there instead of sending.
if [ $# -gt 0 ]; then TEXT="$1"; else TEXT="$(cat)"; fi
[ -n "$TEXT" ] || { echo "usage: tg-ping.sh \"text\"  or  text on stdin" >&2; exit 2; }
if [ -n "${TG_PING_RAW:-}" ]; then MSG="$TEXT"; else MSG="**$(basename "${CLAUDE_PROJECT_DIR:-$PWD}")** · $TEXT"; fi
TOKEN="${CLAUDE_PLUGIN_OPTION_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
CHAT="${CLAUDE_PLUGIN_OPTION_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"
[ -n "$TOKEN" ] && [ -n "$CHAT" ] || { echo "tg-ping: bot token or chat id missing" >&2; exit 1; }
if [ -n "${TG_PING_DRYRUN_FILE:-}" ]; then printf '%s\n===\n' "$MSG" >>"$TG_PING_DRYRUN_FILE"; exit 0; fi
# URL (with token) goes to curl as a config file descriptor (printf is a builtin), the JSON body on stdin:
# neither the token nor the message is ever in a process's argv.
send() { # method; JSON body on stdin
  curl -fsS -o /dev/null --max-time 15 -K <(printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$TOKEN" "$1") \
    -H 'Content-Type: application/json' --data-binary @-
}
body() { # rich|plain; the message reaches python through the environment, not argv
  TG_PING_MSG="$MSG" TG_PING_CHAT="$CHAT" TG_PING_KIND="$1" python3 -c 'import json, os
m, c = os.environ["TG_PING_MSG"], os.environ["TG_PING_CHAT"]
print(json.dumps({"chat_id": c, "link_preview_options": {"is_disabled": True},
                  **({"rich_message": {"markdown": m}} if os.environ["TG_PING_KIND"] == "rich" else {"text": m})}))'
}
body rich | send sendRichMessage 2>/dev/null || body plain | send sendMessage || { echo "tg-ping: send failed" >&2; exit 1; }
