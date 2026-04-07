import datetime
import re
from typing import Any

import discord
from discord import Interaction
from discord._types import ClientT

import config
from database.models import LeaveRequest, LeaveType, User
from texts import (
    ic_leave_description,
    ic_leave_title,
    ooc_leave_description,
    ooc_leave_title,
)
from ui.views.indicators import indicator_view
from utils.notifications import notify_leave_approved, notify_leave_cancelled, notify_leave_rejected
from utils.user_data import get_initiator

MSK = datetime.timezone(datetime.timedelta(hours=3))

closed_requests: set[int] = set()


async def _apply_leave_nick_and_role(
        bot, member: discord.Member, user_db: User, leave_type: LeaveType
) -> None:
    """Выдаёт отпускной ник и роль."""
    from database import divisions
    from utils.user_data import transliterate_abbreviation

    div = divisions.get_division(user_db.division) if user_db.division else None

    if div and div.abbreviation == "ССО":
        new_nick = f"{leave_type.value} | {member.display_name}"[:32]
    else:
        parts = [leave_type.value]

        if div and (abbr := div.abbreviation):
            parts.append(abbr if abbr in ["ВА", "КМБ"] else transliterate_abbreviation(abbr))

        if user_db.rank is not None:
            parts.append(config.RANKS_SHORT[user_db.rank])

        if name := user_db.full_name:
            if len(" | ".join(parts + [name])) > 32:
                name = user_db.short_name or name
            parts.append(name)

        new_nick = " | ".join(parts)[:32]

    role_id = config.RoleId.IC_LEAVE.value if leave_type == LeaveType.IC else config.RoleId.OOC_LEAVE.value

    guild = bot.get_guild(config.GUILD_ID)
    role = guild.get_role(role_id)

    new_roles = list(member.roles)
    if role and role not in new_roles:
        new_roles.append(role)

    try:
        await member.edit(
            nick=new_nick,
            roles=new_roles,
            reason=f"{leave_type.value} отпуск одобрен"
        )
    except discord.Forbidden:
        pass


async def _remove_leave_nick_and_role(
    bot,
    member: discord.Member,
    user_db: User,
    leave_type: LeaveType,
    original_nick: str | None = None,
) -> None:
    """Снимает отпускную роль и восстанавливает ник.

    - ССО: original_nick, т.к. их ник не строится стандартным способом.
    - Остальные: user_db.discord_nick — актуальный ник с учётом
      возможных изменений звания/подразделения за время отпуска.
    """
    from database import divisions

    role_id = (
        config.RoleId.IC_LEAVE.value
        if leave_type == LeaveType.IC
        else config.RoleId.OOC_LEAVE.value
    )
    new_roles = [r for r in member.roles if r.id != role_id]

    div = divisions.get_division(user_db.division) if user_db.division else None
    if div and div.abbreviation == "ССО":
        new_nick = original_nick[:32]
    else:
        new_nick = user_db.discord_nick[:32]

    try:
        await member.edit(
            nick=new_nick, roles=new_roles, reason=f"{leave_type.value} отпуск завершён"
        )
    except discord.Forbidden:
        pass

async def check_can_apply(interaction: discord.Interaction, leave_type: LeaveType) -> bool:
    user_db = await get_initiator(interaction)

    if not user_db or user_db.rank is None:
        await interaction.response.send_message(
            "❌ Вы не состоите на службе.", ephemeral=True
        )
        return False

    if user_db.leave_status:
        await interaction.response.send_message(
            "❌ У вас уже есть активный отпуск.", ephemeral=True
        )
        return False

    user_requests = await LeaveRequest.find(
        LeaveRequest.user_id == interaction.user.id,
        LeaveRequest.status != "REJECTED",
    ).to_list()

    if any(req.status == "PENDING" for req in user_requests):
        await interaction.response.send_message("❌ Ваше предыдущее заявление еще находится на рассмотрении.",
                                                ephemeral=True)
        return False

    if leave_type == LeaveType.IC:
        now_msk = discord.utils.utcnow() + datetime.timedelta(hours=3)
        msk_month_start_utc = now_msk.replace(day=1, hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(hours=3)

        used_this_month = any(
            req.leave_type == LeaveType.IC and
            req.created_at.replace(tzinfo=datetime.timezone.utc) >= msk_month_start_utc
            for req in user_requests
        )

        if used_this_month:
            await interaction.response.send_message("❌ В этом месяце вы уже использовали свое право на IC отпуск.",
                                                    ephemeral=True)
            return False

    return True

async def _ic_leave_button_callback(interaction: discord.Interaction):
    from ui.modals.leave import LeaveRequestModal

    if not await check_can_apply(interaction, LeaveType.IC):
        return

    await interaction.response.send_modal(LeaveRequestModal(LeaveType.IC))


class ICLeaveApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(ic_leave_title))
    container.add_item(discord.ui.TextDisplay(ic_leave_description))
    container.add_item(discord.ui.Separator())

    ic_button = discord.ui.Button(
        label="Подать заявление",
        style=discord.ButtonStyle.primary,
        custom_id="ic_leave_apply_button",
    )
    ic_button.callback = _ic_leave_button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(ic_button)
    container.add_item(action_row)



