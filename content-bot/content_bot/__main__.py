"""Entry point for the Content Bot container."""

from __future__ import annotations

import logging
import sys

from content_bot.bot import ContentBot
from content_bot.config import BotSettings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = BotSettings.from_env()
    if not settings.bot_token:
        logging.getLogger("content_bot").error("CONTENT_BOT_TOKEN is required")
        sys.exit(2)
    ContentBot(settings).run()


if __name__ == "__main__":
    main()
