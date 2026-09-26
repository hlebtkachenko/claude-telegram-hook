#!/usr/bin/env python3
"""Stdio MCP server with one tool, `notify`: send a Markdown message to the configured Telegram chat.

Newline-delimited JSON-RPC 2.0 on stdin/stdout (initialize, tools/list, tools/call, ping; notifications are
ignored). Configuration comes from the env Claude Code fills from plugin.json (`${user_config.*}`).
"""
import importlib
import json
import os
import sys

for _name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TG_USER_NAME"):
    if os.environ.get(_name, "").startswith("${"):
        os.environ.pop(_name)  # an unset option may arrive unsubstituted; CHAT is read at import below
bot = importlib.import_module("tg-bot")

TOOL = {
    "name": "notify",
    "description": ("Send a message to the user's Telegram. Use it only when the user asked to be notified, for a "
                    "blocker only they can clear, or for a result they asked to hear about. Never for progress updates."),
    "inputSchema": {"type": "object", "properties": {
        "text": {"type": "string", "description": "Message in Markdown (bold, lists, links, code)."}},
        "required": ["text"]},
}


def notify(text):
    if not bot.bot_token() or not bot.CHAT:
        return "error: Telegram bot token or chat ID not configured (/plugin, telegram-hook, Configure options)"
    if not text.strip():
        return "error: empty text"
    try:
        bot.send(bot.cut(text, 4000), [])
    except Exception as e:
        bot.log("tg-mcp", f"notify failed: {type(e).__name__}: {e}")
        return "error: Telegram did not accept the message"  # no exception text: it may quote the request URL
    return "sent"


def handle(req):
    method, params = req.get("method"), req.get("params")
    params = params if isinstance(params, dict) else {}
    if method == "initialize":
        version = params.get("protocolVersion")
        return {"protocolVersion": version if isinstance(version, str) and version else "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "telegram-hook", "version": "0.2.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [TOOL]}
    if method == "tools/call":
        if params.get("name") != "notify":
            raise LookupError(f"unknown tool {params.get('name')}")
        args = params.get("arguments")
        text = args.get("text") if isinstance(args, dict) else None
        result = notify(text) if isinstance(text, str) else "error: text must be a string"
        return {"content": [{"type": "text", "text": result}], "isError": result != "sent"}
    raise NotImplementedError(method)


def main():
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        if not isinstance(req, dict) or "id" not in req:
            continue  # a notification: no reply
        try:
            out = {"jsonrpc": "2.0", "id": req["id"], "result": handle(req)}
        except NotImplementedError:
            out = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "Method not found"}}
        except LookupError as e:
            out = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32602, "message": str(e)}}
        except Exception as e:  # never die, never echo exception text (it may quote a request URL)
            bot.log("tg-mcp", f"internal error: {type(e).__name__}")
            out = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32603, "message": "Internal error"}}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
