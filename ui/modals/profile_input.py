import discord

from core.exceptions import ServiceError
from services.member import MemberService
from ui.modals.labels import name_input, static_label
from utils.helpers import safe_respond
from utils.user_data import clean_static, clean_name


class ProfileInputModal(discord.ui.Modal):
    def __init__(self, need_name: bool = True, need_static: bool = True):
        if need_name and need_static:
            title = "Заполнение профиля"
        elif need_name:
            title = "Введите ваше Имя и Фамилию"
        else:
            title = "Введите ваш статик"

        super().__init__(title=title)
        self.need_name = need_name
        self.need_static = need_static

        if need_name:
            self.name_comp = name_input()
            self.add_item(self.name_comp)
        if need_static:
            self.static_comp = static_label()
            self.add_item(self.static_comp)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            full_name = clean_name(self.name_comp.value) if self.need_name else None
            static_id = clean_static(self.static_comp.component.value) if self.need_static else None

            await MemberService.update_self_profile(
                interaction=interaction,
                full_name=full_name,
                static_id=static_id,
            )
            await safe_respond(interaction, "✅ Данные профиля сохранены. Повторите ваше действие.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)