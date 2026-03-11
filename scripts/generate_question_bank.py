from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path


def build_catalog() -> list[dict[str, object]]:
    return [
        {
            "category": "current_focus",
            "expected_intent": "current_focus",
            "templates": [
                "Что у меня на сейчас?",
                "Чем мне сейчас заниматься?",
                "Какой у меня текущий фокус?",
                "Что мне делать прямо сейчас?",
                "Сейчас я занят или свободен?",
                "На сейчас какое у меня дело?",
                "Что у меня в плане в этот момент?",
                "Какой блок у меня идет прямо сейчас?",
                "Что мне делать в {time}?",
                "Если сейчас {time}, чем я должен заниматься?",
            ],
        },
        {
            "category": "next_after_now",
            "expected_intent": "current_focus",
            "templates": [
                "Что у меня идет после этого?",
                "Что идет после текущего дела?",
                "Какое следующее дело после текущего?",
                "Что у меня дальше по расписанию?",
                "Что будет после {time}?",
                "Что у меня после текущего слота?",
                "Какой следующий блок на сегодня?",
                "После этого чем я должен заняться?",
                "Что у меня сразу после текущего события?",
                "Что дальше по календарю?",
            ],
        },
        {
            "category": "done_check",
            "expected_intent": "",
            "templates": [
                "Я уже сделал это дело?",
                "Отмечено ли у меня как выполнено последнее дело?",
                "Покажи, что уже выполнено сегодня.",
                "Какие дела я уже закрыл сегодня?",
                "Что из сегодняшнего уже завершено?",
                "Я уже выполнил задачу после {time}?",
                "Есть ли сегодня выполненные задачи?",
                "Сколько дел уже сделано сегодня?",
                "Что у меня уже сделано по плану?",
                "Покажи список завершенных дел за сегодня.",
            ],
        },
        {
            "category": "today_plan",
            "expected_intent": "today_plan",
            "templates": [
                "Покажи события на сегодня.",
                "Что у меня запланировано на сегодня?",
                "Дай полный план дня.",
                "Какие у меня дела сегодня?",
                "Покажи расписание на сегодня по времени.",
                "Что у меня в календаре на сегодня?",
                "Скинь список всех событий на сегодня.",
                "Дай мне мои планы на сегодняшний день.",
                "Что сегодня по календарю?",
                "Покажи день целиком без сокращений.",
            ],
        },
        {
            "category": "follow_up_after_event",
            "expected_intent": "today_plan",
            "templates": [
                "Что у меня после {event}?",
                "Что идет после {time}?",
                "После блока {time_range} что дальше?",
                "Что у меня после уроков?",
                "Что у меня после ужина?",
                "После регистрации что идет?",
                "Что по плану после {time} сегодня?",
                "Что у меня после этого события?",
                "Что идет следующим после {event}?",
                "А что дальше после {time_range}?",
            ],
        },
        {
            "category": "date_clarification",
            "expected_intent": "today_plan",
            "templates": [
                "Это расписание на какое число?",
                "На какую дату этот список?",
                "За какой день ты мне это показал?",
                "Это планы на сегодня или завтра?",
                "Какая дата у этого расписания?",
                "Это точно на сегодня?",
                "Этот план за какой день?",
                "На какой день ты сейчас смотришь?",
                "Это про сегодняшний календарь?",
                "Уточни дату этого расписания.",
            ],
        },
        {
            "category": "bedtime",
            "expected_intent": "bedtime",
            "templates": [
                "Когда я должен быть в кровати?",
                "Во сколько мне сегодня ложиться?",
                "Когда по плану начинается сон?",
                "Во сколько у меня блок 'В кровати'?",
                "Когда я должен лечь спать?",
                "Во сколько стартует сон по календарю?",
                "Когда у меня время на сон сегодня?",
                "Во сколько я должен быть уже в кровати?",
                "Какое время отбоя у меня сегодня?",
                "Когда планируетcя 'В кровати'?",
            ],
        },
        {
            "category": "calendar_add",
            "expected_intent": "",
            "templates": [
                "Добавь сегодня {time_range} {activity}.",
                "Поставь в календарь {time_range} {activity}.",
                "Запланируй на сегодня {activity} с {time_start} до {time_end}.",
                "Добавь событие {activity} на {time_range}.",
                "Создай слот {activity} сегодня в {time_range}.",
                "Поставь мне {activity} сегодня на {time_range}.",
                "Добавь в мой календарь {activity} {time_range}.",
                "Сделай событие {activity} с {time_start} до {time_end}.",
                "Запиши {activity} на {time_range} сегодня.",
                "Вставь в расписание {activity} на {time_range}.",
            ],
        },
        {
            "category": "calendar_delete",
            "expected_intent": "",
            "templates": [
                "Удали событие {event}.",
                "Удали из календаря {event} на сегодня.",
                "Убери слот {time_range} {event}.",
                "Сотри событие {time_range} {event}.",
                "Нужно удалить {event} из плана.",
                "Удали запись {event} сегодня.",
                "Сними из расписания {event} на {time_range}.",
                "Удали встречу {event}.",
                "Убери событие в {time_range} с названием {event}.",
                "Удали это дело из календаря: {event}.",
            ],
        },
        {
            "category": "calendar_move",
            "expected_intent": "",
            "templates": [
                "Перенеси {event} с {time_range} на {alt_time_range}.",
                "Сдвинь {event} на {alt_time_range}.",
                "Переставь {event} на позже: {alt_time_range}.",
                "Перенеси событие {event} на другое время {alt_time_range}.",
                "Можно передвинуть {event} с {time_start} на {alt_time_start}?",
                "Поменяй время {event} на {alt_time_range}.",
                "Перенеси {event} на завтра в {alt_time_range}.",
                "Сдвинь блок {event} на {alt_time_start}.",
                "Перенеси мою активность {event} на {alt_time_range}.",
                "Поставь {event} вместо {time_range} на {alt_time_range}.",
            ],
        },
        {
            "category": "connections_check",
            "expected_intent": "",
            "templates": [
                "Проверь, подключены ли календарь и YouGile.",
                "Статус интеграций сейчас?",
                "Календарь/YouGile/AI работают?",
                "Есть ли соединение с календарем?",
                "Ты сейчас видишь мой календарь?",
                "Проверь доступ к Google Calendar.",
                "Проверь подключение YouGile.",
                "Покажи статус всех подключений.",
                "Интеграции в норме?",
                "Покажи, что подключено прямо сейчас.",
            ],
        },
        {
            "category": "planning_help",
            "expected_intent": "",
            "templates": [
                "Помоги распланировать мой день без перегруза.",
                "Оптимизируй мой план на сегодня.",
                "Как мне лучше распределить дела сегодня?",
                "Перепланируй день с учетом отдыха.",
                "Сделай реалистичный план на сегодня.",
                "Как лучше расставить приоритеты на сегодня?",
                "Нужен сбалансированный план: работа и отдых.",
                "Помоги не перегореть и закрыть дела.",
                "Дай план с паузами и отдыхом.",
                "Составь адекватный график на день.",
            ],
        },
        {
            "category": "habit_status",
            "expected_intent": "",
            "templates": [
                "Какие привычки у меня просрочены?",
                "Что по моим привычкам на этой неделе?",
                "Какие привычки сегодня нужно закрыть?",
                "Покажи статус привычек.",
                "Есть ли просроченные привычки сейчас?",
                "Что с привычкой тренировки?",
                "Какие привычки я давно не делал?",
                "Напомни мне про привычки на сегодня.",
                "Сколько привычек выполнено за неделю?",
                "Какие привычки требуют внимания?",
            ],
        },
        {
            "category": "weekly_report",
            "expected_intent": "",
            "templates": [
                "Покажи отчет за неделю.",
                "Какой у меня прогресс за неделю?",
                "Сколько задач выполнено за неделю?",
                "Дай недельный отчет продуктивности.",
                "Что по итогам недели?",
                "Покажи сводку: открытые и закрытые задачи.",
                "Какой weekly report на сейчас?",
                "Покажи баланс работы и отдыха за неделю.",
                "Сделай недельный срез по делам.",
                "Какая статистика за последнюю неделю?",
            ],
        },
    ]


