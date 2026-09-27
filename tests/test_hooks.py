#!/usr/bin/env python3
"""Tests for tg-bot.py, tg-ask.py, tg-stop.py against a fake Telegram server. Run: python3 tests/test_hooks.py

Never calls the real API or reads the real token: TG_API_BASE points at a local server and the
token is a fake env value.
"""
import glob
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
        elif method == "getFile":
            with lock:
                calls.append((method, body))
            result = {"file_id": body.get("file_id"), "file_path": "photos/file_7.jpg", "file_size": 8}
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


    def do_GET(self):
        with lock:
            calls.append(("GET", {"path": self.path}))
        ok = self.path == "/file/botfake-token/photos/file_7.jpg"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b"JPEGDATA" if ok else b"")


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


def reply_photo(mid, caption=None, chat=CHAT):
    msg = {"message_id": 9000 + cfg["update_id"], "from": {"id": chat}, "chat": {"id": chat}, "date": int(time.time()),
           "photo": [{"file_id": "small", "file_size": 2}, {"file_id": "big", "file_size": 8}],
           "reply_to_message": {"message_id": mid, "from": {"is_bot": True}}}
    if caption:
        msg["caption"] = caption
    push({"message": msg})


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
check("link previews off", (msg or ("", {}))[1].get("link_preview_options") == {"is_disabled": True}, msg)
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
pm = msg[1]["rich_message"]["markdown"] if msg else ""
check("bash: command headline, description below", "**Run: make deploy**\nDeploy" in pm and "truncated" not in pm, pm)
check("permission buttons", [r[0]["text"] for r in msg[1]["reply_markup"]["inline_keyboard"]] == ["Allow", "Deny", "Answer in app"], msg)
SUGG = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "make deploy"}], "behavior": "allow",
         "destination": "localSettings"}]
n = len(calls)
p = start("tg-ask.py", {**perm, "permission_suggestions": SUGG + [
    {"type": "setMode", "mode": "acceptEdits", "destination": "session"},
    {"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow", "destination": "projectSettings"},
    {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "rm *"}], "behavior": "deny",
     "destination": "session"}]})
mid, msg = ping_id(n)
button(mid, "always")
code, out, _ = finish(p)
check("always allow: addRules echoed, setMode dropped", json.loads(out or "{}").get("hookSpecificOutput", {}).get("decision") ==
      {"behavior": "allow", "updatedPermissions": SUGG}, out)
check("always allow: rules shown", "Always allow saves:\n- `Bash(make deploy) -> localSettings`"
      in (msg[1]["rich_message"]["markdown"] if msg else "") and "projectSettings" not in msg[1]["rich_message"]["markdown"], msg)
n = len(calls)
p = start("tg-ask.py", {**perm, "permission_suggestions": [
    {"type": "addRules", "rules": [{"toolName": "Bash"}], "behavior": "allow", "destination": "projectSettings"}]})
mid2, msg2 = ping_id(n)
button(mid2, "allow")
finish(p)
check("always allow: none left, no button", msg2 and [r[0]["text"] for r in msg2[1]["reply_markup"]["inline_keyboard"]] ==
      ["Allow", "Deny", "Answer in app"], msg2)
check("always allow button", [r[0]["text"] for r in msg[1]["reply_markup"]["inline_keyboard"]] ==
      ["Allow", "Always allow", "Deny", "Answer in app"], msg)
p = start("tg-ask.py", {**perm, "tool_name": "AskUserQuestion"})
code, out, _ = finish(p, 5)
check("PermissionRequest ignores AskUserQuestion", code == 0 and out == "", out)

# photo reply: downloaded (0600, largest size), path in the denial; photo-only reply answers a question
n = len(calls)
p = start("tg-ask.py", {**perm, "session_id": "sph"})
mid, msg = ping_id(n)
reply_photo(mid, "use this layout")
code, out, _ = finish(p)
deny = json.loads(out or "{}").get("hookSpecificOutput", {}).get("decision", {}).get("message", "")
fpath = deny.split("Attached: ")[-1]
check("photo: caption + Attached path", deny.startswith("Alex replied in Telegram: use this layout\nAttached: /")
      and fpath.endswith("-file_7.jpg"), deny)
check("photo: file saved 0600", os.path.exists(fpath) and open(fpath, "rb").read() == b"JPEGDATA"
      and os.stat(fpath).st_mode & 0o777 == 0o600, fpath)
