#!/usr/bin/env python3
"""Telegram bot for Claude hooks: shared helpers, and the reply poller (`python3 tg-bot.py poll`).

Hooks (tg-away.py, tg-ask.py, tg-stop.py) import the helpers: session/app/project lines,
transcript reading, desktop idle time, sending a ping with inline buttons, and the waits registry.
A hook that pings and then waits for the user registers a wait (STATE/waits/<message_id>.json)
and starts the poller. The poller long-polls getUpdates, accepts only the configured chat, and writes
button presses and text replies to STATE/answers/<message_id>.json. It never runs anything
from Telegram input. One poller at a time (fcntl lock); it exits after TG_POLL_LINGER seconds
(default 60) with no open waits.
"""
import base64
import fcntl
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
import uuid



def opt(key, env, default=""):
    """Plugin option (CLAUDE_PLUGIN_OPTION_<KEY>, set by /plugin config), else a plain env var, else default."""
    for name in (f"CLAUDE_PLUGIN_OPTION_{key}", env):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return default


def seconds(key, env, default):
    """Bounded to 1..600 so DELAY + REPLY_WINDOW + 60 stays under the 1320 s hook timeout in hooks.json."""
    try:
        return max(1, min(600, int(float(opt(key, env, default)))))
    except (ValueError, OverflowError):
        return int(default)


DELAY = seconds("AWAY_DELAY", "TG_AWAY_DELAY", "600")
REPLY_WINDOW = seconds("REPLY_WINDOW", "TG_REPLY_WINDOW", "600")
LINGER = int(os.environ.get("TG_POLL_LINGER", "60")) if os.environ.get("TG_POLL_LINGER", "").isdigit() else 60
MIN_IDLE = 30  # below this at hook start the user is at the Mac: normal dialog, no ping
USER = opt("USER_NAME", "TG_USER_NAME", "The user")  # names the replier in notes Claude reads
STATE = os.path.join(os.environ.get("TMPDIR") or os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "claude-telegram-hook")


