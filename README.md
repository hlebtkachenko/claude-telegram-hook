# claude-telegram-hook

Answer Claude Code from your phone when you step away from your computer.

When a Claude Code session on your Mac or Linux desktop needs you and you have been away for a while, this plugin sends the prompt to Telegram:

- **Questions** (`AskUserQuestion`): one button per option, multi-select toggles, or reply with free text.
- **Plan approval** (`ExitPlanMode`): the plan as Markdown, with Allow / Deny.
- **Permission prompts**: the command itself for shell commands ("Run: npm test", with Claude's description below it), a plain-English line for other tools ("Edit config.ts") with the raw input folded away, and Allow / Deny. Anything cut to fit is marked "(truncated, check in app)". When Claude Code suggests "don't ask again" allow rules saved to this project's local settings or the session, an **Always allow** button applies exactly those rules; the ping lists each one (`Bash(npm test) -> localSettings`). Mode changes and rules for shared or user-wide settings are never offered.
- **A turn that ends with a question**: reply to the Telegram message and Claude continues with your answer.
- **Reply to an earlier ping**: after any finished turn the session keeps listening (silently, up to away delay + reply window). Reply to any earlier ping of that session and Claude wakes up with your text.
- **Photos and files**: reply with a photo or document (the caption is its text). It is downloaded and Claude gets its local path to read.
- **API errors** (`StopFailure`): billing, auth and other errors only you can fix always ping; rate limits and other errors ping only while you are away.
- **`notify` tool**: Claude can message you when you asked to be told, or for a blocker only you can clear.

Your answer goes straight back into the session. Closed pings lose their buttons; answered ones get a thumbs-up reaction. Touch the computer, tap "Answer in app", or let the reply window pass, and the normal dialog takes over. Nothing is sent while you are at the computer.

It works with the sessions you already run (terminal, the Claude desktop app, Conductor): it is a set of Claude Code hooks, not a separate agent.

## Requirements

- macOS, or a Linux desktop session. Presence detection reads keyboard and mouse idle time: `ioreg` on macOS; on Linux `xprintidle` (X11), else GNOME's Mutter idle monitor over `gdbus` (GNOME on Wayland). Without an idle reader (headless servers, SSH sessions without a desktop, other Wayland desktops, Windows) the hooks do nothing.
- `python3` (3.9 or later; the one from Xcode Command Line Tools works) and `curl`. No other dependencies.
- A recent Claude Code with plugin `userConfig` support.
- A Telegram bot that nothing else polls. Telegram allows one `getUpdates` reader per bot token, so do not reuse the bot of the official Telegram channel plugin or any other bot program.

## Setup

1. **Create a bot.** In Telegram, open [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.

2. **Find your chat ID** (optional: you can pair later, see step 3). Send any message to your new bot in a private chat, then run this and paste the token when it waits for input. The token is not echoed and stays out of the process list. Your chat ID is the positive number it prints; group chats are not supported.

   ```bash
   read -rs TOKEN && printf 'url = "https://api.telegram.org/bot%s/getUpdates"\n' "$TOKEN" | curl -s -K - | python3 -c 'import json,sys; print({u["message"]["chat"]["id"] for u in json.load(sys.stdin)["result"] if "message" in u})'; unset TOKEN
   ```

3. **Install the plugin.**

   In a Claude Code session, run:

   ```
   /plugin marketplace add hlebtkachenko/claude-telegram-hook
   /plugin install telegram-hook@claude-telegram-hook
   ```

   Enter the bot token and chat ID when asked. No chat ID yet? Leave it empty and start a session: for 10 minutes the bot answers any private message with "Your chat ID is ...". Paste that ID into the chat_id option and start a new session. Until then nothing else is sent. If you were not asked, open `/plugin`, pick telegram-hook under Installed, and choose Configure options. The token is stored in the system's secure credential store, not in `settings.json`. Start a new session afterwards: Claude Code gives the secret token only to the plugin's MCP server, which hands it to the hooks through a 0600 file in the private state folder, so pings start from the first session after setup.

   To update later: `/plugin marketplace update claude-telegram-hook`, then restart the session.

## Options

| Option | Default | Meaning |
|---|---|---|
| `bot_token` | required | Bot token from @BotFather (stored as a secret). |
| `chat_id` | empty (pairing) | Your chat with the bot. Updates from any other chat are ignored. Empty: pairing mode, all other hooks stay silent. |
| `away_delay` | 600 | Seconds of keyboard and mouse idle time before a prompt goes to Telegram (1 to 600). |
| `reply_window` | 600 | Seconds a Telegram ping waits for your answer (1 to 600). |
| `user_name` | `The user` | Name Claude sees in "`<name>` replied in Telegram". |

Change them in `/plugin` (telegram-hook, Configure options). The non-secret options also appear in `/config`. Outside the plugin (for example when calling `tg-ping.sh` from your own scripts), the same values can come from `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `TG_AWAY_DELAY`, `TG_REPLY_WINDOW` and `TG_USER_NAME`.

## How it answers

| Where you answer | What Claude gets |
|---|---|
| Option button | That option, as if you had picked it in the dialog. |
| Text reply to a question | Your text as the "Other" answer. |
| Allow / Deny | The permission decision. |
| Text reply to a permission or plan | A denial carrying your text, so Claude reads your instruction. |
| Text reply to a turn that ended with a question | Claude wakes up with "`<name>` replied in Telegram (HH:MM) to your message ...", followed by your text. |
| Always allow | Allow, plus the permission rules Claude Code suggested for the call (as "don't ask again" in the dialog). |
| Reply to an earlier ping while the session listens | Claude wakes up with the same note. If the session is not listening, the bot answers "This session is not listening now; open it to answer." |
| Photo or document reply | Its caption as the text, plus "Attached: `<path>`" for Claude to read. |
| "Answer in app", keyboard or mouse input, or no answer | Nothing: the normal dialog stays open. |

## Security

- What goes to Telegram: question and option text, plans, tool commands and file paths (commands cut at 800 characters), the project and branch name, the session title, Claude's last message, and the newest image of the turn, API error details, and whatever Claude sends through `notify`. Photos and files you send back are downloaded (20 MB max, the Bot API limit) to the state directory's `files/` (mode 0600; see Troubleshooting for where it is) and Claude can read them. Telegram bot chats are not end-to-end encrypted. Don't use this plugin where that content must not leave the machine.
- Only the configured chat can answer. Buttons and replies from anyone else are dropped. While pairing, the bot only tells a private sender their own chat ID.
- Telegram input is never executed. It only becomes an answer, an allow/deny decision, or a note to Claude.
- An Allow tap in Telegram approves that one call, the same as the dialog. Always allow saves the rules Claude Code proposed, the same as "don't ask again". Anyone holding your phone and Telegram session can approve prompts while you are away, so keep Telegram locked.
- Claude Code does not pass secret options to plugin hooks, only to MCP servers. The plugin's MCP server (`telegram`, keep it enabled in `/mcp`) therefore saves the token as `bot_token` (mode 0600) in the private state folder: `$TMPDIR/claude-telegram-hook/`, else `$XDG_RUNTIME_DIR/claude-telegram-hook/`, else `~/.cache/claude-telegram-hook/`. The hooks read it there. It is rewritten each time the MCP server starts; to remove the token for good, uninstall the plugin or clear the option, then delete that file.
- The token goes to Telegram inside the request URL object (Python) or a curl config file descriptor (`tg-ping.sh`), never in process arguments, logs, or notes to Claude (file download URLs contain it and never leave the poller). Prompt text is not put in process arguments either: `tg-away.py` hands it to its timer in a 0600 file and to `tg-ping.sh` on stdin. (`bash tg-ping.sh "text"` from your own scripts does put the text in argv; pipe it in instead.)

## Limitations

- macOS and Linux desktops only (see Requirements).
- Hooks run for at most 1320 seconds, which is why both timers are capped at 600.
- "A turn that ends with a question" means the last message ends with `?`.
- Replies to earlier pings reach a session only while it listens: from the end of a turn until the next turn starts, at most away delay + reply window.
- The `notify` tool reads the options through its MCP server environment; start a new session after changing them.
- The "Reply in Claude" link reads the Claude desktop app's local session files. It appears only for desktop sessions with Remote Control, and may stop working if the app changes those files.

## Troubleshooting

Every hook decision (skip, hold, ping, answer) is logged, without secrets, to `hooks.log` in the state directory: `claude-telegram-hook/` under `$TMPDIR` (macOS), else `$XDG_RUNTIME_DIR` (most Linux desktops), else `~/.cache`.

```bash
tail -f "${TMPDIR:-${XDG_RUNTIME_DIR:-$HOME/.cache}}/claude-telegram-hook/hooks.log"
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