check("photo: largest size fetched", [b.get("file_id") for m, b in sent_since(n, "getFile")] == ["big"], sent_since(n, "getFile"))
code, out, msg = ask([Q1], lambda m: reply_photo(m))
ans = out.get("hookSpecificOutput", {}).get("updatedInput", {}).get("answers") if isinstance(out, dict) else None
check("photo-only reply as Other", ans and ans["Which delay?"].startswith("Attached: /"), out)

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
check("fallback: no preview", plain and plain[0].get("link_preview_options") == {"is_disabled": True}, plain)
check("fallback keeps reply_markup", rich and plain and plain[0].get("reply_markup") == rich[0].get("reply_markup")
      and "text" in plain[0], (rich, plain))
check("fallback message_id used", "allow" in (out or ""), out)
cfg["fail_rich"] = False

# expired reply: only for a message that was a ping
STATE = os.path.join(T, "claude-telegram-hook")
pinged_path = os.path.join(STATE, "pinged.json")
pinged = json.load(open(pinged_path)) if os.path.exists(pinged_path) else {}
check("pinged.json maps id to session", any(v.get("session") == "sp" for v in pinged.values()), pinged)
json.dump({**pinged, "55555": {"session": "gone", "quote": "Old?"}}, open(pinged_path, "w"))
n = len(calls)
subprocess.Popen([sys.executable, os.path.join(HOOKS, "tg-bot.py"), "poll"], env=ENV)
time.sleep(1.1)
reply(66666, "reply to another alert")
reply(55555, "late answer")
end = time.time() + 8
while time.time() < end and not sent_since(n, "sendMessage"):
    time.sleep(0.2)
texts = [b.get("text") for m, b in sent_since(n, "sendMessage")]
check("not listening note: no preview", all(b.get("link_preview_options") == {"is_disabled": True}
                                           for m, b in sent_since(n, "sendMessage")), sent_since(n, "sendMessage"))
check("not listening reply answered", "This session is not listening now; open it to answer." in texts, texts)
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
p = start("tg-stop.py", {"hook_event_name": "Stop", "session_id": "sd", "transcript_path": tr, "last_assistant_message": "Done."})
time.sleep(3)
check("tg-stop: no question, inbox open", os.path.exists(os.path.join(STATE, "inbox", "sd.json")), "")
open(tr, "a").write("{}\n")  # the session moved on
code, out, err = finish(p, 10)
check("tg-stop: no question, silent; exits on transcript change", code == 0 and not sent_since(n, "sendRichMessage")
      and not sent_since(n, "sendMessage") and not os.path.exists(os.path.join(STATE, "inbox", "sd.json")), (code, err))

# reply to a past ping of a listening session wakes Claude (text or photo)
pinged = json.load(open(pinged_path))
json.dump({**pinged, "88888": {"session": "si", "quote": "Deploy now?"}}, open(pinged_path, "w"))
tr_i = os.path.join(T, "inbox.jsonl")
open(tr_i, "w").write("{}\n")
n = len(calls)
p = start("tg-stop.py", {"hook_event_name": "Stop", "session_id": "si", "transcript_path": tr_i,
                         "last_assistant_message": "All done."})
time.sleep(3)
reply_photo(88888, "go ahead")
code, out, err = finish(p)
check("inbox: exit 2", code == 2, (code, err))
check("inbox: note", err.startswith("Alex replied in Telegram (") and 'to your message "Deploy now?":\ngo ahead\nAttached: /' in err,
      err)
check("inbox: thumbs-up on the old ping", any(b.get("message_id") == 88888 for m, b in sent_since(n, "setMessageReaction")),
      sent_since(n))
check("inbox: no message sent", not sent_since(n, "sendRichMessage") and not sent_since(n, "sendMessage"), sent_since(n))

# StopFailure: blockers always (once per error type per hour), others only when away (once per session per hour)
def failure(payload, env=None):
    n = len(calls)
    p = start("tg-failure.py", {"hook_event_name": "StopFailure", "session_id": "sf1", **payload}, env)
    finish(p, 10)
    return [b for m, b in sent_since(n) if m in ("sendRichMessage", "sendMessage")]


set_idle(999)
got = failure({"error": "billing_error", "error_details": "Credit balance too low"})
check("failure: blocker pings, no buttons", len(got) == 1 and "billing\\_error" in got[0]["rich_message"]["markdown"]
      and "Needs you" in got[0]["rich_message"]["markdown"] and "reply_markup" not in got[0], got)
