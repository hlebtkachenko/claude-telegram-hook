#!/usr/bin/env python3
"""StopFailure hook: tell the user on Telegram when a turn died on an API error.

A turn that ends on an API error fires no Notification, so an unattended session would stop silently.
Main session only (a subagent's failure carries agent_id; the main session reports it).
- Errors only the user can clear (auth, billing, account, org, cloud credentials, model): always ping,
  at most once per error type per hour across all sessions.
- Everything else (rate limit, overload, server error, ...): ping only when the user is away (idle at least
  TG_AWAY_DELAY seconds; no idle reader = skip), once per session per hour, so parallel sessions hitting
  one rate limit do not flood the chat.
StopFailure ignores hook output, so this only has side effects.
"""
import importlib
import json
import os
import re
import sys
import time

bot = importlib.import_module("tg-bot")
BLOCKERS = {"authentication_failed", "billing_error", "account_on_hold", "oauth_org_not_allowed",
            "cloud_credential_error", "model_not_found"}


def fresh(key):
    """True if `key` was not pinged in the last hour; records it."""
    path = bot.state("failures", re.sub(r"[^\w-]", "_", key))
    if time.time() - bot.mtime(path) < 3600:
        return False
    open(path, "w").close()
    return True


def main():
    data = json.load(sys.stdin)
    if data.get("agent_id") or not bot.bot_token() or not bot.CHAT:
        return  # a subagent failed, or not configured
    error = data.get("error") or "unknown"
    who = f"tg-failure {data.get('session_id', '')[:8]} {error}"
    if error in BLOCKERS:
        if not fresh(f"blocker-{error}"):
            return bot.log(who, "skip: pinged this error in the last hour")
    else:
        idle = bot.idle_seconds()
        if idle is None or idle < bot.DELAY:
            return bot.log(who, f"skip: idle={idle} (no idle reader, or user at the computer)")
        session = re.sub(r"[^\w-]", "", data.get("session_id", "")) or "unknown"
        if not fresh(f"session-{session}"):
            return bot.log(who, "skip: pinged this session in the last hour")
    detail = " ".join((data.get("error_details") or data.get("last_assistant_message") or "").split())
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    lines = bot.header("Claude stopped on an API error", cwd)
    lines.append(f"**{bot.md(error)}**" + (f": {bot.md(bot.cut(detail, 300))}" if detail else ""))
    if error in BLOCKERS:
        lines += ["", "Needs you: other sessions are likely blocked too."]
    mid = bot.send("\n".join(lines).strip(), [])
    bot.log(who, f"pinged: message {mid}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        bot.log("tg-failure", f"error: {type(e).__name__}: {e}")  # never disturb the session
