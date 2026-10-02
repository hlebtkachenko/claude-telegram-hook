# telegram-hook

Answer Claude Code from your phone. When you step away from your Mac or Linux desktop, Claude Code's questions, plan approvals and permission prompts arrive in your private Telegram chat with answer buttons. Tap an option, Allow, Always allow or Deny, or reply with text, a photo or a file, and your answer goes straight back into the running session. Nothing is sent while you are at the computer, and Telegram input is never executed.

![A permission prompt with Allow, Always allow and Deny buttons, and a multi-select question with one button per option](https://raw.githubusercontent.com/hlebtkachenko/claude-telegram-hook/main/docs/images/hero.png)

<sub>Illustration: mock rendering of the plugin's messages with sample data, not a real Telegram capture.</sub>

## Install

```
/plugin install telegram-hook --marketplace hlebtkachenko/claude-telegram-hook
```

Create a bot with [@BotFather](https://t.me/BotFather), enter its token when asked, and leave `chat_id` empty: in the next session the bot replies to a private message with your chat ID, which you paste into the `chat_id` option.

## What it needs

macOS or a Linux desktop (it reads keyboard and mouse idle time), `python3` 3.9 or later, `curl`, and a Telegram bot that nothing else polls. It talks only to `api.telegram.org`, by long polling, with no inbound port and no relay server.

Full guide, options, security notes and FAQ: [github.com/hlebtkachenko/claude-telegram-hook](https://github.com/hlebtkachenko/claude-telegram-hook).

Not affiliated with or endorsed by Anthropic or Telegram. MIT License.
