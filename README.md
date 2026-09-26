# claude-telegram-hook

Answer Claude Code from your phone when you step away from your Mac.

When a Claude Code session on your Mac needs you and you have been away for a while, this plugin sends the prompt to Telegram:

- **Questions** (`AskUserQuestion`): one button per option, multi-select toggles, or reply with free text.
- **Plan approval** (`ExitPlanMode`): the plan as Markdown, with Allow / Deny.
- **Permission prompts**: a plain-English line ("Run a shell command", "Edit config.ts") with the raw command folded away, and Allow / Deny.
- **A turn that ends with a question**: reply to the Telegram message and Claude continues with your answer.

Your answer goes straight back into the session. Touch the Mac, tap "Answer in app", or let the reply window pass, and the normal dialog takes over. Nothing is sent while you are at the Mac.

It works with the sessions you already run (terminal, the Claude desktop app, Conductor): it is a set of Claude Code hooks, not a separate agent.

## Requirements

- macOS. Presence detection reads the Mac's keyboard and mouse idle time (`ioreg`). On other systems the hooks do nothing.
- `python3` (3.9 or later; the one from Xcode Command Line Tools works) and `curl`. No other dependencies.
- A recent Claude Code with plugin `userConfig` support.
- A Telegram bot that nothing else polls. Telegram allows one `getUpdates` reader per bot token, so do not reuse the bot of the official Telegram channel plugin or any other bot program.

## Setup

1. **Create a bot.** In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.

2. **Find your chat ID.** Send any message to your new bot, then run this and paste the token when it waits for input (the token is not echoed):

   ```bash
   read -rs TOKEN && curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | python3 -c 'import json,sys; print({u["message"]["chat"]["id"] for u in json.load(sys.stdin)["result"] if "message" in u})'
   ```

3. **Install the plugin.**

   ```bash
   claude plugin marketplace add hlebtkachenko/claude-telegram-hook
   ```

   ```bash
   claude plugin install telegram-hook@claude-telegram-hook
   ```

   Claude Code asks for the bot token and chat ID when the plugin is enabled. The token is stored in the system's secure credential store, not in `settings.json`. Start a new session afterwards.

## Options

| Option | Default | Meaning |
|---|---|---|
| `bot_token` | required | Bot token from @BotFather (stored as a secret). |
| `chat_id` | required | Your chat with the bot. Updates from any other chat are ignored. |
| `away_delay` | 600 | Seconds of Mac idle time before a prompt goes to Telegram (1 to 600). |
| `reply_window` | 600 | Seconds a Telegram ping waits for your answer (1 to 600). |
| `user_name` | `The user` | Name Claude sees in "`<name>` replied in Telegram". |

Change them with `/config` in a session. Outside the plugin (for example when calling `tg-ping.sh` from your own scripts), the same values can come from `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TG_AWAY_DELAY`, `TG_REPLY_WINDOW` and `TG_USER_NAME`.

## How it answers

| Where you answer | What Claude gets |
|---|---|
| Option button | That option, as if you had picked it in the dialog. |
| Text reply to a question | Your text as the "Other" answer. |
| Allow / Deny | The permission decision. |
| Text reply to a permission or plan | A denial carrying your text, so Claude reads your instruction. |
| Text reply to a turn that ended with a question | Claude wakes up with "`<name>` replied in Telegram: ...". |
| "Answer in app", Mac input, or no answer | Nothing: the normal dialog stays open. |

## Security

- Only the configured chat can answer. Buttons and replies from anyone else are dropped.
- Telegram input is never executed. It only becomes an answer, an allow/deny decision, or a note to Claude.
- An Allow tap in Telegram approves that one call, the same as the dialog. Anyone holding your phone and Telegram session can approve prompts while you are away, so keep Telegram locked.
- The token goes to Telegram inside the request URL object or curl's stdin config, never in process arguments or logs.

## Limitations

- macOS only (see Requirements).
- Hooks run for at most 1320 seconds, which is why both timers are capped at 600.
- "A turn that ends with a question" means the last message ends with `?`.
- The "Reply in Claude" link reads the Claude desktop app's local session files. It appears only for desktop sessions with Remote Control, and may stop working if the app changes those files.

## Troubleshooting

Every hook decision (skip, hold, ping, answer) is logged, without secrets, to:

```bash
tail -f "$TMPDIR/claude-telegram-hook/hooks.log"
```

## Development

```bash
python3 tests/test_hooks.py
```

```bash
bash tests/test_away.sh
```

The tests run against a local fake Telegram server and never call the real API. `tests/test_away.sh` needs `jq`. See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.

## License

[MIT](LICENSE)
