import logging
from pathlib import Path

import discord
from discord.ext import commands

from bot import Bot
from core.config import TOKEN
from core.exceptions import ModalInputRequired

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(Path(__file__).parent / "bot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger(__name__)

logging.getLogger("discord.client").addFilter(
    lambda r: "PyNaCl" not in r.getMessage()
)

logging.getLogger("discord.ui.view").addFilter(
    lambda r: not (r.exc_info and isinstance(getattr(r.exc_info[1], "original", r.exc_info[1]), ModalInputRequired))
)

def main():
    token = TOKEN

    intents = discord.Intents.none()
    intents.guilds = True
    bot = Bot(command_prefix=commands.when_mentioned, intents=intents, help_command=None)

    bot.run(token)


if __name__ == "__main__":
    main()
