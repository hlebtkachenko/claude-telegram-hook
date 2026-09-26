#!/usr/bin/env python3
"""Notification hook: ping the user on Telegram when Claude waits for them and they are away.

Claude Code fires Notification when it needs a permission or an answer. This hook
starts a detached timer (TG_AWAY_DELAY seconds, default 600) and pings only if, when
it ends: the session transcript has not changed (nobody answered), the Mac has had no
keyboard or mouse input for as long (skipped on cloud VMs), and no newer wait replaced
this one. Only permission prompts and MCP elicitation forms ping here: tg-stop.py owns
finished turns (idle_prompt), and a permission prompt that tg-ask.py claimed is skipped
(sandbox network prompts skip PermissionRequest, so they still come through here).
Shared helpers live in tg-bot.py.

The message (Markdown, sent by tg-ping.sh as a Telegram rich message) names the app
(desktop, terminal, Conductor, cloud, VPS), the session title and project, then the
question with its options, or the tool waiting for permission, and ends with a
"Reply in Claude" link (claude.ai/code/<Remote Control id>) when the desktop app
has a Remote Control id for the session.
"""
import importlib
import json
import os
import re
import subprocess
import sys
import time

bot = importlib.import_module("tg-bot")
DELAY, STATE, HOOKS = bot.DELAY, bot.STATE, bot.HOOKS
KINDS = ("permission_prompt", "elicitation_dialog")  # idle_prompt belongs to tg-stop.py


def build_message(data, found):
    """Rich Markdown: what Claude waits for, session/app/project, divider, the ask, reply link."""
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    permission = data.get("notification_type") == "permission_prompt" and found["tool"] and not found["questions"]
    heading = ("Claude has a question" if found["questions"] else
               "Claude needs permission" if permission else "Claude is waiting for you")
    lines = bot.header(heading, cwd, found["title"])
    if found["questions"]:
        lines += bot.question_lines(found["questions"])
    elif permission:
        lines += bot.permission_lines(found["tool"])
    else:
        text = bot.cut(found["text"] or data.get("message", "Claude is waiting for you"), 800)
        lines += [">" + bot.md(line) for line in text.splitlines()] + [""]
    return "\n".join(lines + bot.reply_line()).strip()


def wait_and_ping(session, transcript, token, text, kind):
    time.sleep(DELAY)
    marker = os.path.join(STATE, session)
    try:
        if open(marker).read().split("\n")[0] != token:
            return  # a newer wait replaced this one
    except OSError:
        return
    if bot.mtime(transcript) > float(token.split(":")[1]) + 1:
        return  # the session moved on: the user answered
    if kind == "permission_prompt" and bot.claimed(session, float(token.split(":")[1])):
        return  # tg-ask.py pinged or is holding this prompt
    idle = bot.mac_idle_seconds()
    if idle is not None and idle < DELAY:
        return  # the user is at the Mac
    subprocess.run(["bash", os.path.join(HOOKS, "tg-ping.sh"), text], env={**os.environ, "TG_PING_RAW": "1"},
                   capture_output=True, timeout=30)


def main():
    if len(sys.argv) == 7 and sys.argv[1] == "--wait":
        wait_and_ping(*sys.argv[2:])
        return
    if bot.on_mac() is None:
        return  # pings only from the Mac: cloud and VPS sessions keep their normal prompts
    data = json.load(sys.stdin)
    kind = data.get("notification_type", "")
    transcript = data.get("transcript_path", "")
    session = re.sub(r"[^\w-]", "", data.get("session_id", "")) or "unknown"
    found = bot.read_transcript(transcript)
    if kind not in KINDS or (kind == "permission_prompt" and bot.claimed(session, time.time())):
        return
    started = bot.mtime(transcript) or time.time()
    text = build_message(data, found)
    bot.state()
    token = f"{os.getpid()}:{started}"
    with open(os.path.join(STATE, session), "w") as f:
        f.write(token)
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--wait", session, transcript, token, text, kind],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never disturb the session because the pinger failed