check("failure: blocker once per hour", not failure({"error": "billing_error"}, {"TG_AWAY_FAKE_PLATFORM": "win32"}), "")
check("failure: subagent skipped", not failure({"error": "model_not_found", "agent_id": "a1"}), "")
check("failure: other error, no idle reader: skip", not failure({"error": "rate_limit"}, {"TG_AWAY_FAKE_PLATFORM": "win32"}), "")
set_idle(0)
check("failure: other error at the computer: skip", not failure({"error": "rate_limit"}), "")
set_idle(999)
check("failure: other error when away pings", len(failure({"error": "rate_limit"})) == 1, "")
check("failure: once per session per hour", not failure({"error": "overloaded"}), "")

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
leaks = [f for f in glob.glob(os.path.join(STATE, "**", "*"), recursive=True)
         if os.path.isfile(f) and "fake-token" in (f + open(f, "rb").read().decode("utf-8", "replace"))]
check("token in no state file", not leaks, leaks)

# pairing: no chat ID -> SessionStart note + one pairing poller that tells a private sender their own chat ID
try:
    os.kill(int(open(lockfile).read().strip() or 0), signal.SIGTERM)  # no second getUpdates reader
except (OSError, ValueError):
    pass
n = len(calls)
PAIR_ENV = {"TELEGRAM_CHAT_ID": "", "TG_PAIR_SECONDS": "6"}
outs = [finish(start("tg-pair.py", {"hook_event_name": "SessionStart", "source": "startup"}, PAIR_ENV), 5)[1]
        for _ in range(2)]
pair_out = json.loads(outs[0] or "{}")
check("pairing: systemMessage + additionalContext", "chat ID" in pair_out.get("systemMessage", "") and
      pair_out.get("hookSpecificOutput", {}).get("hookEventName") == "SessionStart" and
      "private" in pair_out["hookSpecificOutput"].get("additionalContext", ""), outs[0])
time.sleep(1)
procs = subprocess.run(["pgrep", "-f", "tg-pair.py --pair"], capture_output=True, text=True).stdout.split()
check("pairing: one poller", len(procs) == 1, procs)
push({"message": {"message_id": 1, "from": {"id": -100}, "chat": {"id": -100, "type": "group"}, "text": "hi"}})
push({"message": {"message_id": 2, "from": {"id": 5555}, "chat": {"id": 5555, "type": "private"}, "text": "hi"}})
end = time.time() + 8
while time.time() < end and not sent_since(n, "sendMessage"):
    time.sleep(0.2)
pair_sent = [b for m, b in sent_since(n, "sendMessage")]
check("pairing: private sender gets own chat ID only", len(pair_sent) == 1 and pair_sent[0]["chat_id"] == 5555 and
      pair_sent[0]["text"].startswith("Your chat ID is 5555. Paste it into /plugin"), pair_sent)
check("pairing reply: no preview", pair_sent and pair_sent[0].get("link_preview_options") == {"is_disabled": True}, pair_sent)
check("pairing: configured = silent", finish(start("tg-pair.py", {"hook_event_name": "SessionStart"}), 5)[1] == "", "")
for pid in procs:
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass

# MCP server: newline-delimited JSON-RPC, one notify tool
def mcp(lines, env=None):
    p = subprocess.run([sys.executable, os.path.join(HOOKS, "tg-mcp.py")], input="".join(json.dumps(l) + "\n" for l in lines),
                       env={**ENV, **(env or {})}, capture_output=True, text=True, timeout=20)
    return [json.loads(l) for l in p.stdout.splitlines()]


n = len(calls)
res = mcp([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
           {"jsonrpc": "2.0", "method": "notifications/initialized"},
           {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
           {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "notify", "arguments": {"text": "Build *done*"}}},
           {"jsonrpc": "2.0", "id": 4, "method": "ping"},
           {"jsonrpc": "2.0", "id": 5, "method": "resources/list"}])
check("mcp: one reply per request", [r.get("id") for r in res] == [1, 2, 3, 4, 5], res)
check("mcp: protocolVersion echoed", res and res[0]["result"]["protocolVersion"] == "2025-11-25", res)
check("mcp: notify listed", len(res) > 1 and [t["name"] for t in res[1]["result"]["tools"]] == ["notify"]
      and "never for progress" in res[1]["result"]["tools"][0]["description"].lower(), res)
check("mcp: notify sent", len(res) > 2 and res[2]["result"]["content"][0]["text"] == "sent"
      and [b["rich_message"]["markdown"] for m, b in sent_since(n, "sendRichMessage")] == ["Build *done*"], res)
check("mcp: ping + unknown method", len(res) > 4 and res[3]["result"] == {} and res[4]["error"]["code"] == -32601, res)
res = mcp([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "notify", "arguments": {"text": "x"}}}],
          {"TELEGRAM_CHAT_ID": "${user_config.chat_id}"})
check("mcp: unset option -> error text", res and res[0]["result"]["isError"]
      and res[0]["result"]["content"][0]["text"].startswith("error: Telegram bot token or chat ID not configured"), res)

