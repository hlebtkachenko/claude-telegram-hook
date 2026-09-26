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
│       ├── tg-stop.py                  # Stop hook (asyncRewake): turn ended with a question
│       └── tg-ping.sh                  # Send one message (used by tg-away.py; usable from scripts)
├── tests/
│   ├── test_hooks.py                   # tg-bot / tg-ask / tg-stop against a fake Telegram server
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

- **tg-ask.py** (`PreToolUse` for `AskUserQuestion|ExitPlanMode`, `PermissionRequest` for everything else). Skips unless the Mac has been idle at least 30 s. Holds until idle reaches `away_delay`, sends the prompt with buttons, then waits up to `reply_window` for an answer. It prints the hook decision JSON, or nothing to fall back to the dialog. Any Mac input releases it.
- **tg-away.py** (`Notification`, `permission_prompt` and `elicitation_dialog`). Starts a detached timer. When it ends, it pings through `tg-ping.sh` only if the transcript is unchanged, the Mac is still idle, and tg-ask.py has not claimed the prompt. This covers prompts that skip `PermissionRequest` (for example sandbox network prompts). These pings are notify-only.
- **tg-stop.py** (`Stop`, `asyncRewake`). If the last assistant message ends with `?`, it waits for the away delay and pings with the message and the newest image of the turn. A text reply exits 2 with "`<name>` replied in Telegram ..." on stderr, which wakes Claude.

### 3.2. Poller

`tg-bot.py poll` is started on demand by any waiting hook, runs one instance at a time (`fcntl` lock), long-polls `getUpdates`, and accepts only the configured chat. It writes button presses and replies to `answers/<message_id>.json`. It exits after `TG_POLL_LINGER` seconds (default 60) with no open waits. A late reply to a closed ping gets an "expired" note.

## 4. Data Stores

Files only, under `$TMPDIR/claude-telegram-hook/` (mode 0700):

- `waits/<message_id>.json`: open pings (session, kind, questions, expiry, hook pid).
- `answers/<message_id>.json`: events the poller recorded for a ping.
- `claims/<session>`: tg-ask.py owns this session's prompt until a time, so tg-away.py stays quiet.
- `<session>`: tg-away.py's newest timer token for the session.
- `offset`, `pinged.json`, `poller.lock`, `hooks.log`: poller offset, last 500 ping ids, lock, decision log.

## 5. External Integrations / APIs

- **Telegram Bot API** (`sendRichMessage` with `sendMessage` fallback, `getUpdates`, `answerCallbackQuery`), over HTTPS with stdlib `urllib` and `curl`.
- **Claude Code hooks**: input and output shapes of `PreToolUse`, `PermissionRequest`, `Notification` and `Stop`, plus `CLAUDE_PLUGIN_OPTION_*` for configuration.
- **Claude desktop app session files** (`~/Library/Application Support/Claude/claude-code-sessions`), read-only and best-effort, for the session title and the Remote Control link.

## 6. Deployment & Infrastructure

Distributed as a Claude Code plugin from this repository (`claude plugin marketplace add hlebtkachenko/claude-telegram-hook`). Runs locally on the user's Mac. CI: GitHub Actions runs both test suites on Ubuntu with the platform and idle time faked.

## 7. Security Considerations

- Authorization: the `chat_id` option. The poller checks both the sender and the chat of every update.
- Telegram input is never executed. It becomes an answer, a decision, or a note.
- Token: `userConfig` `sensitive` (system credential store). It is kept out of argv and logs: urllib `Request` in Python, curl `-K -` in shell.
- State directory and files are private to the user (0700).

## 8. Development & Testing Environment

Stdlib Python and bash only. Gate: `python3 tests/test_hooks.py && bash tests/test_away.sh`. Tests point `TG_API_BASE` at a local fake server and fake the platform and idle time (`TG_AWAY_FAKE_PLATFORM`, `TG_AWAY_FAKE_IDLE`).

## 9. Future Considerations / Roadmap

- Linux presence detection.
- "Always allow" button through `updatedPermissions`.
- Remove buttons from resolved pings.
- Voice replies.

## 10. Project Identification

Project Name: claude-telegram-hook

Repository URL: https://github.com/hlebtkachenko/claude-telegram-hook

Primary Contact: Hleb Tkachenko

Date of Last Update: 2026-09-26

## 11. Glossary

- Ping: a Telegram message a hook sent for one prompt.
- Wait: an open ping a hook is blocking on.
- Claim: tg-ask.py's marker that it owns a session's current prompt.
