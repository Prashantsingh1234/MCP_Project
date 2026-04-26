"""CLI chatbot for MCPDischarge.

Prereq: MCP servers running:
  python src/servers/mcp_servers.py --all

Run:
  python src/chatbot/cli.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Ensure project root on path when running as a script.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chatbot.llm_controller import LLMChatController  # noqa: E402


async def main():
    controller = LLMChatController()
    conversation_id = "cli"
    print("MCPDischarge Chatbot (type 'exit' to quit)")
    while True:
        try:
            msg = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return
        if not msg:
            continue
        if msg.lower() in {"exit", "quit"}:
            print("Bye.")
            return
        resp = await controller.handle_message(msg, conversation_id=conversation_id)
        print(f"\nBot> {resp.answer}")


if __name__ == "__main__":
    asyncio.run(main())