# state dir: a symlink (planted dir) is refused; the log is never written through it
evil_tmp, evil_target = os.path.join(T, "evil"), os.path.join(T, "evil-target")
os.makedirs(evil_tmp, exist_ok=True)
os.makedirs(evil_target, exist_ok=True)
if not os.path.islink(os.path.join(evil_tmp, "claude-telegram-hook")):
    os.symlink(evil_target, os.path.join(evil_tmp, "claude-telegram-hook"))
subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                "importlib.import_module('tg-bot').log('t', 'x')", HOOKS], env={**ENV, "TMPDIR": evil_tmp})
check("symlinked state dir refused", os.listdir(evil_target) == [], os.listdir(evil_target))
check("log is 0600", os.stat(os.path.join(STATE, "hooks.log")).st_mode & 0o777 == 0o600, "")

# TG_API_BASE: only a local URL; otherwise the real API and no TG_AWAY_FAKE_* overrides (nothing is called here)
for base, want in (("https://evil.example", "https://api.telegram.org False"),
                   ("http://127.0.0.1.evil.example", "https://api.telegram.org False"),
                   ("http://localhost:8080", "http://localhost:8080 True")):
    got = subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                          "b = importlib.import_module('tg-bot'); print(b.API, b.platform() == 'win32')", HOOKS],
                         env={**ENV, "TG_API_BASE": base, "TG_AWAY_FAKE_PLATFORM": "win32"},
                         capture_output=True, text=True).stdout.strip()
    check(f"api base {base}", got == want, got)

res = mcp([{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["notify"]},
           {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "notify", "arguments": "text"}},
           {"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": 7}},
           {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "notify", "arguments": {"text": 5}}},
           "not an object", {"jsonrpc": "2.0", "id": 5, "method": "ping"}])
check("mcp: malformed params answered, server keeps running", [r.get("id") for r in res] == [1, 2, 3, 4, 5]
      and res[0].get("error", {}).get("code") == -32602 and res[1]["result"]["isError"]
      and res[2]["result"]["protocolVersion"] == "2025-06-18" and res[3]["result"]["isError"] and res[4]["result"] == {}, res)

# plugin options win over env vars; delays are clamped to 600
out = subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                      "b = importlib.import_module('tg-bot'); print(b.CHAT, b.DELAY, b.USER, b.bot_token())", HOOKS],
                     env={**ENV, "CLAUDE_PLUGIN_OPTION_CHAT_ID": "1", "CLAUDE_PLUGIN_OPTION_AWAY_DELAY": "9000",
                          "CLAUDE_PLUGIN_OPTION_BOT_TOKEN": "t2"}, capture_output=True, text=True).stdout.split()
check("plugin options win, delay clamped", out == ["1", "600", "Alex", "t2"], out)

# token hand-over: the MCP server (which gets the secret option) saves it; hooks without it read the copy
tok_path = os.path.join(STATE, "bot_token")
check("mcp saved token 0600", os.path.exists(tok_path) and oct(os.stat(tok_path).st_mode & 0o777) == "0o600"
      and open(tok_path).read() == "fake-token", tok_path)
got = subprocess.run([sys.executable, "-c", "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                      "print(importlib.import_module('tg-bot').bot_token())", HOOKS],
                     env={**ENV, "TELEGRAM_BOT_TOKEN": ""}, capture_output=True, text=True).stdout.strip()
check("hook reads saved token when the option is not passed", got == "fake-token", got)
read_tok = ("import importlib, sys; sys.path.insert(0, sys.argv[1]); "
            "print(repr(importlib.import_module('tg-bot').bot_token()))")
open(tok_path, "wb").write(b"\xff\xfe")
got = subprocess.run([sys.executable, "-c", read_tok, HOOKS], env={**ENV, "TELEGRAM_BOT_TOKEN": ""},
                     capture_output=True, text=True)
check("non-text token file: empty, no crash", got.stdout.strip() == "''" and got.returncode == 0, got)
os.remove(tok_path)
os.symlink(os.path.join(T, "elsewhere"), tok_path)
open(os.path.join(T, "elsewhere"), "w").write("planted")
got = subprocess.run([sys.executable, "-c", read_tok, HOOKS], env={**ENV, "TELEGRAM_BOT_TOKEN": ""},
                     capture_output=True, text=True).stdout.strip()
check("symlinked token file ignored", got == "''", got)
os.remove(tok_path)

# cleanup: stop any poller left
try:
    os.kill(int(open(lockfile).read().strip() or 0), signal.SIGTERM)
except (OSError, ValueError):
    pass
print(f"tg-bot: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
