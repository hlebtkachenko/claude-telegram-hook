# Contributing to telegram-hook

Thanks for helping. telegram-hook is a small, dependency-free Claude Code plugin; changes that keep it that way are the easiest to accept.

## Before you start

- **Bugs:** open an issue with the bug template: prompt type, OS and desktop, `python3`, `claude` and plugin versions, and the relevant `hooks.log` lines (remove chat IDs and prompt text; never paste a token).
- **Features:** open an issue first for anything larger than a small fix, so we can agree on the shape before you build it.
- **Security issues:** don't open a public issue; see [SECURITY.md](SECURITY.md).

## Rules

- Standard-library Python and bash only. No third-party packages.
- A hook must never break a session: on any error it logs to `hooks.log`, exits 0 and prints nothing, so the normal dialog appears.
- Telegram input is never executed. It may only become an answer, a decision, or a note to Claude.
- Tests use the local fake Telegram server (`TG_API_BASE`) and a fake token only, never the real API or a real bot.
- Bump `version` in `plugins/telegram-hook/.claude-plugin/plugin.json` for every change to the plugin: installed copies update only when it changes. Add a line to [CHANGELOG.md](CHANGELOG.md).
- Screenshots in `docs/images` are mock renderings: regenerate them with `bash docs/demo/render.sh`, never with the real bot.

[AGENTS.md](AGENTS.md) has the full rule list and [ARCHITECTURE.md](ARCHITECTURE.md) explains how the pieces fit together.

## Gate

Run before every commit (CI runs the same on Python 3.9 and the latest 3.x):

```bash
python3 tests/test_hooks.py && bash tests/test_away.sh
```

`tests/test_away.sh` needs `jq`. After changing a manifest, also run `claude plugin validate .` and `claude plugin validate plugins/telegram-hook`.

## Pull requests

- One logical change per pull request; [Conventional Commits](https://www.conventionalcommits.org) for commit messages (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`, `ci:`).
- Update README, ARCHITECTURE.md and CHANGELOG.md when behaviour or setup changes.
- No real token, chat ID or prompt text in code, tests, logs or screenshots.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE) and that you follow the [Code of Conduct](CODE_OF_CONDUCT.md).
