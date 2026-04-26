"""Demo: multi-turn medication flow (med list -> stock check) using ChatController.

Prereq: MCP servers running in another terminal:
  python src/servers/mcp_servers.py --all

Run:
  python demo/multi_turn_medication_flow.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chatbot.controller import ChatController


async def main() -> None:
    controller = ChatController()
    conv_id = "demo-med-flow"

    messages = [
        "What are the prescribed medicines for PAT-001?",
        "Check if these medicines are available",
        "Is everything okay?",
    ]

    for msg in messages:
        resp = await controller.handle_message(msg, conversation_id=conv_id)
        print("\nYou>", msg)
        print("\nBot>", resp.answer)


if __name__ == "__main__":
    asyncio.run(main())
