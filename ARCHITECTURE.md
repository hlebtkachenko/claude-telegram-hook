# Architecture Overview

## 1. Project Structure

```
claude-telegram-hook/
├── .claude-plugin/marketplace.json     # Marketplace manifest: this repo is a one-plugin marketplace
├── plugins/telegram-hook/
│   ├── .claude-plugin/plugin.json      # Plugin manifest and userConfig (token, chat ID, timers, name)
│   ├── hooks/hooks.json                # Hook wiring: events, matchers, timeouts
│   └── scripts/
│       ├── tg-bot.py                   # Shared helpers and the reply poller (`tg-bot.py poll`)
│       ├── tg-ask.py                   # Blocking hook: questions, plans, permission prompts
│       ├── tg-away.py                  # Notification hook: fallback ping for prompts tg-ask.py cannot hold
│       ├── tg-stop.py                  # Stop hook (asyncRewake): turn ended with a question, or silent inbox
│       ├── tg-failure.py               # StopFailure hook: ping on API errors
│       ├── tg-pair.py                  # SessionStart hook: chat ID pairing while chat_id is empty
│       ├── tg-mcp.py                   # Stdio MCP server: the `notify` tool
│       └── tg-ping.sh                  # Send one message (used by tg-away.py; usable from scripts)
├── tests/
│   ├── test_hooks.py                   # tg-bot / tg-ask / tg-stop / tg-failure / tg-pair / tg-mcp against a fake Telegram server
│   └── test_away.sh                    # tg-away table test (dry run)
├── .github/workflows/test.yml          # CI: both test suites
├── AGENTS.md                           # Instructions for coding agents
└── README.md                           # User guide
```

## 2. High-Level System Diagram

```
Claude Code session ──hook event (stdin JSON)──> tg-ask.py / tg-away.py / tg-stop.py
        ^                                               │ send ping (Bot API)
        │ decision JSON (stdout) or exit 2 + stderr     v
        │                                         Telegram ──> user's phone
        │                                               │ button / reply
        │                                               v
        └──── answers/<message_id>.json <──── tg-bot.py poll (getUpdates)
```

## 3. Core Components

### 3.1. Hooks

- **tg-ask.py** (`PreToolUse` for `AskUserQuestion|ExitPlanMode`, `PermissionRequest` for everything else). Skips unless the desktop has been idle at least 30 s. Holds until idle reaches `away_delay`, sends the prompt with buttons, then waits up to `reply_window` for an answer. It prints the hook decision JSON, or nothing to fall back to the dialog. Any keyboard or mouse input releases it. On `PermissionRequest` with `permission_suggestions`, an "Always allow" button returns the `addRules` allow entries for `localSettings` or `session` unchanged as `decision.updatedPermissions` (other entries are dropped; the ping lists each rule). When the wait closes, the ping loses its buttons (`editMessageReplyMarkup`); a Telegram answer also gets a thumbs-up (`setMessageReaction`).
- **tg-away.py** (`Notification`, `permission_prompt` and `elicitation_dialog`). Starts a detached timer. When it ends, it pings through `tg-ping.sh` only if the transcript is unchanged, the desktop is still idle, and tg-ask.py has not claimed the prompt. This covers prompts that skip `PermissionRequest` (for example sandbox network prompts). These pings are notify-only.
- **tg-stop.py** (`Stop`, `asyncRewake`). If the last assistant message ends with `?`, it waits for the away delay and pings with the message and the newest image of the turn. A text reply exits 2 with "`<name>` replied in Telegram ..." on stderr, which wakes Claude. Any other turn opens a silent inbox (`inbox/<session>.json`) for up to `away_delay + reply_window`; a reply to an earlier ping of the session wakes Claude the same way. A transcript change ends it.
- **tg-failure.py** (`StopFailure`). Skips subagents. Blocker errors (auth, billing, account, org, cloud credentials, model) ping once per error type per hour; other errors ping only when away, once per session per hour.
- **tg-pair.py** (`SessionStart`). With a token but no `chat_id`: starts one pairing poller (`pair.lock`, 10 minutes) that answers a private message with the sender's own chat ID, and returns `systemMessage` plus `additionalContext` telling the user to message the bot.
- **tg-mcp.py** (MCP server `telegram`). Newline-delimited JSON-RPC over stdio; one tool, `notify`, sends Markdown to the configured chat. Options arrive through `${user_config.*}` in `plugin.json`.

Presence: `idle_seconds()` in tg-bot.py reads `ioreg` on macOS, `xprintidle` or GNOME Mutter's IdleMonitor (`gdbus`) on Linux, and returns None elsewhere, so the hooks skip.

