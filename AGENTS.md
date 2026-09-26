# AGENTS.md

Claude Code plugin that relays questions, plan approvals and permission prompts to Telegram while the user is away from their Mac. Read [ARCHITECTURE.md](ARCHITECTURE.md) first.

## Gate

Run before every commit:

```bash
python3 tests/test_hooks.py && bash tests/test_away.sh
```

After changing a manifest, also run `claude plugin validate .` and `claude plugin validate plugins/telegram-hook`.

## Rules

- Stdlib Python and bash only. No third-party packages.
- A hook must never break a session. On any error: log to `hooks.log`, exit 0, print nothing, so the normal dialog appears.
- Never execute Telegram input. It may only become an answer, a decision, or a note to Claude.
- Keep the token out of argv, logs and test output. Tests use `TG_API_BASE` and a fake token, never the real API.
- `hooks.json` timeouts (1320 s) and the 600 s cap in `seconds()` in `tg-bot.py` belong together: `DELAY + REPLY_WINDOW + 60` must stay below the timeout.
- Configuration comes from `CLAUDE_PLUGIN_OPTION_<KEY>` first, then the plain env var (`opt()` in `tg-bot.py`). A new option goes in `plugin.json` `userConfig`, in `opt()`, and in the README table.
- Bump `version` in `plugin.json` for every release: installed copies update only when it changes.
- Conventional Commits, one logical unit per commit.
