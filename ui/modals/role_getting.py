import datetime

import discord.ui

import config
from config import nickname_regex
from database.counters import get_next_id
from database.models import (
    ExtendedRoleData,
    RoleData,
    RoleRequest,
    RoleType,
)
from ui.modals.labels import (
    name_component,
    static_label,
)
from ui.views.role_getting import RoleManagementButton
from utils.user_data import formatted_static_to_int


async def _reject_stale_pending(interaction: discord.Interaction) -> bool:
    """
    Проверяет старую PENDING заявку пользователя.
    Если она старше 24ч — атомарно отклоняет и возвращает True (можно подавать).
    Если моложе 24ч — отвечает пользователю и возвращает False.
    Если заявок нет — возвращает True.
    """
    old_pending = await RoleRequest.find_one(
        RoleRequest.user == interaction.user.id,
        RoleRequest.status == "PENDING",
    )

    if old_pending is None:
        return True

    age = discord.utils.utcnow() - old_pending.sent_at.replace(tzinfo=datetime.timezone.utc)

    if age < datetime.timedelta(hours=24):
        retry_at = old_pending.sent_at.replace(tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=24)
        await interaction.response.send_message(
            f"### ⏳ Заявка уже подана\n"
            f"Повторно подать можно {discord.utils.format_dt(retry_at, 'R')}, "
            f"если текущая не будет рассмотрена.",
            ephemeral=True,
        )
        return False

    await RoleRequest.get_pymongo_collection().update_one(
        {"_id": old_pending.id, "status": "PENDING"},
        {"$set": {"status": "REJECTED"}},
    )

    if old_pending.message_id:
        try:
            msg = await interaction.channel.fetch_message(old_pending.message_id)
            old_pending.status = "REJECTED"
            await msg.edit(embed=await old_pending.to_embed(), view=None)
        except Exception:
            pass

    return True


class RoleRequestModal(discord.ui.Modal, title="Заявление на получение роли"):
    name = name_component()
    static_id = static_label()
    footer = discord.ui.TextDisplay(
        "Если вы что-то не понимаете, обратитесь к военнослужащему за помощью."
    )

    def __init__(self, user_name: str | None, static_id: str | None):
        super().__init__()
        self.name.default = user_name
        self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        if not await _reject_stale_pending(interaction):
            return

        try:
            static_id = formatted_static_to_int(self.static_id.component.value)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "### Вы ввели некорректный статик. "
                "Правильный формат: ХХХ-ХХХ. Пример: 537-328.",
                ephemeral=True,
            )
            return

        if not nickname_regex.match(self.name.value):
            await interaction.response.send_message(
                "### Вы ввели некорректное имя и фамилию. "
                "Правильный формат: Иван Иванов.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.send_message(
                "### Заявление отправлено на рассмотрение.", ephemeral=True
            )
        except discord.NotFound:
            pass

        request = RoleRequest(
            id=await get_next_id("role_requests"),
            user=interaction.user.id,
            data=RoleData(full_name=self.name.value, static_id=static_id),
        )
        await request.create()

        view = discord.ui.View(timeout=None)
        view.add_item(RoleManagementButton("approve", request.id))
        view.add_item(RoleManagementButton("reject", request.id))
        sent_msg = await interaction.channel.send(
            content=f"-# ||<@{interaction.user.id}>||",
            embed=await request.to_embed(),
            view=view,
        )
        request.message_id = sent_msg.id
        await request.save()

        from cogs.role_getting import update_bottom_message

        await update_bottom_message(interaction.client)


class KMBRequestModal(discord.ui.Modal, title="Заявление на КМБ"):
    name = name_component()
    static_id = static_label()
    footer = discord.ui.TextDisplay(
        "Если вы что-то не понимаете, обратитесь к военнослужащему за помощью."
    )

    def __init__(self, user_name: str | None, static_id: str | None):
        super().__init__()
        self.name.default = user_name
        self.static_id.component.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        if not await _reject_stale_pending(interaction):
            return

        try:
            static_id = formatted_static_to_int(self.static_id.component.value)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "### Вы ввели некорректный статик. "
                "Правильный формат: ХХХ-ХХХ. Пример: 537-328.",
                ephemeral=True,
            )
            return

        if not nickname_regex.match(self.name.value):
            await interaction.response.send_message(
                "### Вы ввели некорректное имя и фамилию. "
                "Правильный формат: Иван Иванов.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.send_message(
                "### Заявление отправлено на рассмотрение.", ephemeral=True
            )
        except discord.NotFound:
            pass

        request = RoleRequest(
            id=await get_next_id("role_requests"),
            user=interaction.user.id,
            role_type=RoleType.KMB,
            data=RoleData(
                full_name=self.name.value,
                static_id=static_id,
            )
        )
        await request.create()

        view = discord.ui.View(timeout=None)
        view.add_item(RoleManagementButton("approve", request.id))
        view.add_item(RoleManagementButton("reject", request.id))

        sent_msg = await interaction.channel.send(
            content=f"-# ||<@{interaction.user.id}>||",
            embed=await request.to_embed(),
            view=view,
        )

        request.message_id = sent_msg.id
        await request.save()

        from cogs.role_getting import update_bottom_message

        await update_bottom_message(interaction.client)


