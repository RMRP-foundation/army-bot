import logging
import os
import traceback

import discord
from discord import app_commands

from utils.exceptions import StaticInputRequired

_original_view_on_error = discord.ui.View.on_error


async def _custom_view_on_error(
    self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
):
    """Глобальный обработчик ошибок View - игнорирует StaticInputRequired."""
    if isinstance(error, StaticInputRequired):
        return
    await _original_view_on_error(self, interaction, error, item)

async def respond(interaction: discord.Interaction, **kwargs):
    """Универсальный метод ответа на взаимодействие"""
    if interaction.response.is_done():
        return await interaction.followup.send(**kwargs)
    return await interaction.response.send_message(**kwargs)

async def on_tree_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError | str
):
    if isinstance(error, StaticInputRequired):
        return

    traceback_info = traceback.format_exc()
    error_id = os.urandom(4).hex()

    if isinstance(error, app_commands.CommandOnCooldown):
        await respond(interaction,
                      content=f"Команда ещё недоступна! Попробуйте ещё раз через **{error.retry_after:.2f}** сек!",
                      ephemeral=True
                      )
    elif isinstance(error, app_commands.MissingPermissions):
        await respond(interaction, content="У вас нет прав", ephemeral=True)
    elif isinstance(error, app_commands.CommandInvokeError) or isinstance(
        error, str
    ):
        logging.error(f"[{error_id}] Error: {traceback_info}")
        error_msg = str(error.original if isinstance(error, app_commands.CommandInvokeError) else error)
        description = (error_msg[:3997] + '...') if len(error_msg) > 4000 else error_msg
        embed = discord.Embed(
            title=f"💀 Произошла ошибка [{error_id}]",
            description=description,
            color=discord.Color.dark_grey(),
        )
        await respond(interaction, embed=embed, ephemeral=True)
    else:
        logging.error(f"[{error_id}] Error: {traceback_info}")
        await respond(interaction,
                      content=f"### Произошла ошибка [{error_id}]",
                      ephemeral=True
                      )