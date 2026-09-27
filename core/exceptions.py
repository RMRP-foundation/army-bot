from discord.ext import commands


class ServiceError(Exception):
    """Базовое исключение для ошибок валидации в сервисах.

    Передает понятный текст ошибки напрямую в UI слой.
    """

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ModalInputRequired(Exception):
    """
    Исключение, которое выбрасывается когда пользователю
    показан модал для ввода static ID или nickname.
    Глобально игнорируется в обработчике ошибок View.
    """

    pass


class SilentCheckFailure(commands.CheckFailure):
    """Тихий отказ в доступе — не логируется и не отправляется пользователю."""

    pass