class SupplyAccessModal(discord.ui.Modal, title="Заявление на доступ к поставке"):
    name = discord.ui.TextInput(
        label="Ваше имя и фамилия", placeholder="Иван Иванов", max_length=25
    )
    static_id = discord.ui.TextInput(
        label="Ваш статик", placeholder="XXX-XXX", max_length=7
    )
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

    def __init__(self, user_name: str | None, static_id: str | None):
        super().__init__()
        self.name.default = user_name
        self.static_id.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        if not await _reject_stale_pending(interaction):
            return

        try:
            static_id = formatted_static_to_int(self.static_id.value)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "### Вы ввели некорректный статик. "
                "Правильный формат: ХХХ-ХХХ. Пример: 537-328.",
                ephemeral=True,
            )
            return

        try:
            await interaction.response.send_message(
                "### Заявление отправлено на рассмотрение.", ephemeral=True
            )
        except discord.NotFound:
            pass

        request = RoleRequest(
            id=await get_next_id("role_requests"),
            user=interaction.user.id,
            role_type=RoleType.SUPPLY_ACCESS,
            extended_data=ExtendedRoleData(
                full_name=self.name.value,
                static_id=static_id,
                faction=self.faction.value,
                rank_position=self.rank_position.value,
                certificate_link=self.certificate_link.value,
            ),
        )
        await request.create()

        # Тегаем автора + Подполковника и выше
        colonel_mentions = " ".join(
            f"<@&{role_id}>"
            for rank, role_id in config.RANK_ROLES.items()
            if config.RANKS.index(rank) >= config.RankIndex.LIEUTENANT_COLONEL
        )

        view = discord.ui.View(timeout=None)
        view.add_item(RoleManagementButton("approve", request.id))
        view.add_item(RoleManagementButton("reject", request.id))
        sent_msg = await interaction.channel.send(
            content=f"-# ||<@{interaction.user.id}> {colonel_mentions}||",
            embed=await request.to_embed(),
            view=view,
        )

        request.message_id = sent_msg.id
        await request.save()

        from cogs.role_getting import update_bottom_message

        await update_bottom_message(interaction.client)


class GovEmployeeModal(discord.ui.Modal, title="Заявление на роль Гос. сотрудник"):
    name = discord.ui.TextInput(
        label="Ваше имя и фамилия", placeholder="Иван Иванов", max_length=25
    )
    static_id = discord.ui.TextInput(
        label="Ваш статик", placeholder="XXX-XXX", max_length=7
    )
    faction = discord.ui.TextInput(
        label="Ваша фракция", placeholder="ФСВНГ, МО и т.д.", max_length=50
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

    def __init__(self, user_name: str | None, static_id: str | None):
        super().__init__()
        self.name.default = user_name
        self.static_id.default = static_id

    async def on_submit(self, interaction: discord.Interaction):
        if not await _reject_stale_pending(interaction):
            return

        try:
            static_id = formatted_static_to_int(self.static_id.value)
        except (ValueError, TypeError):
            await interaction.response.send_message(
                "### Вы ввели некорректный статик. "
                "Правильный формат: ХХХ-ХХХ. Пример: 537-328.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "### Заявление отправлено на рассмотрение.", ephemeral=True
        )

        request = RoleRequest(
            id=await get_next_id("role_requests"),
            user=interaction.user.id,
            role_type=RoleType.GOV_EMPLOYEE,
            extended_data=ExtendedRoleData(
                full_name=self.name.value,
                static_id=static_id,
                faction=self.faction.value,
                rank_position=self.rank_position.value,
                purpose=self.purpose_and_certificate.value,
            ),
        )
        await request.create()

        # Тегаем автора + Подполковника и выше
        colonel_mentions = " ".join(
            f"<@&{role_id}>"
            for rank, role_id in config.RANK_ROLES.items()
            if config.RANKS.index(rank) >= config.RankIndex.LIEUTENANT_COLONEL
        )

        view = discord.ui.View(timeout=None)
        view.add_item(RoleManagementButton("approve", request.id))
        view.add_item(RoleManagementButton("reject", request.id))
        sent_msg = await interaction.channel.send(
            content=f"-# ||<@{interaction.user.id}> {colonel_mentions}||",
            embed=await request.to_embed(),
            view=view,
        )

        request.message_id = sent_msg.id
        await request.save()

        from cogs.role_getting import update_bottom_message

        await update_bottom_message(interaction.client)
