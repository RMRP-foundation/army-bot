import datetime
import logging
import re
from typing import Any

import discord
from beanie.odm.operators.find.comparison import In
from discord import Interaction, InteractionResponse
from discord._types import ClientT

import config
import texts
from database import divisions
from database.models import RoleRequest, RoleType, User
from ui.views.indicators import indicator_view
from utils.audit import AuditAction, audit_logger
from utils.exceptions import StaticInputRequired
from utils.notifications import notify_role_approved, notify_role_rejected
from utils.user_data import format_game_id, get_initiator, get_user_defaults

logger = logging.getLogger(__name__)

ROLE_DISPLAY_NAMES = {
    RoleType.ARMY: "ВС РФ",
    RoleType.KMB: "КМБ",
    RoleType.SUPPLY_ACCESS: "Доступ к поставке",
    RoleType.GOV_EMPLOYEE: "Гос. сотрудник",
}

ROLE_REQUIRED_RANKS = {
    RoleType.ARMY: "Младший лейтенант",
    RoleType.KMB: "Младший лейтенант",
    RoleType.SUPPLY_ACCESS: "Подполковник",
    RoleType.GOV_EMPLOYEE: "Полковник",
}


async def _check_can_apply(interaction: discord.Interaction, check_blacklist: bool = False) -> bool:
    opened_request = await RoleRequest.find_one(
        RoleRequest.user == interaction.user.id,
        In(RoleRequest.status, ["PENDING", "PROCESSING"])
    )
    if opened_request is not None:
        await interaction.response.send_message(
            "### У вас уже есть открытое заявление на рассмотрении.\nОжидайте его рассмотрения.",
            ephemeral=True,
        )
        return False
    if check_blacklist:
        user = await get_initiator(interaction)
        if user and user.blacklist:
            await interaction.response.send_message(
                "### Вы не можете подать заявление на роль, "
                "так как на вас наложен черный список.\n"
                f"Дата окончания: {discord.utils.format_dt(user.blacklist.ends_at, 'd')}.",
                ephemeral=True,
            )
            return False
    return True


async def army_button_callback(interaction: discord.Interaction):
    if not await _check_can_apply(interaction, check_blacklist=True):
        return
    _, user_name, static_id = await get_user_defaults(interaction)
    from ui.modals.role_getting import RoleRequestModal
    await interaction.response.send_modal(RoleRequestModal(user_name=user_name, static_id=static_id))


async def kmb_button_callback(interaction: discord.Interaction):
    if not await _check_can_apply(interaction, check_blacklist=True):
        return
    _, user_name, static_id = await get_user_defaults(interaction)
    from ui.modals.role_getting import KMBRequestModal
    await interaction.response.send_modal(KMBRequestModal(user_name=user_name, static_id=static_id))


async def supply_access_button_callback(interaction: discord.Interaction):
    if not await _check_can_apply(interaction):
        return
    _, user_name, static_id = await get_user_defaults(interaction)
    from ui.modals.role_getting import SupplyAccessModal
    await interaction.response.send_modal(SupplyAccessModal(user_name=user_name, static_id=static_id))


async def gov_employee_button_callback(interaction: discord.Interaction):
    if not await _check_can_apply(interaction):
        return
    _, user_name, static_id = await get_user_defaults(interaction)
    from ui.modals.role_getting import GovEmployeeModal
    await interaction.response.send_modal(GovEmployeeModal(user_name=user_name, static_id=static_id))


class RoleApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(texts.role_title))
    container.add_item(discord.ui.TextDisplay(texts.role_submission))
    container.add_item(discord.ui.TextDisplay(texts.role_requirements))
    container.add_item(discord.ui.Separator(visible=True))

    army_button = discord.ui.Button(label="ВС РФ", emoji="🎖️", style=discord.ButtonStyle.primary, custom_id="role_apply_army")
    army_button.callback = army_button_callback

    kmb_button = discord.ui.Button(label="КМБ", emoji="🔰", style=discord.ButtonStyle.secondary, custom_id="role_apply_kmb")
    kmb_button.callback = kmb_button_callback

    supply_button = discord.ui.Button(label="Доступ к поставке", emoji="📦", style=discord.ButtonStyle.secondary, custom_id="role_apply_supply")
    supply_button.callback = supply_access_button_callback

    gov_button = discord.ui.Button(label="Гос. сотрудник", emoji="🏛️", style=discord.ButtonStyle.secondary, custom_id="role_apply_gov")
    gov_button.callback = gov_employee_button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(army_button)
    action_row.add_item(kmb_button)
    action_row.add_item(supply_button)
    action_row.add_item(gov_button)
    container.add_item(action_row)


def get_required_rank(role_type: RoleType) -> int:
    ranks = {
        RoleType.ARMY: config.RankIndex.JUNIOR_LIEUTENANT,
        RoleType.KMB: config.RankIndex.JUNIOR_LIEUTENANT,
        RoleType.SUPPLY_ACCESS: config.RankIndex.LIEUTENANT_COLONEL,
        RoleType.GOV_EMPLOYEE: config.RankIndex.LIEUTENANT_COLONEL,
    }
    return ranks.get(role_type, config.RankIndex.COLONEL)


async def check_approve_permission(interaction: Interaction[ClientT], request: RoleRequest) -> bool:
    try:
        user = await get_initiator(interaction)
    except StaticInputRequired:
        return False

    if not user:
        return False

    if (user.rank or 0) >= get_required_rank(request.role_type):
        return True

    if request.role_type in [RoleType.ARMY, RoleType.KMB]:
        division = divisions.get_division(user.division)
        if not division:
            return False
        if division.abbreviation == "ВК":
            return True
        if division.positions:
            for position in division.positions:
                if position.name == user.position and position.privilege.value >= 3:
                    return True

    return False