def generate_rows(count: int, seed: int = 42) -> list[dict[str, str]]:
    rnd = random.Random(seed)
    catalog = build_catalog()

    times = ["06:30", "07:40", "08:00", "09:00", "10:00", "11:00", "14:00", "17:00", "18:30", "19:00", "20:00", "21:15"]
    ranges = ["20:00-21:00", "20:30-21:00", "19:10-19:40", "17:00-18:30", "18:30-19:00", "21:15-21:30"]
    alt_ranges = ["21:00-21:30", "19:30-20:00", "20:40-21:10", "16:30-17:00", "18:00-18:30"]
    events = ["Регистрация", "Ужин + Собойка", "Английский Listening", "Подготовка ко сну", "Рисование"]
    activities = ["Рисование", "Чтение", "Прогулка", "Английский", "Планирование", "Разбор задач", "Тренировка"]

    rows: list[dict[str, str]] = []
    idx = 0
    while len(rows) < count:
        item = catalog[idx % len(catalog)]
        template = rnd.choice(item["templates"])  # type: ignore[index]
        time_start = rnd.choice(times)
        time_end = rnd.choice(times)
        if time_end <= time_start:
            # simple deterministic fallback
            time_end = "21:00"
        mapping = {
            "time": rnd.choice(times),
            "time_start": time_start,
            "time_end": time_end,
            "time_range": rnd.choice(ranges),
            "alt_time_start": rnd.choice(times),
            "alt_time_range": rnd.choice(alt_ranges),
            "event": rnd.choice(events),
            "activity": rnd.choice(activities),
        }
        question = template.format(**mapping)
        rows.append(
            {
                "category": str(item["category"]),
                "expected_intent": str(item["expected_intent"]),
                "question": question,
            }
        )
        idx += 1

    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate large training question bank CSV.")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=str, default="training/question_bank.csv")
    args = parser.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = generate_rows(count=max(1, args.count), seed=args.seed)

    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["category", "expected_intent", "question"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Generated {len(rows)} questions -> {out_path}")


if __name__ == "__main__":
    main()
