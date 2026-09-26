#!/usr/bin/env python3
"""Stop hook (asyncRewake): when Claude ends a turn with a question and the user is away, ask them on Telegram.

Any other turn: listen silently (no message) up to TG_AWAY_DELAY + TG_REPLY_WINDOW seconds for a reply to an
earlier ping of this session; such a reply wakes Claude the same way. The transcript changing ends the listening.

On a desktop with an idle reader (macOS, Linux) only. Waits until the user has been idle TG_AWAY_DELAY seconds while the transcript stays
unchanged (desktop input only restarts the idle count), then pings if Claude's last message ends with
"?". Claude's message goes out as Markdown (tables, checkboxes, lists render), with the newest image
of the turn if there is one. A text reply to the ping within TG_REPLY_WINDOW seconds wakes Claude:
the note goes to stderr and the hook exits 2. Transcript change, "Answer in app" or timeout: exit 0.
"""
import importlib
import json
import os
import re
import sys
import time

bot = importlib.import_module("tg-bot")
SETTLE = 2  # ponytail: the transcript may still flush the final message right after Stop


def main():
    bot.exit_on_signals()
    data = json.load(sys.stdin)
    idle = bot.idle_seconds()
    if idle is None or not bot.bot_token() or not bot.CHAT:
        return 0  # no idle reader, or not configured
    transcript = data.get("transcript_path", "")
    who = f"tg-stop {data.get('session_id', '')[:8]}"
    text = (data.get("last_assistant_message") or bot.read_transcript(transcript)["text"]).strip()
    session = re.sub(r"[^\w-]", "", data.get("session_id", "")) or "unknown"
    if not text.endswith("?"):
        return listen(transcript, session, who)  # ponytail: "ends with ?" heuristic, a plain finished turn is not a question
    bot.log(who, f"hold: turn ended with a question, idle={idle}s")
    start = time.time()
    deadline = start + bot.DELAY + bot.REPLY_WINDOW + 60
    time.sleep(SETTLE)
    base = bot.mtime(transcript)
    while True:  # desktop input only restarts the idle count: ping once the user has been away DELAY seconds
        if bot.mtime(transcript) != base or time.time() > deadline:
            bot.log(who, "release: " + ("transcript changed" if bot.mtime(transcript) != base else "deadline"))
            return 0
        if (bot.idle_seconds() or 0) >= bot.DELAY:
            if deadline - time.time() < min(120, bot.REPLY_WINDOW):
                bot.log(who, "release: too close to the hook timeout to ping")
                return 0
            break
        time.sleep(2)
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    lines = bot.header("Claude is waiting for you", cwd, bot.read_transcript(transcript)["title"])
    body = "\n".join(lines + [bot.cut(text, bot.BODY_MAX), ""] + bot.reply_line()).strip()
    mid = bot.send(body, [("Answer in app", "app")], bot.latest_image(transcript))
    bot.log(who, f"pinged: message {mid}")
    reply = None
    try:
        bot.open_wait(mid, session, "stop", quote=text)
        bot.ensure_poller()

        def stop(evts):
            if bot.mtime(transcript) != base:
                return "moved"
            for e in evts:
                if e.get("data") == "app":
                    return "app"
                if bot.reply_text(e):
                    return e
            return None

        reply = bot.wait_for(mid, stop, min(time.time() + bot.REPLY_WINDOW, deadline))
    finally:
        bot.close_wait(mid)
        bot.close_ping(mid, isinstance(reply, dict))
    bot.log(who, "done: " + (reply if isinstance(reply, str) else "timeout" if reply is None else "reply, waking Claude"))
    if not isinstance(reply, dict):
        return 0
    return wake(reply, bot.cut(" ".join(text.split()), 120))


def wake(reply, quote):
    """The note Claude reads on stderr; exit 2 wakes it."""
    when = time.strftime("%H:%M", time.localtime(reply.get("date") or time.time()))
    sys.stderr.write(f"{bot.USER} replied in Telegram ({when}) to your message \"{quote}\":\n{bot.reply_text(reply)}\n")
    return 2


def listen(transcript, session, who):
    """Silent inbox: the poller routes a reply to any earlier ping of this session here, until the session moves on."""
    deadline = time.time() + bot.DELAY + bot.REPLY_WINDOW
    bot.open_inbox(session, deadline)
    try:
        time.sleep(SETTLE)
        base = bot.mtime(transcript)

        def stop(evts):
            if bot.mtime(transcript) != base:
                return "moved"
            return next((e for e in evts if bot.reply_text(e)), None)

        reply = bot.wait_for(f"inbox-{session}", stop, deadline)
    finally:
        bot.close_inbox(session)
    if not isinstance(reply, dict):
        return 0
    bot.log(who, "inbox: reply to an earlier ping, waking Claude")
    bot.close_ping(reply.get("ping"), True)
    return wake(reply, reply.get("quote", ""))


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        bot.log("tg-stop", f"error: {type(e).__name__}: {e}")
        code = 0  # never disturb the session because the pinger failed
    sys.exit(code)
