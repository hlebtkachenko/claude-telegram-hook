## What and why

<!-- One logical change. Link the issue it closes: Closes #123 -->

## Checks

- [ ] Gate passes: `python3 tests/test_hooks.py && bash tests/test_away.sh`
- [ ] Manifest changed: `claude plugin validate .` and `claude plugin validate plugins/telegram-hook` pass
- [ ] Plugin changed: `version` bumped in `plugins/telegram-hook/.claude-plugin/plugin.json` and CHANGELOG.md updated
- [ ] README / ARCHITECTURE.md updated if behaviour or setup changed
- [ ] No real bot token, chat ID or prompt text in code, tests, logs or screenshots