### 3.2. Poller

`tg-bot.py poll` is started on demand by any waiting hook, runs one instance at a time (`fcntl` lock), long-polls `getUpdates`, and accepts only the configured chat. It writes button presses and replies to `answers/<message_id>.json`. A reply to a closed ping goes to `answers/inbox-<session>.json` when that session listens, else it gets "This session is not listening now". A photo or document reply is downloaded (`getFile`, 20 MB max) into `files/` with mode 0600, and the event holds its local path. It exits after `TG_POLL_LINGER` seconds (default 60) with no open waits or inboxes.

## 4. Data Stores

Files only, under `claude-telegram-hook/` in `$TMPDIR`, else `$XDG_RUNTIME_DIR`, else `~/.cache` (never a shared `/tmp`). The directory must be a real directory owned by the user (no symlink); it is kept at mode 0700, and `hooks.log` is opened without following symlinks (0600):

- `waits/<message_id>.json`: open pings (session, kind, questions, expiry, hook pid).
- `answers/<message_id>.json`: events the poller recorded for a ping; `answers/inbox-<session>.json`: replies routed to a listening session.
- `inbox/<session>.json`: tg-stop.py listens for replies to this session's earlier pings (pid, expiry).
- `files/<message_id>-<name>`: photos and documents from replies (0600).
- `failures/`: tg-failure.py's once-per-hour markers.
- `claims/<session>`: tg-ask.py owns this session's prompt until a time, so tg-away.py stays quiet.
- `<session>`: tg-away.py's newest timer token for the session.
- `bot_token` (0600): the token as the MCP server received it; hooks read it because Claude Code passes secret options only to MCP servers.
- `offset`, `pinged.json`, `poller.lock`, `pair.lock`, `hooks.log`: poller offset, last 500 pings (message ID to session and quote), locks, decision log.

## 5. External Integrations / APIs

- **Telegram Bot API** (`sendRichMessage` with `sendMessage` fallback, `getUpdates`, `answerCallbackQuery`, `editMessageReplyMarkup`, `setMessageReaction`, `getFile` and the file download URL), over HTTPS with stdlib `urllib` and `curl`.
- **Claude Code hooks**: input and output shapes of `SessionStart`, `PreToolUse`, `PermissionRequest`, `Notification`, `Stop` and `StopFailure`, plus `CLAUDE_PLUGIN_OPTION_*` for configuration; an MCP server declared in `plugin.json`.
- **Claude desktop app session files** (`~/Library/Application Support/Claude/claude-code-sessions`), read-only and best-effort, for the session title and the Remote Control link.

## 6. Deployment & Infrastructure

Distributed as a Claude Code plugin from this repository (`claude plugin marketplace add hlebtkachenko/claude-telegram-hook`). Runs locally on the user's Mac or Linux desktop. CI: GitHub Actions runs both test suites on Ubuntu with the platform and idle time faked.

## 7. Security Considerations

- Authorization: the `chat_id` option. The poller checks both the sender and the chat of every update. The pairing poller only replies to a private chat with that chat's own ID.
- Telegram input is never executed. It becomes an answer, a decision, or a note.
- Token: `userConfig` `sensitive` (system credential store). Claude Code passes it only to the MCP server, which saves a 0600 copy (`bot_token`) in the state dir for the hooks. It is kept out of argv, logs, events and notes: urllib `Request` in Python, a curl config file descriptor (`-K <(printf ...)`) in shell. Prompt text stays out of argv too (0600 file for the tg-away timer, stdin for tg-ping.sh). File download URLs hold it and stay inside `download()`.
- State directory and files are private to the user (0700).

## 8. Development & Testing Environment

Stdlib Python and bash only. Gate: `python3 tests/test_hooks.py && bash tests/test_away.sh`. Tests point `TG_API_BASE` at a local fake server and fake the platform and idle time (`TG_AWAY_FAKE_PLATFORM`, `TG_AWAY_FAKE_IDLE`).

## 9. Future Considerations / Roadmap

- Voice replies.
- Presence on Wayland desktops other than GNOME.

## 10. Project Identification

Project Name: claude-telegram-hook

Repository URL: https://github.com/hlebtkachenko/claude-telegram-hook

Primary Contact: Hleb Tkachenko

Date of Last Update: 2026-09-27

## 11. Glossary

- Ping: a Telegram message a hook sent for one prompt.
- Wait: an open ping a hook is blocking on.
- Claim: tg-ask.py's marker that it owns a session's current prompt.
- Inbox: tg-stop.py listening, without a message, for replies to a session's earlier pings.
