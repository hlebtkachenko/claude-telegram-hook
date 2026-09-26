#!/usr/bin/env python3
"""Blocking hook: answer Claude's questions, plans and permission prompts from Telegram.

Runs as PreToolUse for AskUserQuestion and ExitPlanMode, and as PermissionRequest for every
other tool. On a desktop (macOS, Linux) where the user has been away MIN_IDLE seconds, it holds until they have been idle
TG_AWAY_DELAY seconds, pings them with option buttons, and waits TG_REPLY_WINDOW seconds for a
button press or a text reply to the ping. Any keyboard or mouse input, the "Answer in app" button, a timeout
or an error exit 0 with no output, so the normal dialog appears. Elsewhere it does nothing.
"""
import importlib
import json
import os
import re
import sys
import time

bot = importlib.import_module("tg-bot")

HEADINGS = {"question": "Claude has a question", "plan": "Claude wants to finish planning",
            "permission": "Claude needs permission"}


def buttons(kind, questions, always=False):
    if kind != "question":
        return [("Allow", "allow")] + ([("Always allow", "always")] if always else []) + [
            ("Deny", "deny"), ("Answer in app", "app")]
    rows, many = [], len(questions) > 1
    for qi, q in enumerate(questions):
        num = f"{qi + 1}. " if many else ""
        rows += [(num + o.get("label", ""), f"q{qi}o{oi}") for oi, o in enumerate(q.get("options") or [])]
        if q.get("multiSelect"):
            rows.append((num + "Done", f"d{qi}"))
    return rows + [("Answer in app", "app")]


def always_rules(data, event):
    """The permission_suggestions an "Always allow" tap may echo: addRules allow entries saved to the project's
    local settings or the session only. setMode, other behaviors, shared or user-wide destinations: dropped."""
    if event != "PermissionRequest":
        return []
    return [s for s in data.get("permission_suggestions") or [] if isinstance(s, dict) and s.get("type") == "addRules"
            and s.get("behavior") == "allow" and s.get("destination") in ("localSettings", "session")
            and isinstance(s.get("rules"), list) and s["rules"]]


def rule_lines(suggestions):
    """What "Always allow" saves, one `toolName(ruleContent) -> destination` per rule."""
    lines = []
    for s in suggestions:
        for rule in s["rules"]:
            rule = rule if isinstance(rule, dict) else {}
            content = f"({rule['ruleContent']})" if rule.get("ruleContent") else ""
            text = f"{rule.get('toolName', '?')}{content} -> {s['destination']}".replace("`", "'")
            lines.append(f"- `{bot.cut(text, 300)}`")
    return ["Always allow saves:"] + lines + [""] if lines else []


def message(data, kind, suggestions=()):
    inp = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    lines = bot.header(HEADINGS[kind], cwd, bot.read_transcript(data.get("transcript_path", ""))["title"])
    if kind == "question":
        lines += bot.question_lines(inp.get("questions") or [])
    elif kind == "plan":
        lines += [bot.cut(inp.get("plan", ""), bot.BODY_MAX), ""]
    else:
        lines += bot.permission_lines({"name": data.get("tool_name"), "input": inp}) + rule_lines(suggestions)
    return "\n".join(lines + bot.reply_line()).strip()


def decide(event, kind, inp, allow, reason="", permissions=None):
    """Decision JSON in the shape the hook event expects."""
    if event == "PermissionRequest":
        decision = {"behavior": "allow"} if allow else {"behavior": "deny", "message": reason}
        if allow and permissions:
            decision["updatedPermissions"] = permissions  # Claude Code's own suggestions, unchanged
        return {"hookSpecificOutput": {"hookEventName": event, "decision": decision}}
    out = {"hookEventName": event, "permissionDecision": "allow" if allow else "deny"}
    if allow:
        out["updatedInput"] = inp
    else:
        out["permissionDecisionReason"] = reason
    return {"hookSpecificOutput": out}


