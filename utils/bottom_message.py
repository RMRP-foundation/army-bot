import asyncio
import logging

import discord

from database.models import BottomMessage

logger = logging.getLogger(__name__)

_DEBOUNCE_DELAY = 3.0  # секунды

# channel_id -> pending asyncio.Task
_pending_tasks: dict[int, asyncio.Task] = {}


async def _execute_update(
    bot, channel_id: int, view: discord.ui.View, embed: discord.Embed | None
) -> None:
    bottom_message = await BottomMessage.find_one(
        BottomMessage.channel_id == channel_id
    )

    if bottom_message:
        try:
            await bot.http.delete_message(channel_id, bottom_message.message_id)
        except discord.NotFound:
            logger.debug(f"Bottom message {bottom_message.message_id} already deleted")
        except discord.Forbidden:
            logger.warning(f"No permission to delete message in channel {channel_id}")
        except Exception as e:
            logger.error(f"Failed to delete bottom message: {e}")

    channel = bot.get_channel(channel_id)
    if not channel:
        logger.warning(f"Channel {channel_id} not found")
        return

    new_message = await channel.send(embed=embed, view=view)

    if bottom_message:
        bottom_message.message_id = new_message.id
        await bottom_message.save()
    else:
        new_bottom_message = BottomMessage(
            channel_id=channel_id, message_id=new_message.id
        )
        await new_bottom_message.create()


async def update_bottom_message(
    bot, channel_id: int, view: discord.ui.View, embed: discord.Embed | None = None
) -> None:
    """
    Обновляет "закрепленное" сообщение внизу канала.

    Использует дебаунс: при частых вызовах для одного канала
    выполняется только последний вызов через _DEBOUNCE_DELAY секунд после него.
    Это предотвращает флуд Discord API при массовых заявках.
    """
    existing = _pending_tasks.get(channel_id)
    if existing is not None and not existing.done():
        existing.cancel()

    async def _run() -> None:
        try:
            await asyncio.sleep(_DEBOUNCE_DELAY)
            await _execute_update(bot, channel_id, view, embed)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                f"Unhandled error in bottom message update for channel {channel_id}: {e}"
            )

    task = asyncio.create_task(_run())
    _pending_tasks[channel_id] = task

    def _cleanup(t: asyncio.Task) -> None:
        if _pending_tasks.get(channel_id) is t:
            del _pending_tasks[channel_id]

    task.add_done_callback(_cleanup)
