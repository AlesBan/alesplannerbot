from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


def _column_exists(engine: Engine, table_name: str, column_name: str) -> bool:
    inspector = inspect(engine)
    try:
        columns = inspector.get_columns(table_name)
    except Exception:
        return False
    return any(str(col.get("name")) == column_name for col in columns)


def ensure_yougile_columns(engine: Engine) -> None:
    ddl: list[tuple[str, str]] = [
        ("yougile_columns", "ALTER TABLE yougile_columns ADD COLUMN board_id VARCHAR(128)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN board_id VARCHAR(128)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN project_title_cache VARCHAR(255)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN board_title_cache VARCHAR(255)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN column_title_cache VARCHAR(255)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN status_flag VARCHAR(64)"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN ready_for_calendar BOOLEAN DEFAULT 0"),
        ("yougile_tasks", "ALTER TABLE yougile_tasks ADD COLUMN linked_calendar_event_id INTEGER"),
    ]
    expected = {
        ("yougile_columns", "board_id"),
        ("yougile_tasks", "board_id"),
        ("yougile_tasks", "project_title_cache"),
        ("yougile_tasks", "board_title_cache"),
        ("yougile_tasks", "column_title_cache"),
        ("yougile_tasks", "status_flag"),
        ("yougile_tasks", "ready_for_calendar"),
        ("yougile_tasks", "linked_calendar_event_id"),
    }
    with engine.begin() as conn:
        for table_name, statement in ddl:
            col_name = statement.split(" ADD COLUMN ", 1)[1].split(" ", 1)[0]
            if (table_name, col_name) not in expected:
                continue
            if _column_exists(engine, table_name, col_name):
                continue
            try:
                conn.execute(text(statement))
            except Exception:
                # Best-effort compatibility patch. If DB doesn't support a statement,
                # normal ORM operations still work for databases created from scratch.
                continue