def resolve(event, kind, inp, evts, suggestions=None):
    """"app" to hand back to the dialog, the decision JSON once complete, else None (keep waiting)."""
    questions = inp.get("questions") or []
    answers = {}
    for n, e in enumerate(evts):
        data, text = e.get("data", ""), bot.reply_text(e)
        if data == "app":
            return "app"
        if kind != "question":
            if data == "always" and suggestions:
                return decide(event, kind, inp, True, permissions=suggestions)
            if data in ("allow", "deny"):
                return decide(event, kind, inp, data == "allow", f"{bot.USER} denied this in Telegram")
            if text:
                return decide(event, kind, inp, False, f"{bot.USER} replied in Telegram: {text}")
            continue
        m = re.fullmatch(r"([qd])(\d+)(?:o(\d+))?", data)
        if m and int(m.group(2)) < len(questions):
            qi = int(m.group(2))
            q = questions[qi]
            if m.group(1) == "d":
                labels = bot.selected(q, qi, evts[:n])
                if labels:
                    answers[q.get("question", "")] = ", ".join(labels)
            elif not q.get("multiSelect") and m.group(3) and int(m.group(3)) < len(q.get("options") or []):
                answers[q.get("question", "")] = q["options"][int(m.group(3))].get("label", "")
        elif text:
            open_q = [q for q in questions if q.get("question", "") not in answers]
            if open_q:
                answers[open_q[0].get("question", "")] = text  # free text, as "Other"
    if questions and all(q.get("question", "") in answers for q in questions):
        return decide(event, kind, {**inp, "answers": answers}, True)
    return None


def hold(presence, deadline):
    """True once the user has been idle DELAY seconds; False on desktop input or deadline."""
    while time.time() < deadline:
        idle = presence.idle()
        if idle is None:
            return False
        if idle >= bot.DELAY:
            return True
        time.sleep(1)
    return False


def main():
    bot.exit_on_signals()
    data = json.load(sys.stdin)
    event, tool = data.get("hook_event_name", ""), data.get("tool_name", "")
    if event == "PermissionRequest" and tool in ("AskUserQuestion", "ExitPlanMode"):
        return  # PreToolUse owns these
    who = f"tg-ask {data.get('session_id', '')[:8]} {tool}"
    kind = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}.get(tool, "permission")
    if not bot.bot_token() or not bot.CHAT:
        bot.log(who, "skip: bot token or chat ID not configured")
        return
    idle = bot.idle_seconds()
    if idle is None or idle < bot.MIN_IDLE:
        bot.log(who, f"skip: idle={idle} (no idle reader, or below {bot.MIN_IDLE}s: user at the computer)")
        return
    bot.log(who, f"hold: idle={idle}s, ping when idle>={bot.DELAY}s")
    start = time.time()
    deadline = start + bot.DELAY + bot.REPLY_WINDOW + 60  # hooks.json timeout is DELAY + REPLY_WINDOW + 120
    session = re.sub(r"[^\w-]", "", data.get("session_id", "")) or "unknown"
    inp = data.get("tool_input") or {}
    presence = bot.Presence(idle)
    bot.claim(session, deadline)
    mid, result = None, None
    try:
        if not hold(presence, deadline):
            bot.unclaim(session)  # the user is at the computer: tg-away.py may ping later if they leave
            bot.log(who, "release: desktop input or deadline while holding")
            return
        # "Always allow" echoes Claude Code's own suggestions (filtered, never built here), as "don't ask again" does
        suggestions = always_rules(data, event) or None
        mid = bot.send(message(data, kind, suggestions or ()), buttons(kind, inp.get("questions") or [], bool(suggestions)))
        bot.log(who, f"pinged: message {mid}")
        quote = ((inp.get("questions") or [{}])[0].get("question", "") if kind == "question" else
                 HEADINGS[kind] if kind == "plan" else bot.tool_ask({"name": tool, "input": inp})[0])
        bot.open_wait(mid, session, kind, inp.get("questions") or [], quote)
        bot.ensure_poller()

        def stop(evts):
            if presence.idle() is None:
                return "mac"
            return resolve(event, kind, inp, evts, suggestions)

        result = bot.wait_for(mid, stop, min(time.time() + bot.REPLY_WINDOW, deadline))
        bot.log(who, "done: " + ("desktop input" if result == "mac" else "timeout" if result is None
                                 else "answer in app" if result == "app" else "answered from Telegram"))
        if result == "mac":
            bot.unclaim(session)
    except bot.Interrupted as e:
        bot.log(who, f"interrupted ({e}): ping closed")
        bot.unclaim(session)
    finally:
        if mid is not None:
            bot.close_wait(mid)
            bot.close_ping(mid, isinstance(result, dict))
        if result not in (None, "mac"):
            bot.claim(session, time.time() + 30)  # the dialog may still notify: skip that ping
    if isinstance(result, dict):
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        bot.log("tg-ask", f"error: {type(e).__name__}: {e}")  # never disturb the session: exit 0, normal dialog
