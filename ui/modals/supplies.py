import discord.ui

from utils.helpers import safe_respond


class ItemAmountModal(discord.ui.Modal):
    def __init__(self, item_name: str, current_qty: int = 0):
        super().__init__(title=f"Количество: {item_name[:20]}")
        self.item_name = item_name
        self.amount = discord.ui.TextInput(
            label="Введите количество",
            placeholder=f"Текущее: {current_qty}",
            default=str(current_qty) if current_qty > 0 else "",
            min_length=1,
            max_length=5,
            required=True,
        )
        self.add_item(self.amount)
        self.result = None

    async def on_submit(self, interaction: discord.Interaction):
        if not self.amount.value.isdigit():
            await safe_respond(interaction, "❌ Введите число.")
            return

        qty = int(self.amount.value)
        if qty < 0:
            await safe_respond(interaction, "❌ Число не может быть отрицательным.")
            return

        self.result = qty
        await interaction.response.defer()
