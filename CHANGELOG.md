# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org).

## [1.0.0] - 2026-10-02

First public release.

### Fixed
- `tg-failure.py` (StopFailure hook) failed to parse on Python 3.9 to 3.11 (a backslash inside an f-string expression), so API error pings never went out there. First fixed in 0.2.2 (#3).
- `tg-away.py` passes the token saved by the MCP server to `tg-ping.sh`; a token set in `/plugin` wins over the saved copy; the token file is read robustly; pairing waits for the token.

### Added
- CI runs both test suites on Python 3.9 and the latest 3.x; Dependabot keeps the pinned GitHub Actions current.
- README rewritten: quick start with the one-step `/plugin install --marketplace` command, how it works, mock screenshots, comparison, FAQ.
- `llms.txt`, CONTRIBUTING.md, CODE_OF_CONDUCT.md (Contributor Covenant 2.1), SECURITY.md, issue and pull request templates, and a plugin README for plugin directories.
- `docs/demo`: mock screenshots and the social preview, built from the plugin's own message builders with sample data.

### Changed
- Plugin and marketplace descriptions lead with what the plugin does; new keywords, marketplace category and tags.

## [0.2.1] - 2026-09-27

### Fixed
- Claude Code passes secret options only to MCP servers, so hooks never saw the bot token: the MCP server now hands it to the hooks through a 0600 `bot_token` file in the state directory.
- MCP server validates parameters and survives internal errors.
- Always allow offers only `addRules` allow rules for local settings or the session, and the ping lists them.
- Prompt text stays out of process arguments.
- The shell command is the permission headline; truncated details are marked "(truncated, check in app)".
- Link previews are disabled on every message the bot sends.
- `TG_API_BASE` and the test fakes are honoured only for a local fake server.
- Per-user state directory: symlinks and directories owned by another user are refused, and the log is opened without following links.

### Security
- CI runs with a read-only token and actions pinned to commit SHAs.

## [0.2.0] - 2026-09-27

### Added
- Linux presence detection via `xprintidle` (X11) or GNOME's Mutter idle monitor (Wayland).
- Closed pings lose their buttons; Telegram answers get a thumbs-up reaction.
- Always allow button that echoes Claude Code's own permission suggestions.
- Photo and file replies, downloaded for Claude to read.
- A reply to an earlier ping wakes a listening session.
- StopFailure hook pings on API errors.
- Chat ID pairing from a SessionStart hook while `chat_id` is empty.
- `notify` MCP tool so Claude can message you.
- Test suites against a fake Telegram server, CI, README, ARCHITECTURE.md and AGENTS.md.

### Fixed
- The token is redacted in the log, hooks skip when unconfigured, and option parsing is hardened.

## [0.1.0] - 2026-09-26

### Added
- First version of the telegram-hook plugin: questions, plan approvals and permission prompts relayed to Telegram with answer buttons while you are away from your Mac (`tg-ask.py`, `tg-away.py`, `tg-stop.py`, the `tg-bot.py` poller and `tg-ping.sh`).

[1.0.0]: https://github.com/hlebtkachenko/claude-telegram-hook/releases/tag/v1.0.0
[0.2.1]: https://github.com/hlebtkachenko/claude-telegram-hook/compare/e8adf63...a86c914
[0.2.0]: https://github.com/hlebtkachenko/claude-telegram-hook/compare/43858d5...e8adf63
[0.1.0]: https://github.com/hlebtkachenko/claude-telegram-hook/commit/43858d5
