from dataclasses import dataclass
from datetime import datetime, time, timedelta

from app.database.models import EnergyCost, Event, PriorityLevel, Task
from app.utils.time_utils import overlaps


@dataclass
class ScheduledItem:
    task_id: int
    title: str
    start: datetime
    end: datetime
    energy_cost: EnergyCost


def energy_level_for_hour(hour: int) -> EnergyCost:
    if 8 <= hour < 11:
        return EnergyCost.high
    if 11 <= hour < 20:
        return EnergyCost.medium
    return EnergyCost.low


class AIScheduler:
    def __init__(self, max_daily_work_minutes: int = 480) -> None:
        self.max_daily_work_minutes = max_daily_work_minutes

    def detect_free_slots(
        self,
        day_start: datetime,
        day_end: datetime,
        events: list[Event],
        min_slot_minutes: int = 30,
    ) -> list[tuple[datetime, datetime]]:
        windows = [(day_start, day_end)]
        for event in sorted(events, key=lambda e: e.start_time):
            next_windows: list[tuple[datetime, datetime]] = []
            for start, end in windows:
                if not overlaps(start, end, event.start_time, event.end_time):
                    next_windows.append((start, end))
                    continue
                if start < event.start_time:
                    next_windows.append((start, event.start_time))
                if event.end_time < end:
                    next_windows.append((event.end_time, end))
            windows = next_windows

        min_delta = timedelta(minutes=min_slot_minutes)
        return [(s, e) for s, e in windows if e - s >= min_delta]

    def sort_tasks(self, tasks: list[Task]) -> list[Task]:
        priority_weight = {
            PriorityLevel.high: 0,
            PriorityLevel.medium: 1,
            PriorityLevel.low: 2,
        }
        return sorted(
            tasks,
            key=lambda t: (
                priority_weight.get(t.priority, 1),
                t.deadline or datetime.max,
                t.duration_minutes,
            ),
        )

    def build_day_window(self, for_date: datetime) -> tuple[datetime, datetime]:
        day_start = datetime.combine(for_date.date(), time(8, 0))
        day_end = datetime.combine(for_date.date(), time(22, 0))
        return day_start, day_end

    def schedule_tasks(self, tasks: list[Task], free_slots: list[tuple[datetime, datetime]]) -> list[ScheduledItem]:
        scheduled: list[ScheduledItem] = []
        total_work = 0
        ordered_tasks = self.sort_tasks(tasks)
        remaining_slots = sorted(free_slots, key=lambda x: x[0])

        for task in ordered_tasks:
            if total_work + task.duration_minutes > self.max_daily_work_minutes:
                continue
            duration = timedelta(minutes=task.duration_minutes)

            for idx, (slot_start, slot_end) in enumerate(remaining_slots):
                candidate_start = slot_start
                candidate_end = candidate_start + duration
                if candidate_end > slot_end:
                    continue

                slot_energy = energy_level_for_hour(candidate_start.hour)
                if not self._energy_match(slot_energy, task.energy_cost):
                    continue

                scheduled.append(
                    ScheduledItem(
                        task_id=task.id,
                        title=task.title,
                        start=candidate_start,
                        end=candidate_end,
                        energy_cost=task.energy_cost,
                    )
                )
                total_work += task.duration_minutes

                new_slots: list[tuple[datetime, datetime]] = []
                if slot_start < candidate_start:
                    new_slots.append((slot_start, candidate_start))
                rest_buffer_end = candidate_end + timedelta(minutes=10)
                if rest_buffer_end < slot_end:
                    new_slots.append((rest_buffer_end, slot_end))
                remaining_slots = remaining_slots[:idx] + new_slots + remaining_slots[idx + 1 :]
                break

        return sorted(scheduled, key=lambda item: item.start)

    @staticmethod
    def _energy_match(slot_energy: EnergyCost, task_energy: EnergyCost) -> bool:
        order = {EnergyCost.low: 0, EnergyCost.medium: 1, EnergyCost.high: 2}
        return order[slot_energy] >= order[task_energy]
