"""Set safe chat defaults for the PC15 Open WebUI Computer instance."""

from __future__ import annotations

import asyncio
import os

from cptr.models.config import Config


DEFAULT_CHAT_MODEL = os.environ.get(
    "MAE_COMPUTER_DEFAULT_MODEL", "lcdash/qwen3.5:27b"
)


async def main() -> None:
    await Config.upsert({"chat.default_model": DEFAULT_CHAT_MODEL})
    configured = await Config.get("chat.default_model")
    if configured != DEFAULT_CHAT_MODEL:
        raise RuntimeError("PC15 Computer default model did not persist")
    print(f"PC15 Computer default chat model: {configured}")


if __name__ == "__main__":
    asyncio.run(main())
