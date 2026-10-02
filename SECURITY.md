# Security policy

Please report vulnerabilities privately through GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. Don't open a public issue for a security problem, and never include a real bot token.

Include the steps to reproduce, the plugin version (`version` in `plugins/telegram-hook/.claude-plugin/plugin.json`, or `/plugin` in Claude Code), your OS and desktop, and your `python3` and Claude Code versions.

Only the latest release is supported.

## Scope

In scope:

- **Token handling:** the bot token reaching argv, logs, events, notes to Claude, exceptions, or any file other than the 0600 `bot_token` copy in the state directory.
- **chat_id authorization:** any way for a chat or user other than the configured `chat_id` to answer a prompt, approve a permission, or wake a session; pairing revealing anything beyond a private sender's own chat ID.
- **The state directory:** symlink or ownership tricks, file modes, or other users reading or planting files under `claude-telegram-hook/`.
- Telegram input being executed, or an Always allow tap saving rules other than Claude Code's own local or session suggestions.

Out of scope:

- Someone holding your unlocked phone with your Telegram session: they can approve prompts while you are away, the same as someone at your unlocked computer.
- Telegram bot chats not being end-to-end encrypted (documented in the README).