def log(who, msg):
    """One line per hook decision in STATE/hooks.log (no secrets), so a missing ping can be explained."""
    tok = bot_token()
    if tok:
        msg = msg.replace(tok, "<token>")  # an InvalidURL error quotes the request path
    try:
        os.makedirs(STATE, mode=0o700, exist_ok=True)
        path = os.path.join(STATE, "hooks.log")
        if os.path.exists(path) and os.path.getsize(path) > 1_000_000:
            os.replace(path, path + ".1")  # ponytail: one rotation, 2 MB max
        with open(path, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {who}[{os.getpid()}] {msg}\n")
    except OSError:
        pass
HOOKS = os.path.dirname(os.path.abspath(__file__))
API = os.environ.get("TG_API_BASE", "https://api.telegram.org")  # override: tests
CHAT = opt("CHAT_ID", "TELEGRAM_CHAT_ID")
BODY_MAX = 3000  # ponytail: fixed cut under Telegram's 4096-char plain limit
NOT_LISTENING = "This session is not listening now; open it to answer."
DESKTOP_SESSIONS = os.environ.get("TG_AWAY_DESKTOP_SESSIONS",  # override: tests
                                  os.path.expanduser("~/Library/Application Support/Claude/claude-code-sessions"))


# ---------- text and session helpers ----------

def md(text):
    """Escape Markdown so question text renders as typed."""
    return re.sub(r"([\\`*_\[\]#~>|])", r"\\\1", text)


def cut(text, n):
    text = text.strip()
    return text[:n] + ("..." if len(text) > n else "")


def read_transcript(transcript):
    """Last assistant text, pending AskUserQuestion questions, pending tool call, session title."""
    found = {"text": "", "questions": None, "tool": None, "title": ""}
    try:
        lines = open(transcript, encoding="utf-8").read().splitlines()
    except OSError:
        return found
    seen_assistant = False
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        kind = entry.get("type")
        if not found["title"] and kind in ("custom-title", "ai-title"):
            found["title"] = entry.get("customTitle") or entry.get("aiTitle") or ""
        msg = entry.get("message") or {}
        if not seen_assistant and kind == "assistant" and isinstance(msg.get("content"), list):
            for block in reversed(msg["content"]):
                if block.get("type") == "tool_use" and found["questions"] is None and found["tool"] is None:
                    if block.get("name") == "AskUserQuestion":
                        found["questions"] = block.get("input", {}).get("questions") or []
                    else:
                        found["tool"] = block
                if block.get("type") == "text" and block.get("text", "").strip():
                    found["text"] = block["text"].strip()
                    seen_assistant = True
                    break
        if seen_assistant and found["title"]:
            break
    return found


def latest_image(transcript):
    """(filename, mime, bytes) of the newest image since the user's last prompt, or None."""
    try:
        lines = open(transcript, encoding="utf-8").read().splitlines()
    except OSError:
        return None

    def images(blocks):
        for block in reversed(blocks if isinstance(blocks, list) else []):
            if not isinstance(block, dict):
                continue
            src = block.get("source") or {}
            if block.get("type") == "image" and src.get("type") == "base64":
                return src
            nested = images(block.get("content"))
            if nested:
                return nested
        return None

    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        content = (entry.get("message") or {}).get("content")
        src = images(content)
        if src:
            mime = src.get("media_type", "image/png")
            try:
                return "image." + mime.split("/")[-1], mime, base64.b64decode(src.get("data", ""))
            except ValueError:
                return None
        prompt = entry.get("type") == "user" and (isinstance(content, str) or any(
            isinstance(b, dict) and b.get("type") == "text" for b in (content or [])))
        if prompt:
            return None  # reached the start of the current turn
    return None


def desktop_session(host_id):
    """The desktop app's record of this session (title, Remote Control state)."""
    for path in glob.glob(os.path.join(DESKTOP_SESSIONS, "*", "*", f"{host_id}.json")):
        try:
            return json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return {}


def remote_link(host_id):
    """https link to the session in claude.ai / the Claude phone app (its latest Remote Control id)."""
    info = desktop_session(host_id) if host_id else {}
    ids = info.get("bridgeSessionIds") or []
    if ids and re.fullmatch(r"session_[A-Za-z0-9]+", ids[-1]):
        return f"https://claude.ai/code/{ids[-1]}"
    return ""


def where():
    """(app name, session title or "") for this session."""
    host_id = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", "")
    entry = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "")
    if os.environ.get("CLAUDE_CODE_REMOTE"):
        return "Cloud session", ""
    if os.environ.get("CONDUCTOR_WORKSPACE_NAME"):
        return "Conductor", os.environ["CONDUCTOR_WORKSPACE_NAME"]
    if entry == "claude-desktop" and re.fullmatch(r"local_[A-Za-z0-9-]{1,64}", host_id):
        return "Desktop app", desktop_session(host_id).get("title", "")
    return ("Terminal" if entry in ("", "cli") else entry), ""


def tool_ask(block):
    """(plain-English sentence of what the tool wants to do, raw detail to fold away or "")."""
    name, inp = block.get("name") or "a tool", block.get("input") or {}
    path = os.path.basename(inp.get("file_path") or inp.get("notebook_path") or "")
    if name == "Bash":
        return inp.get("description") or "Run a shell command", inp.get("command", "")
    if name in ("Edit", "MultiEdit", "NotebookEdit"):
        return f"Edit {path}", inp.get("file_path", "")
    if name == "Write":
        return f"Create or overwrite {path}", inp.get("file_path", "")
    if name == "Read":
        return f"Read {path}", inp.get("file_path", "")
    if name == "WebFetch":
        url = inp.get("url", "")
        return "Open " + re.sub(r"^https?://([^/]+).*", r"\1", url), url
    if name == "WebSearch":
        return f"Search the web for \"{inp.get('query', '')}\"", ""
    if name.startswith("mcp__"):
        server, _, tool = name[5:].partition("__")
        server = "" if re.fullmatch(r"[0-9a-f-]{36}", server) else f" ({server.replace('_', ' ')})"
        return f"Use {tool.replace('_', ' ').replace('-', ' ')}{server}", json.dumps(inp, ensure_ascii=False)
    return inp.get("description") or f"Use {name}", json.dumps(inp, ensure_ascii=False)


