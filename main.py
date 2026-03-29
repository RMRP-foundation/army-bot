import logging
from pathlib import Path

import discord

import config
from bot import Bot

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


def main():
    token = config.TOKEN

    intents = discord.Intents.default()
    intents.members = True          # Для выдачи ролей и ников
    intents.message_content = True  # Для работы префиксных команд (!refresh_...)
    bot = Bot(command_prefix="!", intents=intents)

    bot.run(token)


if __name__ == "__main__":
    main()
