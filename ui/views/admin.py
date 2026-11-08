import asyncio
import io
import logging
from pathlib import Path
from typing import Any

import discord

from bot import Bot
from core import config
from core.config import GUILD_ID
from database import divisions
from database.models import Division, User
from services import admin_db, bottom_messages, presence
from ui.modals.admin import AddMemberModal, DossierModal, EditFieldModal, SearchModal, guard, SupplyLinkModal

logger = logging.getLogger(__name__)

LOG_PATH = Path(__file__).resolve().parent.parent.parent / "bot.log"
LOG_TAIL_BYTES = 8 * 1024 * 1024

BLURPLE = discord.Color.blurple()
GREEN = discord.Color.green()
RED = discord.Color.red()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(content: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(content)


def _sep() -> discord.ui.Separator:
    return discord.ui.Separator(visible=True)


def _button(label: str, emoji: str | None, callback, style=discord.ButtonStyle.secondary) -> discord.ui.Button:
    btn = discord.ui.Button(label=label, emoji=emoji, style=style)
    btn.callback = callback
    return btn


def _row(*items) -> discord.ui.ActionRow:
    row = discord.ui.ActionRow()
    for item in items:
        row.add_item(item)
    return row


def _read_tail(path: Path, limit: int) -> bytes:
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - limit))
        return f.read()


