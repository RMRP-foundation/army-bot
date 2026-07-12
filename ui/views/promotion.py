import discord
from discord import Interaction
from discord._types import ClientT

import config
from database import divisions
from database.models import PromotionReport, User
from texts import promotion_title, promotion_description
from ui.views.indicators import indicator_view
from utils.audit import AuditAction, audit_logger
from utils.mongo_lock import try_lock
from utils.notifications import notify_promoted, notify_promotion_approved, notify_promotion_rejected
from utils.roles import to_rank
from utils.user_data import get_initiator


def _can_approve(approver: User, div, report: PromotionReport) -> tuple[bool, str]:
    if (approver.rank or 0) >= config.RankIndex.COLONEL:
        return True, ""

    min_rank = div.promotion_min_rank_review if div.promotion_min_rank_review is not None else config.RankIndex.MAJOR
    if (approver.rank or 0) < min_rank:
        return False, f"❌ Для проверки рапортов требуется звание {config.RANKS[min_rank]}+."

    if (approver.rank or 0) <= report.target_rank:
        return False, f"❌ Для проверки этого рапорта требуется звание {config.RANKS[report.target_rank + 1]}+."

    if div.promotion_reviewer_division_id is not None:
        if approver.division != div.promotion_reviewer_division_id:
            reviewer_div = divisions.get_division(div.promotion_reviewer_division_id)
            name = reviewer_div.name if reviewer_div else "нужное подразделение"
            return False, f"❌ Рапорты этого подразделения проверяет {name}."
    else:
        if approver.division != div.division_id:
            return False, "❌ Вы не можете проверять рапорты этого подразделения."

    return True, ""


def _can_promote(promoter: User, div) -> tuple[bool, str]:
    if (promoter.rank or 0) >= config.RankIndex.COLONEL:
        return True, ""

    if (promoter.rank or 0) < config.RankIndex.MAJOR:
        return False, f"❌ Для повышения требуется звание {config.RANKS[config.RankIndex.MAJOR]}+."

    if div.promotion_reviewer_division_id is not None:
        if promoter.division != div.promotion_reviewer_division_id:
            reviewer_div = divisions.get_division(div.promotion_reviewer_division_id)
            name = reviewer_div.name if reviewer_div else "нужного подразделения"
            return False, f"❌ Повышение в этом подразделении выполняет {name}."
    else:
        if promoter.division != div.division_id:
            return False, "❌ Вы не можете повышать в этом подразделении."

    return True, ""


async def _do_promote(interaction: discord.Interaction, report: PromotionReport):
    user_db = await User.find_one(User.discord_id == report.user_id)
    if not user_db:
        await interaction.followup.send("❌ Пользователь не найден в БД.", ephemeral=True)
        return

    user_db.rank = report.target_rank
    await user_db.save()

    member = await interaction.client.getch_member(report.user_id)
    if member:
        new_roles = to_rank(member.roles, user_db.rank)
        await member.edit(
            nick=user_db.discord_nick,
            roles=new_roles,
            reason=f"Повышение по рапорту #{report.id} by {interaction.user.id}",
        )

    await audit_logger.log_action(
        action=AuditAction.PROMOTED,
        initiator=interaction.user,
        target=report.user_id,
    )

    await notify_promoted(interaction.client, report.user_id, config.RANKS[report.target_rank])


class PromotionManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"promotion:(?P<action>approve|reject|cancel):(?P<id>\d+)",
):
    _config = {
        "approve": ("Одобрить", discord.ButtonStyle.success, "👍"),
        "reject":  ("Отклонить", discord.ButtonStyle.danger,  "👎"),
        "cancel":  ("Отменить",  discord.ButtonStyle.grey,    None),
    }

    def __init__(self, action: str, report_id: int):
        label, style, emoji = self._config[action]
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                emoji=emoji,
                custom_id=f"promotion:{action}:{report_id}",
            )
        )
        self.action = action
        self.report_id = report_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match.group("action"), int(match.group("id")))

    async def _handle_approve(self, interaction: discord.Interaction, report: PromotionReport):
        approver = await get_initiator(interaction)
        div = divisions.get_division(report.division_id)
        ok, err = _can_approve(approver, div, report)
        if not ok:
            await PromotionReport.get_pymongo_collection().update_one(
                {"_id": self.report_id}, {"$set": {"status": "PENDING"}}
            )
            return await interaction.response.send_message(err, ephemeral=True)

        report.status = "APPROVED"
        report.reviewer_id = interaction.user.id
        await report.save()

        view = discord.ui.View(timeout=None)
        view.add_item(PromoteButton(report.id))

        await interaction.response.edit_message(
            content=f"-# ||<@{report.user_id}> <@{interaction.user.id}>||",
            embed=await report.to_embed(interaction.client),
            view=view,
        )

        await notify_promotion_approved(interaction.client, report.user_id)

    async def _handle_reject(self, interaction: discord.Interaction, report: PromotionReport):
        approver = await get_initiator(interaction)
        div = divisions.get_division(report.division_id)
        ok, err = _can_approve(approver, div, report)
        if not ok:
            await PromotionReport.get_pymongo_collection().update_one(
                {"_id": self.report_id}, {"$set": {"status": "PENDING"}}
            )
            return await interaction.response.send_message(err, ephemeral=True)

        modal = discord.ui.Modal(title="Отклонение рапорта на повышение")
        reason_input = discord.ui.TextInput(
            label="Причина отклонения",
            style=discord.TextStyle.paragraph,
            placeholder="Введите причину отклонения",
            required=True,
            max_length=500,
        )
        modal.add_item(reason_input)

        async def on_submit(modal_interaction: discord.Interaction):
            report.status = "REJECTED"
            report.reviewer_id = interaction.user.id
            report.reject_reason = reason_input.value
            await report.save()

            await modal_interaction.response.edit_message(
                content=f"-# ||<@{report.user_id}> <@{interaction.user.id}>||",
                embed=await report.to_embed(interaction.client),
                view=indicator_view("Отклонён", emoji="👎"),
            )

            await notify_promotion_rejected(interaction.client, report.user_id, reason_input.value)

        modal.on_submit = on_submit
        await interaction.response.send_modal(modal)

    async def _handle_cancel(self, interaction: discord.Interaction, report: PromotionReport):
        if interaction.user.id != report.user_id:
            await PromotionReport.get_pymongo_collection().update_one(
                {"_id": self.report_id}, {"$set": {"status": "PENDING"}}
            )
            return await interaction.response.send_message(
                "❌ Отменить рапорт может только его автор.", ephemeral=True
            )

        report.status = "CANCELLED"
        await report.save()

        await interaction.response.send_message("✅ Ваш рапорт был отменен.", ephemeral=True)
        await interaction.message.delete()

    async def callback(self, interaction: Interaction[ClientT]):
        expected = ["PENDING", "APPROVED"] if self.action == "reject" else "PENDING"
        if not await try_lock(PromotionReport, self.report_id, "status", "PROCESSING", expected):
            return await interaction.response.send_message(
                f"❌ Рапорт #{self.report_id} не найден или уже обработан.", ephemeral=True
            )

        report = await PromotionReport.find_one(PromotionReport.id == self.report_id)

        match self.action:
            case "approve":
                await self._handle_approve(interaction, report)
            case "reject":
                await self._handle_reject(interaction, report)
            case "cancel":
                await self._handle_cancel(interaction, report)


class PromoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"promotion:promote:(?P<id>\d+)",
):
    def __init__(self, report_id: int):
        super().__init__(
            discord.ui.Button(
                label="Повысить",
                emoji="🎖️",
                style=discord.ButtonStyle.primary,
                custom_id=f"promotion:promote:{report_id}",
            )
        )
        self.report_id = report_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match.group("id")))

    async def callback(self, interaction: Interaction[ClientT]):
        if not await try_lock(PromotionReport, self.report_id, "status", "PROCESSING", "APPROVED"):
            return await interaction.response.send_message(
                f"❌ Рапорт #{self.report_id} не найден или уже обработан.", ephemeral=True
            )

        report = await PromotionReport.find_one(PromotionReport.id == self.report_id)

        member = await interaction.client.getch_member(report.user_id)
        if member:
            user_roles_ids = [role.id for role in member.roles]
            if any(rid in config.PENALTY_ROLES for rid in user_roles_ids) or config.INVESTIGATION_ROLE in user_roles_ids:
                await PromotionReport.get_pymongo_collection().update_one(
                    {"_id": self.report_id}, {"$set": {"status": "APPROVED"}}
                )
                return await interaction.response.send_message(
                    "❌ Невозможно повысить военнослужащего с активными дисциплинарными взысканиями или под расследованием.",
                    ephemeral=True,
                )

        promoter = await get_initiator(interaction)
        div = divisions.get_division(report.division_id)
        ok, err = _can_promote(promoter, div)
        if not ok:
            await PromotionReport.get_pymongo_collection().update_one(
                {"_id": self.report_id}, {"$set": {"status": "APPROVED"}}
            )
            return await interaction.response.send_message(err, ephemeral=True)

        report.status = "PROMOTED"
        report.promoted_by = interaction.user.id
        await report.save()

        await interaction.response.edit_message(
            content=f"-# ||<@{report.user_id}> <@{report.reviewer_id}> <@{interaction.user.id}>||",
            embed=await report.to_embed(interaction.client),
            view=indicator_view("Повышен", emoji="🎖️"),
        )

        await _do_promote(interaction, report)


class PromotionManagementView(discord.ui.View):
    def __init__(self, report_id: int):
        super().__init__(timeout=None)
        self.add_item(PromotionManagementButton("approve", report_id))
        self.add_item(PromotionManagementButton("reject", report_id))
        self.add_item(PromotionManagementButton("cancel", report_id))


async def _promotion_apply_callback(interaction: discord.Interaction):
    from ui.modals.promotion import PromotionReportModal

    div = next(
        (d for d in divisions.divisions if d.promotion_channel == interaction.channel_id),
        None,
    )

    user_db = await get_initiator(interaction)
    if not user_db or user_db.rank is None:
        return await interaction.response.send_message(
            "❌ Вы не состоите на службе.", ephemeral=True
        )

    if user_db.rank >= config.RankIndex.CAPTAIN:
        return await interaction.response.send_message(
            "❌ Повышение через рапорт доступно только до звания Майор.",
            ephemeral=True,
        )

    if user_db.division != div.division_id:
        return await interaction.response.send_message(
            "❌ Вы можете подавать рапорт только в своём подразделении.", ephemeral=True
        )

    await interaction.response.send_modal(PromotionReportModal(div, user_db))


class PromotionApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(promotion_title))
        container.add_item(discord.ui.TextDisplay(promotion_description))
        container.add_item(discord.ui.Separator())

        btn = discord.ui.Button(
            label="Подать рапорт",
            emoji="📨",
            style=discord.ButtonStyle.primary,
            custom_id="promotion_apply_button",
        )
        btn.callback = _promotion_apply_callback

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)