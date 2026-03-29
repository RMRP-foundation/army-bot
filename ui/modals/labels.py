import random

import discord.ui


def name_component():
    return discord.ui.TextInput(
        label="Ваше имя и фамилия", placeholder="Иван Иванов", max_length=25
    )


def static_label():
    description = (
        "Статик - ваш игровой идентификатор. "
        "Посмотреть его можно в вашем паспорте, он будет формата XXX-XXX."
    )
    return discord.ui.Label(
        text="Ваш «Статик»",
        description=description,
        component=discord.ui.TextInput(
            style=discord.TextStyle.short, placeholder="XXX-XXX", max_length=7
        ),
    )


def static_reminder():
    return discord.ui.TextDisplay("Ваш статик уже установлен в системе.")


def screenshot_label(element: str):
    return discord.ui.Label(
        text=f"Копия {element}",
        description=f"Загрузите на фотохостинг скриншот {element} и вставьте ссылку.",
        component=discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder=f"Ссылка на скриншот {element}",
            max_length=200,
        ),
    )

def period_label():
    return discord.ui.TextInput(
        label="Период", placeholder="17:00 - 18:00", max_length=25
    )

def sso_quiz_field(data, index):
    options = data['o'][:]
    random.shuffle(options)

    sel = discord.ui.Select(
        placeholder="Выберите ответ...",
        options=[discord.SelectOption(label=opt) for opt in options]
    )
    return discord.ui.Label(text=f"{index}. {data['q']}"[:45], component=sel), sel

def patrol_reminder():
    return discord.ui.TextDisplay(
        "-# Подавая запрос, вы подтверждаете знание правил ношения формы, обязуетесь быть в спецсвязи ССО, "
        "выполнять задачи возложенные на подразделение, взаимодействовать с бойцами подразделения "
        "и выполнять приказы его командиров."
    )