def project_label(cwd):
    """remote:repo/branch for a GitHub checkout, local:folder otherwise."""
    def git(*args):
        try:
            return subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""
    m = re.search(r"github\.com[:/][^/]+/([^/]+?)(?:\.git)?$", git("remote", "get-url", "origin"))
    if m:
        return f"remote:{m.group(1)}/{git('rev-parse', '--abbrev-ref', 'HEAD') or '?'}"
    return f"local:{os.path.basename(cwd.rstrip('/')) or cwd}"


def header(heading, cwd, title=""):
    """Heading, session/app/project lines, divider."""
    app, app_title = where()
    title = app_title or title
    info = ([md(title)] if title else []) + [md(app), md(project_label(cwd))]
    return [f"### {heading}", "\\\n".join(info), "", "---", ""]


def reply_line():
    link = remote_link(os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", ""))
    return [f"[Reply in Claude]({link})"] if link else []


def question_lines(questions):
    """Questions in bold, options numbered (single choice) or as checkboxes (multiSelect)."""
    lines, many = [], len(questions) > 1
    for n, q in enumerate(questions, 1):
        lines.append(f"**{f'{n}. ' if many else ''}{md(cut(q.get('question', ''), 300))}**")
        for i, opt in enumerate(q.get("options") or [], 1):
            desc = f" · {md(cut(opt.get('description', ''), 150))}" if opt.get("description") else ""
            mark = "- [ ] " if q.get("multiSelect") else f"{i}. "
            lines.append(f"{mark}**{md(opt.get('label', ''))}**{desc}")
        lines.append("")
    return lines


def permission_lines(block):
    ask, detail = tool_ask(block)
    lines = [f"**{md(cut(ask, 300))}**"]
    if detail and detail != "{}":
        lines += ["", "<details><summary>Details</summary>", "", "```",
                  cut(detail, 800).replace("```", "'''"), "```", "</details>"]
    return lines + [""]


# ---------- desktop presence ----------

def platform():
    return os.environ.get("TG_AWAY_FAKE_PLATFORM", sys.platform)  # override: tests


def run_ms(cmd):
    """Idle milliseconds printed by an idle-time reader (xprintidle, gdbus), as seconds; None if it fails."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    nums = re.findall(r"\d+", out.stdout) if out.returncode == 0 else []
    return int(nums[-1]) // 1000 if nums else None  # last number: gdbus prints "(uint64 1234,)"


def idle_seconds():
    """Keyboard/mouse idle seconds on this desktop: macOS ioreg, Linux xprintidle or GNOME Mutter. None: no reader."""
    plat = platform()
    if plat != "darwin" and not plat.startswith("linux"):
        return None  # Windows and others: no idle reader
    fake = os.environ.get("TG_AWAY_FAKE_IDLE")  # tests: a number, or a file holding one
    if fake:
        if fake.startswith("/"):
            try:
                return int(open(fake).read().strip())
            except (OSError, ValueError):
                return None
        return int(fake)
    if plat != "darwin":  # a headless server has neither reader (or no display to read): None
        idle = run_ms(["xprintidle"])
        return idle if idle is not None else run_ms(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Mutter.IdleMonitor", "--object-path",
             "/org/gnome/Mutter/IdleMonitor/Core", "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime"])
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r'"HIDIdleTime" = (\d+)', out)
    return int(m.group(1)) // 1_000_000_000 if m else None


class Presence:
    """Detects desktop input: idle time only grows until a key or mouse event resets it."""

    def __init__(self, idle):
        self.last = idle

    def idle(self):
        """Current idle seconds, or None when the user touched the computer since the last read."""
        now = idle_seconds()
        if now is None or now < self.last:
            return None
        self.last = now
        return now


def mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


# ---------- state: waits, answers, claims ----------

def state(*parts):
    for sub in ("", "waits", "answers", "claims", "files", "inbox", "failures"):
        os.makedirs(os.path.join(STATE, sub), mode=0o700, exist_ok=True)
    if os.lstat(STATE).st_uid != os.getuid():
        raise PermissionError(f"{STATE} belongs to another user")  # shared /tmp: never use a planted directory
    os.chmod(STATE, 0o700)
    return os.path.join(STATE, *parts)


def write_json(path, data):
    """Atomic write: readers never see half a file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def pinged():
    """{str(message_id): {"session", "quote"}} of the last 500 pings (a 0.1 list of ids still reads)."""
    data = read_json(state("pinged.json"), {})
    return {str(m): {} for m in data} if isinstance(data, list) else data if isinstance(data, dict) else {}


def open_wait(message_id, session, kind, questions=None, quote=""):
    """Register a ping the hook in this process waits on (its pid lets the poller spot a killed hook)."""
    now = time.time()
    write_json(state("waits", f"{message_id}.json"), {"session": session, "kind": kind, "questions": questions or [],
                                                      "opened": now, "expires": now + REPLY_WINDOW,
                                                      "pid": os.getpid()})
    seen = pinged()
    seen[str(message_id)] = {"session": session, "quote": cut(" ".join(quote.split()), 120)}
    write_json(state("pinged.json"), dict(list(seen.items())[-500:]))  # ponytail: last 500 pings only


def ping_info(message_id):
    """{"session", "quote"} for a message a hook sent as a ping, else None (replies to other messages: silence)."""
    return pinged().get(str(message_id))


def open_inbox(session, until):
    """Listen for replies to any earlier ping of this session (tg-stop.py, silent: no message sent)."""
    try:
        os.remove(state("answers", f"inbox-{session}.json"))  # a killed listener's leftover reply
    except OSError:
        pass
    write_json(state("inbox", f"{session}.json"), {"expires": until, "pid": os.getpid()})


def close_inbox(session):
    """Drop this process's inbox (a newer listener's registration stays)."""
    if (read_json(state("inbox", f"{session}.json"), {}) or {}).get("pid") == os.getpid():
        for path in (state("inbox", f"{session}.json"), state("answers", f"inbox-{session}.json")):
            try:
                os.remove(path)
            except OSError:
                pass


def open_inboxes():
    now, found = time.time(), set()
    for path in glob.glob(state("inbox", "*.json")):
        box = read_json(path) or {}
        if box.get("expires", 0) > now and alive(box.get("pid")):
            found.add(os.path.basename(path)[:-5])
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, TypeError):
        return True  # exists but not ours, or no pid recorded: trust the expiry time
    return True


