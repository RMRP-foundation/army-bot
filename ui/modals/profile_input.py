import discord

from core.exceptions import ServiceError
from services.member import MemberService
from ui.modals.labels import name_input, clean_static, clean_name, static_label
from utils.helpers import safe_respond


class StaticInputModal(discord.ui.Modal, title="Введите ваш статик"):
    static_label = static_label()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            static_id = clean_static(self.static_label.component.value)
            await MemberService.update_self_static(interaction, static_id)
            await safe_respond(interaction, "✅ Статик сохранен. Теперь вы можете повторить действие.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class NameInputModal(discord.ui.Modal, title="Введите ваше Имя и Фамилию"):
    name = name_input()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            full_name = clean_name(self.name.value)
            await MemberService.update_self_name(interaction, full_name)
            await safe_respond(interaction, "✅ Имя успешно сохранено. Теперь вы можете повторить действие.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)