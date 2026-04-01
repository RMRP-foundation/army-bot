from discord.ext import commands


class StaticInputRequired(Exception):
    """
    Исключение, которое выбрасывается когда пользователю
    показан модал для ввода static ID.
    Глобально игнорируется в обработчике ошибок View.
    """

    pass


class SilentCheckFailure(commands.CheckFailure):
    """Тихий отказ в доступе — не логируется и не отправляется пользователю."""

    pass