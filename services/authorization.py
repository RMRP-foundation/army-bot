import discord

from core.exceptions import ServiceError, ModalInputRequired
from database.models import User
from services import presence
from utils.permissions import is_service_account


class AuthorizationService:

    @staticmethod
    async def require_active_soldier(interaction: discord.Interaction) -> User:
        """Проверяет, состоит ли пользователь на службе и заполнены ли его данные."""
        user = await User.get_by_discord_id(interaction.user.id)

        if user is None and isinstance(interaction.user, discord.Member):
            user, _ = await presence.init_user(interaction.user)

        if user is not None and user.rank is None and not is_service_account(interaction.user.id):
            user = None

        if user is None:
            raise ServiceError("❌ Вы не состоите на службе.")

        if user.pre_inited and user.static is None:
            from ui.modals.profile_input import StaticInputModal
            await interaction.response.send_modal(StaticInputModal())
            raise ModalInputRequired()

        if user.pre_inited and (not user.first_name or not user.last_name):
            from ui.modals.profile_input import NameInputModal
            await interaction.response.send_modal(NameInputModal())
            raise ModalInputRequired()

        return user