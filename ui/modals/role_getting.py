import discord.ui

from core.exceptions import ServiceError
from database.models import ExtendedRoleData, RoleData, RoleType
from services.role_getting import RoleService
from ui.modals.labels import (
    name_input,
    static_label,
)
from utils.helpers import safe_respond
from utils.user_data import clean_name, clean_static


class RoleRequestModal(discord.ui.Modal, title="Заявление на получение роли"):
    name = name_input()
    static_id = static_label()
    footer = discord.ui.TextDisplay(
        "Если вы что-то не понимаете, обратитесь к военнослужащему за помощью."
    )

    def __init__(self, user_name: str | None = None, static_id: str | None = None):
        super().__init__()
        if user_name:
            self.name.default = user_name
        if static_id:
            self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.role_getting import RoleManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...")

            await RoleService.submit_role(
                interaction=interaction,
                role_type=RoleType.ARMY,
                data=RoleData(
                    full_name=clean_name(self.name.value),
                    static_id=clean_static(self.static_id.component.value),
                ),
                view_factory=RoleManagementView,
            )
            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.")
        except ServiceError as error:
            await safe_respond(interaction, error.message)


class KMBRequestModal(discord.ui.Modal, title="Заявление на КМБ"):
    name = name_input()
    static_id = static_label()
    footer = discord.ui.TextDisplay(
        "Если вы что-то не понимаете, обратитесь к военнослужащему за помощью."
    )

    def __init__(self, user_name: str | None = None, static_id: str | None = None):
        super().__init__()
        if user_name:
            self.name.default = user_name
        if static_id:
            self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.role_getting import RoleManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...")

            await RoleService.submit_role(
                interaction=interaction,
                role_type=RoleType.KMB,
                data=RoleData(
                    full_name=clean_name(self.name.value),
                    static_id=clean_static(self.static_id.component.value),
                ),
                view_factory=RoleManagementView,
            )
            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.", ephemeral=True)
        except ServiceError as error:
            await safe_respond(interaction, error.message, ephemeral=True)


class SupplyAccessModal(discord.ui.Modal, title="Заявление на доступ к поставке"):
    name = name_input()
    static_id = static_label()
    faction = discord.ui.TextInput(
        label="Ваша фракция", placeholder="ФСВНГ, МО и т.д.", max_length=50
    )
    rank_position = discord.ui.TextInput(
        label="Звание, должность",
        placeholder="Полковник, Командир роты",
        max_length=100,
    )
    certificate_link = discord.ui.TextInput(
        label="Ссылка на удостоверение",
        placeholder="https://imgur.com/...",
        max_length=200,
    )

    def __init__(self, user_name: str | None = None, static_id: str | None = None):
        super().__init__()
        if user_name:
            self.name.default = user_name
        if static_id:
            self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.role_getting import RoleManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...", ephemeral=True)

            await RoleService.submit_role(
                interaction=interaction,
                role_type=RoleType.SUPPLY_ACCESS,
                extended_data=ExtendedRoleData(
                    full_name=clean_name(self.name.value),
                    static_id=clean_static(self.static_id.component.value),
                    faction=self.faction.value.strip(),
                    rank_position=self.rank_position.value.strip(),
                    certificate_link=self.certificate_link.value.strip(),
                ),
                view_factory=RoleManagementView,
            )
            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.", ephemeral=True)
        except ServiceError as error:
            await safe_respond(interaction, error.message, ephemeral=True)


class GovEmployeeModal(discord.ui.Modal, title="Заявление на роль Гос. сотрудник"):
    name = name_input()
    static_id = static_label()
    faction = discord.ui.TextInput(
        label="Ваша фракция", placeholder="ФСВНГ, МО и т.д.", max_length=20
    )
    rank_position = discord.ui.TextInput(
        label="Звание, должность",
        placeholder="Полковник, Командир роты",
        max_length=100,
    )
    purpose_and_certificate = discord.ui.TextInput(
        label="Цель и ссылка на удостоверение",
        placeholder="Цель: ...\nСсылка: https://imgur.com/...",
        style=discord.TextStyle.paragraph,
        max_length=500,
    )

    def __init__(self, user_name: str | None = None, static_id: str | None = None):
        super().__init__()
        if user_name:
            self.name.default = user_name
        if static_id:
            self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        from ui.views.role_getting import RoleManagementView

        try:
            await safe_respond(interaction, "⏳ Заявление отправляется...", ephemeral=True)

            await RoleService.submit_role(
                interaction=interaction,
                role_type=RoleType.GOV_EMPLOYEE,
                extended_data=ExtendedRoleData(
                    full_name=clean_name(self.name.value),
                    static_id=clean_static(self.static_id.component.value),
                    faction=self.faction.value.strip(),
                    rank_position=self.rank_position.value.strip(),
                    purpose=self.purpose_and_certificate.value.strip(),
                ),
                view_factory=RoleManagementView,
            )
            await safe_respond(interaction, "### Заявление отправлено на рассмотрение.", ephemeral=True)
        except ServiceError as error:
            await safe_respond(interaction, error.message, ephemeral=True)