async def _ooc_leave_button_callback(interaction: discord.Interaction):
    from ui.modals.leave import LeaveRequestModal

    if not await check_can_apply(interaction, LeaveType.OOC):
        return

    await interaction.response.send_modal(LeaveRequestModal(LeaveType.OOC))


class OOCLeaveApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

    container = discord.ui.Container()
    container.add_item(discord.ui.TextDisplay(ooc_leave_title))
    container.add_item(discord.ui.TextDisplay(ooc_leave_description))
    container.add_item(discord.ui.Separator())

    ooc_button = discord.ui.Button(
        label="Подать заявление",
        style=discord.ButtonStyle.primary,
        custom_id="ooc_leave_apply_button",
    )
    ooc_button.callback = _ooc_leave_button_callback

    action_row = discord.ui.ActionRow()
    action_row.add_item(ooc_button)
    container.add_item(action_row)



class LeaveManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"leave_(?P<action>approve|reject|annul|cancel):(?P<id>\d+)",
):
    _config: dict[str, tuple[str, discord.ButtonStyle, str | None]] = {
        "approve": ("Одобрить",    discord.ButtonStyle.success, "👍"),
        "reject":  ("Отклонить",   discord.ButtonStyle.danger,  "👎"),
        "annul":   ("Аннулировать",discord.ButtonStyle.grey,    None),
        "cancel":  ("Отменить",    discord.ButtonStyle.grey,    None),
    }

    _expected_status: dict[str, tuple[str, ...]] = {
        "approve": ("PENDING",),
        "reject":  ("PENDING",),
        "annul":   ("APPROVED",),
        "cancel":  ("PENDING",),
    }

    def __init__(self, action: str, request_id: int):
        label, style, emoji = self._config[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"leave_{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
    ):
        return cls(match.group("action"), int(match.group("id")))


    async def _check_officer(
        self, interaction: discord.Interaction, request: LeaveRequest
    ) -> tuple[bool, str]:
        approver = await get_initiator(interaction)
        if not approver:
            return False, "❌ Вы не найдены в базе данных."
        if (approver.rank or 0) < config.RankIndex.MAJOR:
            return False, "❌ Доступно со звания Майор."
        requester = await User.find_one(User.discord_id == request.user_id)
        if requester and (approver.rank or 0) <= (requester.rank or 0):
            return False, "❌ Вы не можете рассматривать заявку военнослужащего равного или старшего звания."
        return True, ""


    async def _handle_approve(self, interaction: discord.Interaction, request: LeaveRequest):
        is_ok, error = await self._check_officer(interaction, request)
        if not is_ok:
            await interaction.response.send_message(error, ephemeral=True)
            return

        user_db = await User.find_one(User.discord_id == request.user_id)
        if not user_db or user_db.rank is None:
            request.status = "REJECTED"
            request.reviewer_id = interaction.user.id
            await request.save()

            await interaction.response.send_message(
                "❌ Пользователь не состоит на службе.",
                ephemeral=True
            )
            return

        now = discord.utils.utcnow()
        member = await interaction.client.getch_member(request.user_id)

        user_db.leave_status = request.leave_type.value
        await user_db.save()

        request.status = "APPROVED"
        request.reviewer_id = interaction.user.id
        request.approved_at = now
        request.ends_at = now + datetime.timedelta(days=request.days)
        request.original_nick = member.display_name if member else None
        await request.save()

        if member:
            await _apply_leave_nick_and_role(
                interaction.client, member, user_db, request.leave_type
            )

        embed = await request.to_embed()
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=embed,
            view=LeaveManagementView(request.id, status="APPROVED"),
        )

        await notify_leave_approved(interaction.client, request.user_id, request)

        from cogs.leave import schedule_leave_expiry
        await schedule_leave_expiry(interaction.client, request)

    async def _handle_reject(self, interaction: discord.Interaction, request: LeaveRequest):
        is_ok, err = await self._check_officer(interaction, request)
        if not is_ok:
            await interaction.response.send_message(err, ephemeral=True)
            return

        request.status = "REJECTED"
        request.reviewer_id = interaction.user.id
        await request.save()

        embed = await request.to_embed()
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=embed,
            view=indicator_view(f"Отклонил {interaction.user.display_name}", emoji="👎"),
        )
        await notify_leave_rejected(interaction.client, request.user_id, request)

    async def _handle_annul(self, interaction: discord.Interaction, request: LeaveRequest):
        """Аннулирование одобренного отпуска. Майор+ или сам пользователь."""
        is_own = interaction.user.id == request.user_id
        if not is_own:
            is_ok, err = await self._check_officer(interaction, request)
            if not is_ok:
                await interaction.response.send_message(err, ephemeral=True)
                return

        now = discord.utils.utcnow()
        request.status = "ANNULLED"
        request.annuller_id = interaction.user.id
        request.annulled_at = now
        await request.save()

        member = await interaction.client.getch_member(request.user_id)
        if member:
            user_db = await User.find_one(User.discord_id == request.user_id)
            if user_db:
                user_db.leave_status = None
                await user_db.save()

                await _remove_leave_nick_and_role(
                    interaction.client, member, user_db,
                    request.leave_type, original_nick=request.original_nick,
                )

        from cogs.leave import cancel_leave_timer
        cancel_leave_timer(request.id)

        embed = await request.to_embed()
        await interaction.response.edit_message(
            content=f"-# ||<@{request.user_id}> {interaction.user.mention}||",
            embed=embed,
            view=indicator_view(f"Аннулировал {interaction.user.display_name}"),
        )
        await interaction.followup.send("✅ Отпуск аннулирован.", ephemeral=True)
        await notify_leave_cancelled(interaction.client, request.user_id, request)

    async def _handle_cancel(self, interaction: discord.Interaction, request: LeaveRequest):
        """Отмена нерассмотренного заявления. Только сам пользователь."""
        if interaction.user.id != request.user_id:
            await interaction.response.send_message(
                "❌ Отменить заявление может только его автор.", ephemeral=True
            )
            return

        now = discord.utils.utcnow()
        request.status = "REJECTED"
        request.annuller_id = interaction.user.id
        request.annulled_at = now
        await request.save()

        try:
            await interaction.message.delete()
        except discord.NotFound:
            pass

        await interaction.response.send_message("✅ Заявление отменено.", ephemeral=True)


    async def callback(self, interaction: Interaction[ClientT]) -> Any:
        request = await LeaveRequest.find_one(LeaveRequest.id == self.request_id)
        if not request:
            await interaction.response.send_message("❌ Заявка не найдена.", ephemeral=True)
            return

        if request.status not in self._expected_status[self.action]:
            await interaction.response.send_message(
                "❌ Заявка уже обработана.", ephemeral=True
            )
            return

        if self.action in ("approve", "reject", "annul"):
            if self.request_id in closed_requests:
                await interaction.response.send_message(
                    "❌ Заявка уже обрабатывается.", ephemeral=True
                )
                return
            closed_requests.add(self.request_id)

        try:
            match self.action:
                case "approve":
                    await self._handle_approve(interaction, request)
                case "reject":
                    await self._handle_reject(interaction, request)
                case "annul":
                    await self._handle_annul(interaction, request)
                case "cancel":
                    await self._handle_cancel(interaction, request)
        finally:
            closed_requests.discard(self.request_id)


class LeaveManagementView(discord.ui.View):
    def __init__(self, request_id: int, status: str = "PENDING"):
        super().__init__(timeout=None)
        if status == "PENDING":
            self.add_item(LeaveManagementButton("approve", request_id))
            self.add_item(LeaveManagementButton("reject", request_id))
            self.add_item(LeaveManagementButton("cancel", request_id))
        elif status == "APPROVED":
            self.add_item(LeaveManagementButton("annul", request_id))