async def _apply_role_discord(interaction: Interaction[ClientT], request: RoleRequest, user_discord: discord.Member):
    """Выдаёт роли и меняет ник в Discord в зависимости от типа роли."""
    if request.role_type in (RoleType.ARMY, RoleType.KMB):
        user = await User.find_one(User.discord_id == request.user) or User(discord_id=request.user)
        user.rank = 0
        user.division = 1 if request.role_type == RoleType.ARMY else 8
        user.first_name, user.last_name = request.data.full_name.split(" ", 1)
        user.static = request.data.static_id
        user.invited_at = datetime.datetime.now()
        user.pre_inited = True
        await user.save()

        extra_role = config.RoleId.MILITARY_ACADEMY.value if request.role_type == RoleType.ARMY else config.RoleId.KMB.value
        role_ids = [config.RoleId.MILITARY.value, config.RANK_ROLES[config.RANKS[0]], extra_role]
        roles_to_add = [interaction.guild.get_role(rid) for rid in role_ids]
        new_roles = [r for r in user_discord.roles if r.id not in role_ids] + [r for r in roles_to_add if r]

        try:
            await user_discord.edit(
                nick=user.discord_nick,
                roles=new_roles,
                reason=f"Одобрено получение роли {ROLE_DISPLAY_NAMES[request.role_type]} by {interaction.user.id}",
            )
        except discord.Forbidden:
            try:
                msg = "⚠️ Данные сохранены, но не удалось обновить Discord-профиль (не хватает прав или иерархия ролей)."
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
            except discord.HTTPException:
                pass
        except Exception as e:
            logger.error(f"Error recruitment syncing user {user_discord.id}: {e}")

        await audit_logger.log_action(action=AuditAction.INVITED, initiator=interaction.user, target=user.discord_id)

    elif request.role_type in (RoleType.SUPPLY_ACCESS, RoleType.GOV_EMPLOYEE):
        role_id = config.RoleId.SUPPLY_ACCESS.value if request.role_type == RoleType.SUPPLY_ACCESS else config.RoleId.GOV_EMPLOYEE.value
        role = interaction.guild.get_role(role_id)
        new_roles = list(user_discord.roles)
        if role:
            new_roles.append(role)
        new_nick = f"{request.extended_data.faction} | {request.extended_data.full_name}"[:32]
        await user_discord.edit(
            nick=new_nick,
            roles=new_roles,
            reason=f"Одобрено роль {ROLE_DISPLAY_NAMES[request.role_type]} by {interaction.user.id}",
        )


class RoleManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"role_(?P<action>approve|reject):(?P<id>\d+)",
):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Одобрить", "reject": "Отклонить"}
        styles = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger}
        emojis = {"approve": "👍", "reject": "👎"}

        super().__init__(
            discord.ui.Button(
                label=labels[action],
                emoji=emojis[action],
                style=styles[action],
                custom_id=f"role_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        from utils.mongo_lock import try_lock

        if not await try_lock(RoleRequest, self.request_id, "status", "PROCESSING", "PENDING"):
            await interaction.response.send_message("❌ Заявка не найдена или уже обработана.", ephemeral=True)
            return

        request = await RoleRequest.find_one(RoleRequest.id == self.request_id)

        if not await check_approve_permission(interaction, request):
            await RoleRequest.get_pymongo_collection().update_one(
                {"_id": self.request_id}, {"$set": {"status": "PENDING"}}
            )
            required = ROLE_REQUIRED_RANKS.get(request.role_type, "Полковник")
            await interaction.response.send_message(
                f"❌ У вас нет прав. Требуется звание: {required}+", ephemeral=True
            )
            return

        if self.action == "approve":
            request.status = "APPROVED"
            await request.save()

            assert isinstance(interaction.response, InteractionResponse)
            await interaction.response.edit_message(
                content=f"-# ||<@{request.user}> {interaction.user.mention}||",
                embed=await request.to_embed(),
                view=indicator_view(f"Одобрил {interaction.user.display_name}", emoji="👍"),
            )

            user_discord = await interaction.client.getch_member(request.user)
            if user_discord:
                await _apply_role_discord(interaction, request, user_discord)

            await notify_role_approved(interaction.client, request.user, ROLE_DISPLAY_NAMES.get(request.role_type, "Роль"))

        else:
            request.status = "REJECTED"
            await request.save()

            assert isinstance(interaction.response, InteractionResponse)
            await interaction.response.edit_message(
                content=f"-# ||<@{request.user}> {interaction.user.mention}||",
                embed=await request.to_embed(),
                view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
            )

            await notify_role_rejected(interaction.client, request.user, ROLE_DISPLAY_NAMES.get(request.role_type, "Роль"))


class RoleManagementView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.add_item(RoleManagementButton("approve", request_id))
        self.add_item(RoleManagementButton("reject", request_id))


class RoleLegacyButton(discord.ui.DynamicItem[discord.ui.Button], template=r"(?P<action>approve|reject)_role:(?P<id>\d+)"):
    def __init__(self, action: str, request_id: int):
        super().__init__(discord.ui.Button(custom_id=f"{action}_role:{request_id}"))
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        # Просто перенаправляем в новую универсальную кнопку
        new_button = RoleManagementButton(self.action, self.request_id)
        await new_button.callback(interaction)