class Interrupted(Exception):
    """Esc or a closed session stopped the hook."""


def exit_on_signals():
    """Turn SIGTERM/SIGHUP/SIGINT into Interrupted, so the hook's `finally` closes its ping."""
    def handler(signum, frame):
        raise Interrupted(signal.Signals(signum).name)
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, handler)


def close_ping(message_id, answered=False):
    """Best effort: drop the ping's buttons; a thumbs-up when answered from Telegram. Never raises."""
    calls = [("editMessageReplyMarkup", {"reply_markup": {"inline_keyboard": []}})]
    if answered:
        calls.append(("setMessageReaction", {"reaction": [{"type": "emoji", "emoji": "\U0001F44D"}]}))
    for method, params in calls:
        try:
            api(method, {"chat_id": CHAT, "message_id": message_id, **params}, timeout=3)
        except Exception:
            pass


def close_wait(message_id):
    for sub in ("waits", "answers"):
        try:
            os.remove(state(sub, f"{message_id}.json"))
        except OSError:
            pass


def events(message_id):
    return read_json(state("answers", f"{message_id}.json"), [])


def open_waits():
    now, found = time.time(), {}
    for path in glob.glob(state("waits", "*.json")):
        wait = read_json(path)
        if wait and wait.get("expires", 0) > now and alive(wait.get("pid")):
            found[int(os.path.basename(path)[:-5])] = wait
        elif wait:
            mid = int(os.path.basename(path)[:-5])
            close_wait(mid)  # expired, or its hook was killed: stale
            close_ping(mid)
    return found