class AdminPanel(discord.ui.LayoutView):
    def __init__(self, bot: Bot):
        super().__init__(timeout=300)
        self.bot = bot
        self.model = None
        self.record = None
        self.found = 1
        self.dossier_id: int | None = None
        self.dossier_user: User | None = None
        self.home()

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return await guard(interaction)

    def _show(self, container: discord.ui.Container) -> None:
        self.clear_items()
        self.add_item(container)

    async def _push(self, interaction: discord.Interaction) -> None:
        if interaction.response.is_done():
            await interaction.edit_original_response(view=self)
        else:
            await interaction.response.edit_message(view=self)

    def home(self, status: str | None = None) -> None:
        self.dossier_id = self.dossier_user = None
        container = discord.ui.Container(accent_color=BLURPLE)
        container.add_item(_text("## 🛠️ Сервисная панель"))
        if status:
            container.add_item(_text(_clip(status, 1500)))
        container.add_item(_sep())

        entries = (
            ("📌 Интерфейсы каналов", "Пересоздание закреплённых сообщений подачи заявок", self.on_interfaces),
            ("👤 Досье бойца", "Профиль и последние заявки по Discord ID или статику", self.on_dossier_open),
            ("🗄️ База данных", "Просмотр, правка и удаление записей коллекций MongoDB", self.on_tables),
        )
        for title, description, callback in entries:
            sec = discord.ui.Section(accessory=_button("Открыть", None, callback, discord.ButtonStyle.primary))
            sec.add_item(_text(f"### {title}\n-# {description}"))
            container.add_item(sec)

        container.add_item(_sep())
        container.add_item(_text("### Система"))
        container.add_item(_row(
            _button("Ресинк команд", "🔄", self.on_resync),
            _button("Добавить бойца", "➕", self.on_add_member_open),
            _button("Кэш подразделений", "♻️", self.on_reload_divisions),
            _button("Логи", "📄", self.on_logs),
        ))
        self._show(container)

    def interfaces(self, status: str | None = None) -> None:
        container = discord.ui.Container(accent_color=BLURPLE)
        container.add_item(_text("## 📌 Интерфейсы каналов\n-# Удаляет старое закреплённое сообщение и отправляет новое"))
        if status:
            container.add_item(_text(_clip(status, 1500)))
        container.add_item(_sep())

        options = []
        for target in bottom_messages.get_targets().values():
            channel = self.bot.get_channel(target.channel_id)
            options.append(discord.SelectOption(
                label=_clip(target.label, 100),
                value=str(target.channel_id),
                description=_clip(f"#{channel.name}", 100) if channel else "канал не найден",
            ))
        if options:
            select = discord.ui.Select(placeholder="Обновить один канал…", options=options[:25])

            async def on_pick(interaction: discord.Interaction):
                await self.refresh_one(interaction, int(select.values[0]))

            select.callback = on_pick
            container.add_item(_row(select))
        else:
            container.add_item(_text("Реестр пуст."))

        container.add_item(_row(
            _button("Ссылка склада", "🔗", self.on_supply_link_open),
            _button("Обновить все", "📌", self.on_refresh_all, discord.ButtonStyle.primary),
            _button("Назад", "⬅️", self.on_home),
        ))
        self._show(container)

    def tables(self, status: str | None = None) -> None:
        self.dossier_id = self.dossier_user = None
        container = discord.ui.Container(accent_color=GREEN)
        container.add_item(_text("## 🗄️ База данных\n-# Выберите коллекцию для поиска по полю"))
        if status:
            container.add_item(_text(_clip(status, 1500)))
        container.add_item(_sep())

        models_map = admin_db.get_models()
        select = discord.ui.Select(
            placeholder="Коллекция…",
            options=[
                discord.SelectOption(label=name, description=model.__name__)
                for name, model in models_map.items()
            ][:25],
        )

        async def on_pick(interaction: discord.Interaction):
            await interaction.response.send_modal(SearchModal(self, models_map[select.values[0]]))

        select.callback = on_pick
        container.add_item(_row(select))
        container.add_item(_row(_button("Назад", "⬅️", self.on_home)))
        self._show(container)

    def dossier_screen(self, user: User, sections: list[admin_db.DossierSection], status: str | None = None) -> None:
        self.dossier_id, self.dossier_user = user.discord_id, user
        container = discord.ui.Container(accent_color=BLURPLE)
        container.add_item(_text(_clip(f"## 👤 {user.full_name or 'Без имени'}\n-# discord_id {user.discord_id}", 300)))
        if status:
            container.add_item(_text(_clip(status, 500)))
        container.add_item(_sep())
        container.add_item(_text(admin_db.render_record(User, user, limit=1500, value_limit=80)))
        container.add_item(_sep())

        if sections:
            lines = [
                f"`{s.table}` · {s.total}: " + ", ".join(admin_db.row_summary(s.model, row) for row in s.rows)
                for s in sections
            ]
            container.add_item(_text(_clip("### Заявки\n" + "\n".join(lines), 1300)))
            options = [
                discord.SelectOption(
                    label=_clip(f"{s.table} {admin_db.row_summary(s.model, row)}", 100),
                    value=f"{s.table}:{getattr(row, admin_db.pk_name(s.model))}",
                )
                for s in sections for row in s.rows
            ][:25]
            select = discord.ui.Select(placeholder="Открыть заявку…", options=options)

            async def on_pick(interaction: discord.Interaction):
                table, pk = select.values[0].rsplit(":", 1)
                await self.open_request(interaction, table, pk)

            select.callback = on_pick
            container.add_item(_row(select))
        else:
            container.add_item(_text("### Заявки\nНет ни одной."))

        container.add_item(_row(
            _button("Карточка бойца", "✏️", self.on_dossier_card, discord.ButtonStyle.primary),
            _button("Обновить", "🔄", self.on_dossier_refresh),
            _button("Назад", "⬅️", self.on_home),
        ))
        self._show(container)

    def record_screen(self, note: str | None = None) -> None:
        pk = admin_db.pk_name(self.model)
        table_name = getattr(self.model.Settings, "name", self.model.__name__)
        header = f"## 🗄️ {table_name}\n-# {pk} = {getattr(self.record, pk)}"
        if self.found > 1:
            header += f" · найдено {self.found}{'+' if self.found >= 10 else ''}, показана первая"

        container = discord.ui.Container(accent_color=GREEN)
        container.add_item(_text(_clip(header, 300)))
        if note:
            container.add_item(_text(_clip(note, 500)))
        container.add_item(_sep())
        container.add_item(_text(admin_db.render_record(self.model, self.record)))
        container.add_item(_sep())

        main = [
            _button("Изменить поле", "✏️", self.on_edit, discord.ButtonStyle.primary),
            _button("Обновить", "🔄", self.on_reload_record),
        ]
        if self.model is User:
            main.append(_button("Сбросить подразделение", "↩️", self.on_reset_division))
        container.add_item(_row(*main))
        container.add_item(_row(
            _button("Удалить", "🗑️", self.on_delete, discord.ButtonStyle.danger),
            _button("Назад", "⬅️", self.on_record_back),
        ))
        self._show(container)

    def delete_screen(self) -> None:
        pk = admin_db.pk_name(self.model)
        table_name = getattr(self.model.Settings, "name", self.model.__name__)
        container = discord.ui.Container(accent_color=RED)
        container.add_item(_text(
            f"## 🗑️ Удалить запись?\n-# {table_name} · {pk} = {getattr(self.record, pk)} · действие необратимо"
        ))
        container.add_item(_sep())
        container.add_item(_text(admin_db.render_record(self.model, self.record, limit=2000, value_limit=100)))
        container.add_item(_sep())
        container.add_item(_row(
            _button("Да, удалить", "🗑️", self.on_delete_confirm, discord.ButtonStyle.danger),
            _button("Отмена", "✖️", self.on_cancel_delete),
        ))
        self._show(container)

    # -- Навигация --

    async def on_home(self, interaction: discord.Interaction):
        self.home()
        await self._push(interaction)

    async def on_interfaces(self, interaction: discord.Interaction):
        self.interfaces()
        await self._push(interaction)

    async def on_tables(self, interaction: discord.Interaction):
        self.tables()
        await self._push(interaction)

    async def on_dossier_open(self, interaction: discord.Interaction):
        await interaction.response.send_modal(DossierModal(self))

    async def _after_record(self, interaction: discord.Interaction, status: str | None = None) -> None:
        if self.dossier_id is not None:
            user = await User.find_one(User.discord_id == self.dossier_id)
            if user is not None:
                await self._load_dossier(user, status)
                await self._push(interaction)
                return
        self.tables(status)
        await self._push(interaction)

    async def on_record_back(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self._after_record(interaction)

    # -- Системные действия --

    async def _system(self, interaction: discord.Interaction, action) -> None:
        await interaction.response.defer()
        try:
            status = "✅ " + await action()
        except Exception as error:
            logger.exception("Admin action failed")
            status = f"❌ `{type(error).__name__}`: {_clip(str(error), 300)}"
        self.home(status)
        await self._push(interaction)

    async def on_resync(self, interaction: discord.Interaction):
        async def action() -> str:
            guild = discord.Object(id=GUILD_ID)
            self.bot.tree.copy_global_to(guild=guild)
            synced = await self.bot.tree.sync(guild=guild)
            return f"Слэш-команды синхронизированы: {len(synced)}"

        await self._system(interaction, action)

    async def on_add_member_open(self, interaction: discord.Interaction):
        await interaction.response.send_modal(AddMemberModal(self))

    async def add_member(self, interaction: discord.Interaction, raw: str) -> None:
        try:
            discord_id = int(raw.strip())
            member = await self.bot.get_guild(GUILD_ID).fetch_member(discord_id)
            user, created = await presence.init_user(member)
            await self._load_dossier(user, "✅ Боец добавлен из ролей Discord" if created else "ℹ️ Боец уже есть в БД")
        except ValueError:
            self.home("❌ Нужен числовой Discord ID")
        except discord.NotFound:
            self.home("❌ Такого пользователя нет на сервере")
        except Exception as error:
            self.home(f"❌ `{type(error).__name__}`: {error}")
        await self._push(interaction)

    async def on_reload_divisions(self, interaction: discord.Interaction):
        async def action() -> str:
            await divisions.load()
            return f"Кэш подразделений перезагружен: {len(divisions.divisions)}"

        await self._system(interaction, action)

    async def on_logs(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            data = await asyncio.to_thread(_read_tail, LOG_PATH, LOG_TAIL_BYTES)
        except OSError as error:
            await interaction.followup.send(f"❌ Не удалось прочитать лог: {error}", ephemeral=True)
            return
        await interaction.followup.send(file=discord.File(io.BytesIO(data), filename="bot.log"), ephemeral=True)

    # -- Интерфейсы каналов --

    async def refresh_one(self, interaction: discord.Interaction, channel_id: int) -> None:
        await interaction.response.defer()
        target = bottom_messages.get_targets().get(channel_id)
        if target is None:
            status = "❌ Канал больше не в реестре"
        else:
            try:
                await bottom_messages.refresh_now(self.bot, target)
                status = f"✅ {target.label}"
            except Exception as error:
                status = f"❌ {target.label}: {error}"
        self.interfaces(status)
        await self._push(interaction)

    async def on_refresh_all(self, interaction: discord.Interaction):
        await interaction.response.defer()
        results = await bottom_messages.refresh_all(self.bot)
        lines = [f"{'✅' if error is None else '❌'} {t.label}" + (f": {error}" if error else "") for t, error in results]
        self.interfaces("\n".join(lines) or "Реестр пуст")
        await self._push(interaction)

    # -- Досье --

    async def _load_dossier(self, user: User, status: str | None = None) -> None:
        sections = await admin_db.dossier_sections(user.discord_id)
        self.dossier_screen(user, sections, status)

    async def open_dossier(self, interaction: discord.Interaction, raw: str) -> None:
        try:
            user = await admin_db.find_user(raw)
            if user is None:
                self.home(f"🔍 Боец `{_clip(raw.strip(), 40)}` не найден")
            else:
                await self._load_dossier(user)
        except Exception as error:
            self.home(f"❌ `{type(error).__name__}`: {error}")
        await self._push(interaction)

    async def on_dossier_refresh(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = await admin_db.reload(User, self.dossier_user)
        if user is None:
            self.home("❌ Боец больше не существует")
        else:
            await self._load_dossier(user)
        await self._push(interaction)

    async def on_dossier_card(self, interaction: discord.Interaction):
        await interaction.response.defer()
        user = await admin_db.reload(User, self.dossier_user)
        if user is None:
            self.home("❌ Боец больше не существует")
        else:
            self.model, self.record, self.found = User, user, 1
            self.record_screen()
        await self._push(interaction)

    async def open_request(self, interaction: discord.Interaction, table: str, pk: Any) -> None:
        await interaction.response.defer()
        model = admin_db.get_models()[table]
        row = await admin_db.load_row(model, pk)
        if row is None:
            await self._after_record(interaction, f"❌ `{table}` #{pk} больше не существует")
            return
        self.model, self.record, self.found = model, row, 1
        self.record_screen()
        await self._push(interaction)

    # -- Инспектор БД --

    async def search(self, interaction: discord.Interaction, model, field: str, raw: str) -> None:
        try:
            records = await admin_db.find_records(model, field, raw)
        except Exception as error:
            self.tables(f"❌ `{type(error).__name__}`: {error}")
            await self._push(interaction)
            return
        table_name = getattr(model.Settings, "name", model.__name__)
        if not records:
            self.tables(f"🔍 `{table_name}`: ничего не найдено")
            await self._push(interaction)
            return
        self.dossier_id = self.dossier_user = None
        self.model, self.record, self.found = model, records[0], len(records)
        self.record_screen()
        await self._push(interaction)

    async def apply_changes(self, interaction: discord.Interaction, changes: dict[str, str]) -> None:
        try:
            diff, self.record = await admin_db.update_fields(self.model, self.record, changes)
            if self.model is Division:
                await divisions.load()
            note = "\n".join(f"✅ `{name}`: {old} → {new}" for name, old, new in diff)
        except Exception as error:
            note = f"❌ `{type(error).__name__}`: {error}"
        self.record_screen(note)
        await self._push(interaction)

    async def on_edit(self, interaction: discord.Interaction):
        await interaction.response.send_modal(EditFieldModal(self))

    async def on_reload_record(self, interaction: discord.Interaction):
        await interaction.response.defer()
        fresh = await admin_db.reload(self.model, self.record)
        if fresh is None:
            await self._after_record(interaction, "❌ Запись больше не существует")
            return
        self.record = fresh
        self.record_screen()
        await self._push(interaction)

    async def on_reset_division(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.apply_changes(interaction, {"division": "null", "position": "null"})

    async def on_delete(self, interaction: discord.Interaction):
        self.delete_screen()
        await self._push(interaction)

    async def on_cancel_delete(self, interaction: discord.Interaction):
        self.record_screen()
        await self._push(interaction)

    async def on_delete_confirm(self, interaction: discord.Interaction):
        await interaction.response.defer()
        pk = getattr(self.record, admin_db.pk_name(self.model))
        table_name = getattr(self.model.Settings, "name", self.model.__name__)
        try:
            await self.record.delete()
            if self.model is Division:
                await divisions.load()
        except Exception as error:
            self.record_screen(f"❌ Не удалено: `{type(error).__name__}`: {error}")
            await self._push(interaction)
            return
        await self._after_record(interaction, f"🗑️ Удалено: `{table_name}` · `{pk}`")

    async def on_supply_link_open(self, interaction: discord.Interaction):
        await interaction.response.send_modal(SupplyLinkModal(self))

    async def update_supply_link(self, interaction: discord.Interaction, new_url: str):
        """Сохраняет ссылку в БД, меняет её в config и обновляет сообщение склада."""
        try:
            from database.models import Setting

            setting = await Setting.find_one(Setting.key == "supply_info_link")
            if setting:
                setting.value = new_url
                await setting.save()
            else:
                setting = Setting(key="supply_info_link", value=new_url)
                await setting.create()

            config.SUPPLY_INFO_LINK = new_url

            channel_id = config.CHANNELS.get("storage_requests")
            target = bottom_messages.get_targets().get(channel_id)
            if target:
                await bottom_messages.refresh_now(self.bot, target)

            status = f"✅ Ссылка на склад обновлена:\n{new_url}"
        except Exception as error:
            logger.exception("Failed to update supply link")
            status = f"❌ Ошибка обновления: {error}"

        self.interfaces(status)
        await self._push(interaction)