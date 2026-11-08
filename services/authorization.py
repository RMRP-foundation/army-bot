import discord

from core.exceptions import ServiceError, ModalInputRequired
from database.models import User
from services import presence
from utils.permissions import is_service_account


class AuthorizationService:

    @staticmethod
    async def require_active_soldier(interaction: discord.Interaction) -> User:
        """Проверяет, состоит ли пользователь на службе и заполнены ли его данные (статик и ник).

        Returns:
            User: Полностью валидный объект пользователя из БД.

        Raises:
            ServiceError: Если пользователь не прошел проверку.
        """
        user = await User.get_by_discord_id(interaction.user.id)

        if user is None and isinstance(interaction.user, discord.Member):
            user, _ = await presence.init_user(interaction.user)

        if user is not None and user.rank is None and not is_service_account(interaction.user.id):
            user = None

        if user is None:
            raise ServiceError("❌ Вы не состоите на службе.")

        missing_static = user.pre_inited and user.static is None
        missing_name = user.pre_inited and (not user.first_name or not user.last_name)

        if missing_static or missing_name:
            from ui.modals.profile_input import ProfileInputModal
            await interaction.response.send_modal(
                ProfileInputModal(need_name=missing_name, need_static=missing_static)
            )
            raise ModalInputRequired()

        return user