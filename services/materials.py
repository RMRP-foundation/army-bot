import logging

import discord

from core import config
from database.counters import get_next_id
from database.models import MaterialsReport, User
from ui.embeds.materials import materials_embed
from utils.helpers import safe_publish_request, build_mentions

logger = logging.getLogger(__name__)


class MaterialsService:

    @staticmethod
    async def create_request(
        user_id: int,
        full_name: str,
        quantity: int,
        evidence: str,
    ) -> MaterialsReport:
        """Создает и сохраняет отчет о продаже материалов в базе данных."""
        new_id = await get_next_id("materials_reports")

        request = MaterialsReport(
            id=new_id,
            user_id=user_id,
            full_name=full_name,
            quantity=quantity,
            evidence=evidence,
        )
        await request.create()
        return request

    @staticmethod
    async def publish_request(
        interaction: discord.Interaction,
        request: MaterialsReport,
        user: User,
    ) -> None:
        """Формирует и публикует отчет в текущий канал."""
        embed = materials_embed(request, user)

        await safe_publish_request(
            channel=interaction.channel,
            request=request,
            embed=embed,
            content=build_mentions(request.user_id, config.MATERIALS_MENTIONS),
        )

        from cogs.materials import update_bottom_message
        await update_bottom_message(interaction.client)

    @classmethod
    async def submit_materials(
        cls,
        interaction: discord.Interaction,
        user_db: User,
        quantity: int,
        evidence: str,
    ) -> None:
        """Публичный оркестратор отправки отчета о материалах."""
        request = await cls.create_request(
            user_id=interaction.user.id,
            full_name=user_db.full_name or interaction.user.display_name,
            quantity=quantity,
            evidence=evidence,
        )

        await cls.publish_request(
            interaction=interaction,
            request=request,
            user=user_db,
        )