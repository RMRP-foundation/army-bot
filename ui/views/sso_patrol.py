import discord
import datetime
import config
from database.models import SSOPatrolRequest
from database import divisions
from texts import patrol_title, patrol_rules
from ui.views.indicators import indicator_view
from utils.permissions import is_high_command
from utils.user_data import get_initiator

MSK = datetime.timezone(datetime.timedelta(hours=3))

closed_requests = set()


class SSOPatrolManagementButton(discord.ui.DynamicItem[discord.ui.Button],
                                template=r"sso_mng:(?P<action>\w+):(?P<id>\d+)"):
    def __init__(self, action: str, request_id: int):
        labels = {"approve": "Одобрить", "reject": "Отклонить"}
        styles = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger}
        emojis = {"approve": "👍", "reject": "👎"}

        super().__init__(discord.ui.Button(
            label=labels.get(action, action),
            emoji=emojis[action],
            style=styles[action],
            custom_id=f"sso_mng:{action}:{request_id}"
        ))
        self.action, self.request_id = action, request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match.group("action"), int(match.group("id")))

    async def callback(self, interaction: discord.Interaction):
        if self.request_id in closed_requests:
            return await interaction.response.send_message("### ❌ Ошибка\nЗаявка уже обрабатывается.", ephemeral=True)

        user = await get_initiator(interaction)
        div_info = divisions.get_division(user.division) if user else None

        is_sso = div_info and div_info.abbreviation == "ССО"
        is_staff = await is_high_command(interaction.user.id)
        if not user or not (is_sso or is_staff):
            return await interaction.response.send_message(
                "### ❌ Ошибка доступа\nРассматривать заявления могут только сотрудники ССО.", ephemeral=True)

        closed_requests.add(self.request_id)

        req = await SSOPatrolRequest.find_one(SSOPatrolRequest.id == self.request_id)
        if not req or req.status != "PENDING":
            closed_requests.discard(self.request_id)
            return await interaction.response.send_message("### ❌ Заявление уже обработано.", ephemeral=True)

        req.status = "APPROVED" if self.action == "approve" else "REJECTED"
        req.reviewer_id = interaction.user.id
        await req.save()

        status_map = {"approve": ("Одобрил", "👍"), "reject": ("Отклонил", "👎")}
        status_text, final_emoji = status_map.get(self.action, ("Обработал", "📝"))

        await interaction.response.edit_message(
            content=f"-# ||<@{req.user_id}> {interaction.user.mention}||",
            embed=await req.to_embed(interaction.client),
            view=indicator_view(f"{status_text} {interaction.user.display_name}", emoji=final_emoji)
        )
        closed_requests.discard(self.request_id)


class SSOPatrolApplyView(discord.ui.LayoutView):
    def __init__(self):
        super().__init__(timeout=None)
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(patrol_title))
        container.add_item(discord.ui.TextDisplay(patrol_rules))
        container.add_item(discord.ui.Separator())

        btn = discord.ui.Button(label="Подать заявление", style=discord.ButtonStyle.primary, custom_id="sso_apply_btn",
                                emoji="📨")
        btn.callback = self.on_apply

        row = discord.ui.ActionRow()
        row.add_item(btn)
        container.add_item(row)
        self.add_item(container)

    async def on_apply(self, interaction: discord.Interaction):
        user = await get_initiator(interaction)

        min_rank = config.RankIndex.SENIOR_SERGEANT
        if (user.rank or 0) < min_rank:
            return await interaction.response.send_message(
                f"### ❌ Отказано в подаче\n"
                f"Подать заявление на совместную работу с ССО можно только со звания "
                f"**{config.RANKS[min_rank]}** и выше.",
                ephemeral=True,
            )

        last_fail = await SSOPatrolRequest.find(
            SSOPatrolRequest.user_id == interaction.user.id,
            SSOPatrolRequest.status == "REJECTED",
            SSOPatrolRequest.reason == "Провал теста"
        ).sort("-date").first_or_none()

        if last_fail:
            now = datetime.datetime.now(datetime.timezone.utc)
            last_date = last_fail.date.replace(tzinfo=datetime.timezone.utc)

            if (now - last_date).total_seconds() < config.SSO_FAIL_COOLDOWN:
                retry_ts = int(last_date.timestamp() + config.SSO_FAIL_COOLDOWN)
                return await interaction.response.send_message(
                    f"### ⏳ Тест провален\nВы сможете попробовать снова <t:{retry_ts}:R>.",
                    ephemeral=True
                )

        from ui.modals.sso_patrol import SSOPatrolModal
        await interaction.response.send_modal(SSOPatrolModal(user.full_name or interaction.user.display_name))
