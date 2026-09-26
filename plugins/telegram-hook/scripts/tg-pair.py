#!/usr/bin/env python3
"""SessionStart hook: help the user find their chat ID when the bot token is set and chat_id is not.

Starts (once: fcntl lock STATE/pair.lock) a short-lived pairing poller that answers any private message to
the bot with the sender's own chat ID, and stops after TG_PAIR_SECONDS (default 600). It reveals nothing
else and never runs anything from Telegram. The hook tells the user and Claude to DM the bot.
Configured (or no token): does nothing.
"""
import fcntl
import importlib
import json
import os
import subprocess
import sys
import time

bot = importlib.import_module("tg-bot")
SECONDS = int(os.environ["TG_PAIR_SECONDS"]) if os.environ.get("TG_PAIR_SECONDS", "").isdigit() else 600
ANSWER = "Your chat ID is {}. Paste it into /plugin, telegram-hook, Configure options, chat_id."
NOTE = ("telegram-hook: the Telegram chat ID is not set. Send any message to your bot in a private Telegram chat "
        "within 10 minutes; it replies with your chat ID. Paste it into /plugin, telegram-hook, Configure options, "
        "chat_id, then start a new session.")


def pair():
    lock = os.open(bot.state("pair.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another pairing poller runs
    end = time.time() + SECONDS
    while time.time() < end:
        try:
            updates = bot.api("getUpdates", {"offset": bot.read_json(bot.state("offset"), 0), "allowed_updates": ["message"],
                                             "timeout": max(1, min(50, int(end - time.time())))}, timeout=65)
        except Exception:
            time.sleep(5)
            continue
        for upd in updates:
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            if chat.get("type") == "private" and chat.get("id") == (msg.get("from") or {}).get("id"):
                try:  # a private chat's ID is the sender's own user ID: nothing else is revealed
                    bot.api("sendMessage", {"chat_id": chat["id"], **bot.NO_PREVIEW, "text": ANSWER.format(chat["id"])})
                    bot.log("tg-pair", "answered a private message with its chat ID")
                except Exception:
                    pass
            bot.write_json(bot.state("offset"), upd["update_id"] + 1)


def main():
    if sys.argv[1:] == ["--pair"]:
        return pair()
    if not bot.bot_token() or bot.CHAT:
        return
    if not bot.poller_running("pair.lock"):
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--pair"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        bot.log("tg-pair", "pairing poller started")
    print(json.dumps({"systemMessage": NOTE, "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": NOTE + " If the user asks about Telegram pings, tell them this."}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        bot.log("tg-pair", f"error: {type(e).__name__}: {e}")  # never disturb the session
