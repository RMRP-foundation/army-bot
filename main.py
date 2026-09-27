import logging
from pathlib import Path

import discord

from bot import Bot
from core.config import TOKEN

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

def main():
    token = TOKEN

    intents = discord.Intents.default()
    intents.members = True          # Для выдачи ролей и ников
    intents.message_content = True  # Для работы префиксных команд (!refresh_...)
    bot = Bot(command_prefix="!", intents=intents, help_command=None)

    bot.run(token)


if __name__ == "__main__":
    main()
