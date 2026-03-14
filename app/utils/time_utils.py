from datetime import datetime, timedelta


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def split_by_duration(start: datetime, end: datetime, duration_minutes: int) -> list[tuple[datetime, datetime]]:
    slots: list[tuple[datetime, datetime]] = []
    cursor = start
    delta = timedelta(minutes=duration_minutes)
    while cursor + delta <= end:
        slots.append((cursor, cursor + delta))
        cursor += delta
    return slots
