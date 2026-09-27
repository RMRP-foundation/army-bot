import discord

from core.exceptions import ServiceError, ModalInputRequired
from database.models import User

class AuthorizationService:

    @staticmethod
    async def require_active_soldier(interaction: discord.Interaction) -> User:
        """Проверяет, состоит ли пользователь на службе и заполнены ли его данные (статик и ник).

        Returns:
            User: Полностью валидный объект пользователя из БД.

        Raises:
            ServiceError: Если пользователь не прошел проверку.
        """
        user = await User.get_active_soldier(interaction.user.id)

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