def claim(session, until):
    """Tell tg-away.py that tg-ask.py handles this session's permission prompt until `until`."""
    write_json(state("claims", session), {"until": until})


def unclaim(session):
    try:
        os.remove(state("claims", session))
    except OSError:
        pass


def claimed(session, at):
    return (read_json(state("claims", session), {}) or {}).get("until", 0) >= at


def reply_text(event):
    """A reply's text, plus "Attached: <path>" for its downloaded photo or file ("" when neither)."""
    parts = [(event.get("text") or "").strip()] + ([f"Attached: {event['file']}"] if event.get("file") else [])
    return "\n".join(p for p in parts if p)


def selected(question, qi, evts):
    """Labels currently toggled on for a multiSelect question, in option order."""
    labels = [o.get("label", "") for o in question.get("options") or []]
    on = set()
    for e in evts:
        m = re.fullmatch(rf"q{qi}o(\d+)", e.get("data", ""))
        if m and int(m.group(1)) < len(labels):
            on ^= {int(m.group(1))}
    return [labels[i] for i in sorted(on)]


# ---------- Telegram ----------

def bot_token():
    return opt("BOT_TOKEN", "TELEGRAM_BOT_TOKEN")


def multipart(params, files):
    boundary = uuid.uuid4().hex
    out = b""
    for key, value in params.items():
        value = value if isinstance(value, str) else json.dumps(value)
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'.encode() + value.encode() + b"\r\n"
    for key, (name, mime, data) in files.items():
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n").encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def api(method, params, files=None, timeout=20):
    """Call the Bot API; the token stays inside the URL object, never in argv or output."""
    if files:
        body, ctype = multipart(params, files)
    else:
        body, ctype = json.dumps(params).encode(), "application/json"
    req = urllib.request.Request(f"{API}/bot{bot_token()}/{method}", body, {"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        res = json.load(resp)
    if not res.get("ok"):
        raise RuntimeError(f"{method} not ok")
    return res["result"]


def send(markdown, buttons, image=None):
    """Send a rich message with inline buttons (plain text fallback keeps the buttons); return its id."""
    base = {"chat_id": CHAT}
    if buttons:
        base["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d}] for t, d in buttons]}
    if image:
        try:
            rich = {"markdown": markdown + "\n\n![](tg://photo?id=img1)",
                    "media": [{"id": "img1", "media": {"type": "photo", "media": "attach://img1"}}]}
            return api("sendRichMessage", {**base, "rich_message": rich}, files={"img1": image}, timeout=60)["message_id"]
        except Exception:
            pass  # send without the image
    try:
        return api("sendRichMessage", {**base, "rich_message": {"markdown": markdown}})["message_id"]
    except Exception:
        return api("sendMessage", {**base, "text": markdown})["message_id"]


def poller_running(lock="poller.lock"):
    try:
        fd = os.open(state(lock), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def ensure_poller():
    if not poller_running():
        subprocess.Popen([sys.executable, os.path.abspath(__file__), "poll"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def wait_for(message_id, stop, deadline):
    """Poll the answer file until `stop(events)` returns a result, or the deadline passes (None)."""
    checked = 0
    while time.time() < deadline:
        if time.time() - checked > 5:
            ensure_poller()
            checked = time.time()
        result = stop(events(message_id))
        if result is not None:
            return result
        time.sleep(0.5)
    return None


# ---------- poller ----------

def record(message_id, event):
    path = state("answers", f"{message_id}.json")
    write_json(path, read_json(path, []) + [event])


def toast(query_id, text):
    try:
        api("answerCallbackQuery", {"callback_query_id": query_id, "text": text})
    except Exception:
        pass


def on_callback(query, waits):
    msg = query.get("message") or {}
    if str((query.get("from") or {}).get("id")) != CHAT or str((msg.get("chat") or {}).get("id")) != CHAT:
        return
    mid, data = msg.get("message_id"), query.get("data", "")
    wait = waits.get(mid)
    if not wait:
        return toast(query.get("id"), "Expired")
    record(mid, {"data": data})
    m = re.fullmatch(r"q(\d+)o\d+", data)
    questions = wait.get("questions") or []
    if m and int(m.group(1)) < len(questions) and questions[int(m.group(1))].get("multiSelect"):
        qi = int(m.group(1))
        return toast(query.get("id"), "Selected: " + (", ".join(selected(questions[qi], qi, events(mid))) or "none"))
    toast(query.get("id"), "Answer in the app" if data == "app" else "Sent to Claude")


FILE_MAX = 20 * 1024 * 1024  # Bot API getFile download limit


def download(msg):
    """Save a reply's photo (largest size) or document as STATE/files/<message_id>-<name>, mode 0600.
    Its path, or "" (too big, or failed). The file URL holds the token: it never leaves this function."""
    doc = msg.get("document") or (msg.get("photo") or [{}])[-1]
    if (doc.get("file_size") or 0) > FILE_MAX:
        log("poller", f"file skipped: {doc.get('file_size')} bytes, over the 20 MB Bot API limit")
        return ""
    try:
        remote = api("getFile", {"file_id": doc.get("file_id", "")})["file_path"]
        name = re.sub(r"[^\w.-]", "_", os.path.basename(doc.get("file_name") or remote))[-100:] or "file"
        with urllib.request.urlopen(urllib.request.Request(f"{API}/file/bot{bot_token()}/{remote}"), timeout=60) as resp:
            data = resp.read(FILE_MAX + 1)
        if len(data) > FILE_MAX:
            log("poller", "file skipped: over the 20 MB Bot API limit")
            return ""
        path = state("files", f"{msg.get('message_id')}-{name}")
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        log("poller", f"file download failed: {type(e).__name__}: {e}")  # log() redacts the token
        return ""


def reply_event(msg, text):
    event = {"text": text, "date": msg.get("date")}
    if msg.get("photo") or msg.get("document"):
        event["file"] = download(msg)
    return event


def on_message(msg, waits):
    """A reply to an open ping is its answer (a reply always comes after the ping, so no clock check).
    Text, or a photo or document (its caption counts as the text)."""
    if str((msg.get("from") or {}).get("id")) != CHAT or str((msg.get("chat") or {}).get("id")) != CHAT:
        return
    target = msg.get("reply_to_message") or {}
    text = (msg.get("text") or msg.get("caption") or "").strip()
    if not target or not (text or msg.get("photo") or msg.get("document")):
        return  # not a reply
    if target.get("message_id") in waits:
        record(target["message_id"], reply_event(msg, text))
        return
    ping = ping_info(target.get("message_id"))  # a closed ping; replies to alerts or manual pings: silence
    if ping is None:
        return
    session = ping.get("session")
    if session and session in open_inboxes():  # tg-stop.py listens: this wakes Claude
        record(f"inbox-{session}", {**reply_event(msg, text), "ping": target["message_id"], "quote": ping.get("quote", "")})
        return
    try:
        api("sendMessage", {"chat_id": CHAT, "text": NOT_LISTENING, "reply_parameters": {"message_id": msg.get("message_id")}})
    except Exception:
        pass


def poll():
    """Single instance: long-poll Telegram while waits are open, record the user's answers."""
    lock = os.open(state("poller.lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return  # another poller runs
    os.ftruncate(lock, 0)
    os.write(lock, str(os.getpid()).encode())
    last_open = time.time()
    while time.time() - last_open < LINGER:
        waits = open_waits()
        if waits or open_inboxes():
            last_open = time.time()
        offset = read_json(state("offset"), 0)
        try:
            updates = api("getUpdates", {"offset": offset, "timeout": 50, "allowed_updates": ["message", "callback_query"]},
                          timeout=65)
        except Exception:
            time.sleep(5)
            continue
        for upd in updates:
            waits = open_waits()
            if upd.get("callback_query"):
                on_callback(upd["callback_query"], waits)
            elif upd.get("message"):
                on_message(upd["message"], waits)
            write_json(state("offset"), upd["update_id"] + 1)


if __name__ == "__main__" and sys.argv[1:] == ["poll"]:
    try:
        poll()
    except Exception:
        pass  # never crash loudly; the next hook starts a new poller
