import logging
import os
import traceback

import discord
import sentry_sdk
from discord import app_commands

from core.exceptions import ModalInputRequired, SilentCheckFailure
from utils.helpers import safe_respond

_original_view_on_error = discord.ui.View.on_error


async def _custom_view_on_error(
    self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
):
    """Глобальный обработчик ошибок View - игнорирует ModalInputRequired."""
    if isinstance(error, ModalInputRequired):
        return

    sentry_sdk.capture_exception(error)

    await _original_view_on_error(self, interaction, error, item)

async def on_tree_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError | str
):
    if isinstance(error, ModalInputRequired):
        return

    traceback_info = traceback.format_exc()
    error_id = os.urandom(4).hex()

    if isinstance(error, app_commands.CommandOnCooldown):
        await safe_respond(interaction, f"Команда ещё недоступна! Попробуйте ещё раз через **{error.retry_after:.2f}** сек!")
    elif isinstance(error, app_commands.MissingPermissions):
        await safe_respond(interaction, "У вас нет прав")
    elif isinstance(error, app_commands.CommandInvokeError) or isinstance(
        error, str
    ):
        logging.error(f"[{error_id}] Error: {traceback_info}")
        embed = discord.Embed(
            title=f"💀 Произошла ошибка [{error_id}]",
            description="Повторите действие. При повторении передайте администрации ID ошибки.",
            color=discord.Color.dark_grey(),
        )
        await safe_respond(interaction, embed=embed)
    else:
        logging.error(f"[{error_id}] Error: {traceback_info}")
        await safe_respond(interaction, f"### Произошла ошибка [{error_id}]")

async def on_command_error(ctx, error):
    if isinstance(error, SilentCheckFailure):
        return
