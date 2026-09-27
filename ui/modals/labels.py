import random

import discord.ui


def name_input() -> discord.ui.TextInput:
    return discord.ui.TextInput(label="Ваше имя и фамилия", placeholder="Иван Иванов", max_length=25)

def static_label() -> discord.ui.Label:
    return discord.ui.Label(
        text="Ваш «Статик»",
        description="Статик - ваш игровой идентификатор. "
                    "Посмотреть его можно в вашем паспорте, он будет формата XXX-XXX.",
        component=discord.ui.TextInput(
            style=discord.TextStyle.short, placeholder="XXX-XXX", max_length=7
        ),
    )

def period_input():
    return discord.ui.TextInput(
        label="Период", placeholder="17:00 - 18:00", max_length=25
    )

def evidence_input(label: str):
    return discord.ui.TextInput(
                label=label,
                style=discord.TextStyle.paragraph,
                placeholder="Перечислите выполненную работу и прикрепите ссылки на доказательства",
                max_length=1024,
            )

def score_input():
    return discord.ui.TextInput(
                label="Общее количество баллов",
                placeholder="Например: 300 из 300",
                max_length=100,
            )

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

def build_sso_quiz(data, index) -> tuple[discord.ui.Label, discord.ui.Select]:
    options = data['o'][:]
    random.shuffle(options)

    inner_select = discord.ui.Select(
        placeholder="Выберите ответ...",
        options=[discord.SelectOption(label=opt) for opt in options]
    )
    result_label = discord.ui.Label(text=f"{index}. {data['q']}"[:45], component=inner_select)

    return result_label, inner_select

def static_reminder():
    return discord.ui.TextDisplay("Ваш статик уже установлен в системе.")

def patrol_reminder():
    return discord.ui.TextDisplay(
        "-# Подавая запрос, вы подтверждаете знание правил ношения формы, обязуетесь быть в спецсвязи ССО, "
        "выполнять задачи возложенные на подразделение, взаимодействовать с бойцами подразделения "
        "и выполнять приказы его командиров."
    )