#!/usr/bin/env python3
"""Tests for tg-bot.py, tg-ask.py, tg-stop.py against a fake Telegram server. Run: python3 tests/test_hooks.py

Never calls the real API or reads the real token: TG_API_BASE points at a local server and the
token is a fake env value.
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "telegram-hook", "scripts")
CHAT = 4242
T = tempfile.mkdtemp()
IDLE = os.path.join(T, "idle")
calls, updates, lock = [], [], threading.Lock()
cfg = {"fail_rich": False, "next_id": 100, "update_id": 1}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        method = self.path.rsplit("/", 1)[-1]
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        body = json.loads(raw) if self.headers.get("Content-Type") == "application/json" else {"multipart": True}
        result, ok = True, True
        if method == "getUpdates":
            deadline = time.time() + 0.5
            while time.time() < deadline:
                with lock:
                    pending = [u for u in updates if u["update_id"] >= body.get("offset", 0)]
                if pending:
                    break
                time.sleep(0.05)
            result = pending
        else:
            with lock:
                calls.append((method, body))
                if method in ("sendRichMessage", "sendMessage"):
                    if method == "sendRichMessage" and cfg["fail_rich"]:
                        ok = False
                    else:
                        cfg["next_id"] += 1
                        result = {"message_id": cfg["next_id"]}
        out = json.dumps({"ok": ok, "result": result} if ok else {"ok": False, "description": "Bad Request"}).encode()
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            self.wfile.write(out)
        except (BrokenPipeError, ConnectionResetError):
            pass  # a poller the test terminated mid-request


server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
ENV = {**os.environ, "TMPDIR": T, "TG_API_BASE": f"http://127.0.0.1:{server.server_port}",
       "TELEGRAM_BOT_TOKEN": "fake-token", "TELEGRAM_CHAT_ID": str(CHAT), "TG_USER_NAME": "Alex", "TG_AWAY_DELAY": "1",
       "TG_REPLY_WINDOW": "15", "TG_POLL_LINGER": "3", "TG_AWAY_FAKE_IDLE": IDLE, "TG_AWAY_FAKE_PLATFORM": "darwin",
       "CLAUDE_PROJECT_DIR": "/x/myproj", "CLAUDE_CODE_ENTRYPOINT": "cli"}
for k in ("CLAUDE_CODE_HOST_SESSION_ID", "CLAUDE_CODE_REMOTE", "CONDUCTOR_WORKSPACE_NAME",
          "CLAUDE_PLUGIN_OPTION_BOT_TOKEN", "CLAUDE_PLUGIN_OPTION_CHAT_ID", "CLAUDE_PLUGIN_OPTION_USER_NAME",
          "CLAUDE_PLUGIN_OPTION_AWAY_DELAY", "CLAUDE_PLUGIN_OPTION_REPLY_WINDOW"):
    ENV.pop(k, None)
passed, failed = 0, 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL {name}: {detail}")


def set_idle(n):
    open(IDLE, "w").write(str(n))


def push(update):
    with lock:
        update["update_id"] = cfg["update_id"]
        cfg["update_id"] += 1
        updates.append(update)


def button(mid, data, chat=CHAT):
    push({"callback_query": {"id": f"cb{cfg['update_id']}", "from": {"id": chat}, "data": data,
                             "message": {"message_id": mid, "chat": {"id": chat}}}})


def reply(mid, text, chat=CHAT):
    push({"message": {"message_id": 9000 + cfg["update_id"], "from": {"id": chat}, "chat": {"id": chat},
                      "date": int(time.time()), "text": text,
                      "reply_to_message": {"message_id": mid, "from": {"is_bot": True}}}})


def sent_since(n, method=None):
    with lock:
        return [c for c in calls[n:] if method is None or c[0] == method]


def start(script, payload, env=None):
    p = subprocess.Popen([sys.executable, os.path.join(HOOKS, script)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**ENV, **(env or {})}, text=True)
    p.stdin.write(json.dumps(payload))
    p.stdin.close()
    p.stdin = None
    return p


def ping_id(n, timeout=10):
    """message_id of the ping the hook sent after call index n."""
    end = time.time() + timeout
    while time.time() < end:
        with lock:
            got = [c for c in calls[n:] if c[0] in ("sendRichMessage", "sendMessage")]
            mid = cfg["next_id"]
        if got and os.path.exists(os.path.join(T, "claude-telegram-hook", "waits", f"{mid}.json")):
            return mid, got[-1]
        time.sleep(0.1)
    return None, None


def finish(p, timeout=20):
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        return None, "", "timeout"
    return p.returncode, out, err


def ask(questions, actions, env=None):
    """Run tg-ask.py on AskUserQuestion, perform actions(mid), return (code, parsed stdout or raw)."""
    n = len(calls)
    p = start("tg-ask.py", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "sq",
                            "tool_input": {"questions": questions}}, env)
    mid, msg = ping_id(n)
    if mid:
        actions(mid)
    code, out, err = finish(p)
    try:
        return code, json.loads(out), msg
    except ValueError:
        return code, out, msg


Q1 = {"question": "Which delay?", "options": [{"label": "Short", "description": "5 min"}, {"label": "Long"}]}
Q2 = {"question": "Which color?", "options": [{"label": "Red"}, {"label": "Blue"}]}
QM = {"question": "Which parts?", "multiSelect": True, "options": [{"label": "A"}, {"label": "B"}, {"label": "C"}]}
set_idle(999)

# single question, button
n_close = len(calls)
code, out, msg = ask([Q1], lambda m: button(m, "q0o1"))
spec = out.get("hookSpecificOutput", {}) if isinstance(out, dict) else {}
check("single: allow", spec.get("permissionDecision") == "allow" and spec.get("hookEventName") == "PreToolUse", out)
check("single: answer", spec.get("updatedInput", {}).get("answers") == {"Which delay?": "Long"}, out)
check("single: questions echoed", spec.get("updatedInput", {}).get("questions") == [Q1], out)
kb = (msg or ("", {}))[1].get("reply_markup", {}).get("inline_keyboard", [])
check("buttons: options + app", [r[0]["text"] for r in kb] == ["Short", "Long", "Answer in app"], kb)
check("ping heading", "### Claude has a question" in (msg or ("", {}))[1].get("rich_message", {}).get("markdown", ""), msg)
edits = [b for m, b in sent_since(n_close, "editMessageReplyMarkup")]
reacts = [b for m, b in sent_since(n_close, "setMessageReaction")]
check("answered: buttons removed", edits and edits[-1].get("reply_markup") == {"inline_keyboard": []}
      and edits[-1].get("message_id") == cfg["next_id"], edits)
check("answered: thumbs-up", reacts and reacts[-1].get("reaction") == [{"type": "emoji", "emoji": "\U0001F44D"}], reacts)

# two questions: second answered by button, first by text reply (as Other)
code, out, msg = ask([Q1, Q2], lambda m: (button(m, "q1o0"), time.sleep(0.5), reply(m, "Ten minutes")))
ans = out.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers") if isinstance(out, dict) else None
check("multi-question + text as Other", ans == {"Which color?": "Red", "Which delay?": "Ten minutes"}, out)
kb = (msg or ("", {}))[1].get("reply_markup", {}).get("inline_keyboard", [])
check("numbered buttons", kb and kb[0][0]["text"] == "1. Short" and kb[2][0]["text"] == "2. Red", kb)

# multiSelect toggles + Done
n0 = len(calls)
code, out, msg = ask([QM], lambda m: [button(m, d) or time.sleep(0.3) for d in ("q0o0", "q0o2", "q0o0", "q0o1", "d0")])
ans = out.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers") if isinstance(out, dict) else None
check("multiSelect with Done", ans == {"Which parts?": "B, C"}, out)
md_ = (msg or ("", {}))[1].get("rich_message", {}).get("markdown", "")
check("multiSelect checkboxes", "- [ ] **A**" in md_, md_)
toasts = [b.get("text") for m, b in sent_since(n0, "answerCallbackQuery")]
check("toggle toast shows selection", "Selected: A, C" in toasts and "Selected: B, C" in toasts, toasts)

# Answer in app
n_close = len(calls)
code, out, msg = ask([Q1], lambda m: button(m, "app"))
check("answer in app: exit 0, no output", code == 0 and out == "", (code, out))
check("answer in app: buttons removed, no reaction", sent_since(n_close, "editMessageReplyMarkup")
      and not sent_since(n_close, "setMessageReaction"), sent_since(n_close))

# other chat ignored, then the real answer
n0 = len(calls)
code, out, msg = ask([Q1], lambda m: (button(m, "q0o0", chat=777), reply(m, "hacked", chat=777), time.sleep(1),
                                      button(m, "q0o1")))
ans = out.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers") if isinstance(out, dict) else None
check("other chat ignored", ans == {"Which delay?": "Long"}, out)
check("no toast for other chat", len(sent_since(n0, "answerCallbackQuery")) == 1, sent_since(n0, "answerCallbackQuery"))

# Mac input while waiting
code, out, msg = ask([Q1], lambda m: (time.sleep(0.5), set_idle(0)))
check("mac input: exit 0, no output", code == 0 and out == "", (code, out))
set_idle(999)

# at the Mac at start: no ping
n0 = len(calls)
set_idle(5)
p = start("tg-ask.py", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {"questions": [Q1]}})
code, out, _ = finish(p, 5)
check("at Mac at start: silent", code == 0 and out == "" and not sent_since(n0), (code, out))
set_idle(999)

# no idle reader (Windows, headless): immediate exit, nothing sent
n0, t0 = len(calls), time.time()
p = start("tg-ask.py", {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}},
          {"TG_AWAY_FAKE_PLATFORM": "win32"})
code, out, _ = finish(p, 5)
check("no idle reader: exit 0 at once", code == 0 and out == "" and time.time() - t0 < 2 and not sent_since(n0), (code, out))
p = start("tg-stop.py", {"hook_event_name": "Stop", "last_assistant_message": "Go?"}, {"TG_AWAY_FAKE_PLATFORM": "win32"})
code, out, _ = finish(p, 5)
check("no idle reader: tg-stop exits 0", code == 0 and not sent_since(n0), code)

# Linux idle readers: xprintidle first, then GNOME Mutter over gdbus, else None
fakebin = os.path.join(T, "bin")
os.makedirs(fakebin, exist_ok=True)
IDLE_PY = ("import importlib, sys; sys.path.insert(0, sys.argv[1]); "
           "print(importlib.import_module('tg-bot').idle_seconds())")


def linux_idle(tools):
    for name in ("xprintidle", "gdbus"):
        path = os.path.join(fakebin, name)
        if os.path.exists(path):
            os.remove(path)
        if name in tools:
            open(path, "w").write(f"#!/bin/sh\necho '{tools[name]}'\n")
            os.chmod(path, 0o755)
    env = {**ENV, "TG_AWAY_FAKE_PLATFORM": "linux", "PATH": fakebin}
    env.pop("TG_AWAY_FAKE_IDLE")
    return subprocess.run([sys.executable, "-c", IDLE_PY, HOOKS], env=env, capture_output=True, text=True).stdout.strip()


check("linux: xprintidle ms", linux_idle({"xprintidle": "5300", "gdbus": "(uint64 9000,)"}) == "5", "")
check("linux: gdbus Mutter ms", linux_idle({"gdbus": "(uint64 7000,)"}) == "7", "")
check("linux: no reader = None", linux_idle({}) == "None", "")

# permission: text reply -> deny with message; Allow button -> allow
n = len(calls)
perm = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "session_id": "sp",
        "tool_input": {"command": "make deploy", "description": "Deploy"}}
p = start("tg-ask.py", perm)
mid, msg = ping_id(n)
reply(mid, "not now")
code, out, _ = finish(p)
check("permission text -> deny", json.loads(out or "{}") == {"hookSpecificOutput": {
    "hookEventName": "PermissionRequest", "decision": {"behavior": "deny", "message": "Alex replied in Telegram: not now"}}},
      out)
check("claim written", os.path.exists(os.path.join(T, "claude-telegram-hook", "claims", "sp")), "no claim")
n = len(calls)
p = start("tg-ask.py", perm)
mid, msg = ping_id(n)
button(mid, "allow")
code, out, _ = finish(p)
check("permission allow", json.loads(out or "{}").get("hookSpecificOutput", {}).get("decision") == {"behavior": "allow"}, out)
check("permission buttons", [r[0]["text"] for r in msg[1]["reply_markup"]["inline_keyboard"]] == ["Allow", "Deny", "Answer in app"], msg)
p = start("tg-ask.py", {**perm, "tool_name": "AskUserQuestion"})
code, out, _ = finish(p, 5)
check("PermissionRequest ignores AskUserQuestion", code == 0 and out == "", out)

# plan: body is the plan, Deny -> PreToolUse deny with reason
n = len(calls)
plan = {"plan": "# Plan\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] step", "planFilePath": "/x/plan.md"}
p = start("tg-ask.py", {"hook_event_name": "PreToolUse", "tool_name": "ExitPlanMode", "tool_input": plan})
mid, msg = ping_id(n)
button(mid, "deny")
code, out, _ = finish(p)
spec = json.loads(out or "{}").get("hookSpecificOutput", {})
check("plan deny", spec.get("permissionDecision") == "deny" and spec.get("permissionDecisionReason"), out)
check("plan body raw", "| 1 | 2 |" in msg[1]["rich_message"]["markdown"], msg)

# fallback plain send keeps reply_markup
cfg["fail_rich"] = True
n = len(calls)
p = start("tg-ask.py", {**perm, "session_id": "sf"})
mid, msg = ping_id(n)
button(mid, "allow")
code, out, _ = finish(p)
rich = [b for m, b in sent_since(n, "sendRichMessage")]
plain = [b for m, b in sent_since(n, "sendMessage")]
check("fallback keeps reply_markup", rich and plain and plain[0].get("reply_markup") == rich[0].get("reply_markup")
      and "text" in plain[0], (rich, plain))
check("fallback message_id used", "allow" in (out or ""), out)
cfg["fail_rich"] = False

# expired reply: only for a message that was a ping
STATE = os.path.join(T, "claude-telegram-hook")
pinged_path = os.path.join(STATE, "pinged.json")
pinged = json.load(open(pinged_path)) if os.path.exists(pinged_path) else []
json.dump(pinged + [55555], open(pinged_path, "w"))
n = len(calls)
subprocess.Popen([sys.executable, os.path.join(HOOKS, "tg-bot.py"), "poll"], env=ENV)
time.sleep(1.1)
reply(66666, "reply to another alert")
reply(55555, "late answer")
end = time.time() + 8
while time.time() < end and not sent_since(n, "sendMessage"):
    time.sleep(0.2)
texts = [b.get("text") for m, b in sent_since(n, "sendMessage")]
check("expired reply answered", "This ping has expired; open the session to answer." in texts, texts)
check("reply to a non-ping: no expired note", len(texts) == 1, texts)

# a killed hook's ping counts as closed: tap gets "Expired", wait file removed
dead = subprocess.Popen(["true"]); dead.wait()
json.dump({"session": "dead", "kind": "question", "questions": [], "opened": time.time(),
           "expires": time.time() + 60, "pid": dead.pid}, open(os.path.join(STATE, "waits", "77777.json"), "w"))
n = len(calls)
subprocess.Popen([sys.executable, os.path.join(HOOKS, "tg-bot.py"), "poll"], env=ENV)
button(77777, "q0o0")
end = time.time() + 8
while time.time() < end and not sent_since(n, "answerCallbackQuery"):
    time.sleep(0.2)
toasts = [b.get("text") for m, b in sent_since(n, "answerCallbackQuery")]
check("killed hook: tap says Expired", toasts == ["Expired"], toasts)
check("killed hook: wait removed", not os.path.exists(os.path.join(STATE, "waits", "77777.json")), "")

# Esc / closed session: SIGTERM closes the ping and the claim
set_idle(100)
tr_sig = os.path.join(T, "sig.jsonl")
open(tr_sig, "w").write("")
n = len(calls)
p = start("tg-ask.py", {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "session_id": "sig",
                        "transcript_path": tr_sig, "tool_input": {"questions": [
                            {"question": "Sig?", "header": "S", "options": [{"label": "A"}, {"label": "B"}]}]}})
mid, msg = ping_id(n)
p.send_signal(signal.SIGTERM)
code, out, err = finish(p, 10)
check("SIGTERM: exit 0, no decision", code == 0 and out == "", (code, out, err))
check("SIGTERM: ping closed", mid and not os.path.exists(os.path.join(STATE, "waits", f"{mid}.json")), mid)
check("SIGTERM: claim released", not os.path.exists(os.path.join(STATE, "claims", "sig")), "")
check("SIGTERM: buttons removed", any(b.get("message_id") == mid for m, b in sent_since(n, "editMessageReplyMarkup")), mid)

# only one poller
lockfile = os.path.join(T, "claude-telegram-hook", "poller.lock")
try:
    os.kill(int(open(lockfile).read().strip() or 0), signal.SIGTERM)  # the poller left by earlier cases
except (OSError, ValueError):
    pass
time.sleep(0.5)
first = subprocess.Popen([sys.executable, os.path.join(HOOKS, "tg-bot.py"), "poll"], env={**ENV, "TG_POLL_LINGER": "30"})
time.sleep(1)
t0 = time.time()
second = subprocess.run([sys.executable, os.path.join(HOOKS, "tg-bot.py"), "poll"], env=ENV, timeout=10)
check("second poller exits at once", time.time() - t0 < 3 and first.poll() is None, "")
check("lock names first poller", open(lockfile).read().strip() == str(first.pid), open(lockfile).read())
first.terminate()
first.wait()

# tg-stop: reply wakes Claude
tr = os.path.join(T, "stop.jsonl")
open(tr, "w").write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "x"}]}}) + "\n")
n = len(calls)
long_q = "| a | b |\n|---|---|\n| 1 | 2 |\n\nShall I deploy **now**?"
p = start("tg-stop.py", {"hook_event_name": "Stop", "session_id": "ss", "transcript_path": tr,
                         "stop_hook_active": True, "last_assistant_message": long_q})
mid, msg = ping_id(n)
reply(mid, "yes, go")
code, out, err = finish(p)
check("tg-stop exits 2", code == 2, (code, err))
check("tg-stop note", err.startswith("Alex replied in Telegram (") and 'to your message "| a | b |' in err
      and err.rstrip().endswith(":\nyes, go"), err)
check("tg-stop body not escaped", msg and "| 1 | 2 |" in msg[1]["rich_message"]["markdown"], msg)
n = len(calls)
p = start("tg-stop.py", {"hook_event_name": "Stop", "transcript_path": tr, "last_assistant_message": "Done."})
code, out, err = finish(p, 5)
check("tg-stop: no question, silent", code == 0 and not sent_since(n), code)

# not configured: silent, and a token in an error never reaches the log
n0, t0 = len(calls), time.time()
p = start("tg-ask.py", {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "ls"}},
          {"TELEGRAM_CHAT_ID": ""})
code, out, _ = finish(p, 5)
check("not configured: exit 0 at once", code == 0 and out == "" and time.time() - t0 < 2 and not sent_since(n0),
      (code, out))
subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                "importlib.import_module('tg-bot').log('t', 'error at /botfake-token/sendMessage')", HOOKS], env=ENV)
logged = open(os.path.join(STATE, "hooks.log")).read()
check("token redacted in log", "fake-token" not in logged and "/bot<token>/" in logged, logged[-200:])

# plugin options win over env vars; delays are clamped to 600
out = subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                      "b = importlib.import_module('tg-bot'); print(b.CHAT, b.DELAY, b.USER, b.bot_token())", HOOKS],
                     env={**ENV, "CLAUDE_PLUGIN_OPTION_CHAT_ID": "1", "CLAUDE_PLUGIN_OPTION_AWAY_DELAY": "9000",
                          "CLAUDE_PLUGIN_OPTION_BOT_TOKEN": "t2"}, capture_output=True, text=True).stdout.split()
check("plugin options win, delay clamped", out == ["1", "600", "Alex", "t2"], out)

# cleanup: stop any poller left
try:
    os.kill(int(open(lockfile).read().strip() or 0), signal.SIGTERM)
except (OSError, ValueError):
    pass
print(f"tg-bot: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
