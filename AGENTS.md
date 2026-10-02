# AGENTS.md

Claude Code plugin that relays questions, plan approvals and permission prompts to Telegram while the user is away from their computer (macOS, Linux desktops). Read [ARCHITECTURE.md](ARCHITECTURE.md) first.

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
- While `chat_id` is empty every hook stays silent except `tg-pair.py`, which only tells a private sender their own chat ID.
- Downloaded files and the token: a file URL contains the token, so it never goes into events, notes, logs or exceptions stored anywhere.
- The MCP server (`tg-mcp.py`) gets options only through `${user_config.*}` in `plugin.json` `mcpServers.env`; a new option it needs goes there too.
- Configuration comes from `CLAUDE_PLUGIN_OPTION_<KEY>` first, then the plain env var (`opt()` in `tg-bot.py`). A new option goes in `plugin.json` `userConfig`, in `opt()`, and in the README table.
- Bump `version` in `plugin.json` for every release: installed copies update only when it changes.
- Regenerate screenshots with docs/demo/mock.html (`bash docs/demo/render.sh`), never with the real bot. They are mock renderings captioned as such; message text and buttons come from the plugin's builders run offline.
- Conventional Commits, one logical unit per commit.
