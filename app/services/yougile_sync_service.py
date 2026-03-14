from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.database.models import (
    YouGileBoard,
    YouGileColumn,
    YouGileProject,
    YouGileTask,
    YouGileTaskAssignee,
    YouGileTaskSticker,
)
from app.integrations.yougile import YouGileService


class YouGileSyncService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.api = YouGileService()

    @staticmethod
    def _task_deadline(raw_task: dict):
        raw = raw_task.get("deadline") or raw_task.get("dueDate") or raw_task.get("due_at")
        return YouGileService()._parse_deadline(raw)  # reuse parser logic

    def _upsert_project(self, user_id: int, raw: dict) -> YouGileProject | None:
        project_id = str(raw.get("id") or "").strip()
        if not project_id:
            return None
        row = (
            self.db.query(YouGileProject)
            .filter(YouGileProject.user_id == user_id, YouGileProject.project_id == project_id)
            .one_or_none()
        )
        if not row:
            row = YouGileProject(user_id=user_id, project_id=project_id)
            self.db.add(row)
        row.title = str(raw.get("title") or raw.get("name") or "Untitled project")
        row.deleted = bool(raw.get("deleted", False))
        row.raw_json = json.dumps(raw, ensure_ascii=False)
        return row

    def _upsert_board(self, user_id: int, raw: dict) -> YouGileBoard | None:
        board_id = str(raw.get("id") or "").strip()
        if not board_id:
            return None
        row = (
            self.db.query(YouGileBoard)
            .filter(YouGileBoard.user_id == user_id, YouGileBoard.board_id == board_id)
            .one_or_none()
        )
        if not row:
            row = YouGileBoard(user_id=user_id, board_id=board_id)
            self.db.add(row)
        row.project_id = str(raw.get("projectId") or raw.get("boardProjectId") or "").strip() or None
        row.title = str(raw.get("title") or raw.get("name") or "Untitled board")
        row.deleted = bool(raw.get("deleted", False))
        row.raw_json = json.dumps(raw, ensure_ascii=False)
        return row

    def _upsert_column(self, user_id: int, raw: dict, board_to_project: dict[str, str] | None = None) -> YouGileColumn | None:
        column_id = str(raw.get("id") or "").strip()
        if not column_id:
            return None
        row = (
            self.db.query(YouGileColumn)
            .filter(YouGileColumn.user_id == user_id, YouGileColumn.column_id == column_id)
            .one_or_none()
        )
        if not row:
            row = YouGileColumn(user_id=user_id, column_id=column_id)
            self.db.add(row)
        board_id = str(raw.get("boardId") or "").strip()
        project_id_raw = str(raw.get("projectId") or "").strip()
        mapped_project = (board_to_project or {}).get(board_id) if board_id else None
        row.board_id = board_id or None
        row.project_id = project_id_raw or mapped_project or board_id or None
        row.title = str(raw.get("title") or raw.get("name") or "Untitled column")
        row.deleted = bool(raw.get("deleted", False))
        row.raw_json = json.dumps(raw, ensure_ascii=False)
        return row

    def _replace_task_links(self, user_id: int, task_row: YouGileTask, raw: dict) -> None:
        self.db.query(YouGileTaskAssignee).filter(YouGileTaskAssignee.task_row_id == task_row.id).delete()
        self.db.query(YouGileTaskSticker).filter(YouGileTaskSticker.task_row_id == task_row.id).delete()

        assigned = raw.get("assigned") or []
        if isinstance(assigned, list):
            for member in assigned:
                member_id = str(member).strip()
                if member_id:
                    self.db.add(YouGileTaskAssignee(user_id=user_id, task_row_id=task_row.id, member_id=member_id))

        stickers = raw.get("stickers") or {}
        if isinstance(stickers, dict):
            for key, value in stickers.items():
                sticker_key = str(key).strip()
                if sticker_key:
                    self.db.add(
                        YouGileTaskSticker(
                            user_id=user_id,
                            task_row_id=task_row.id,
                            sticker_key=sticker_key,
                            sticker_value=str(value) if value is not None else None,
                        )
                    )

    def _upsert_task(
        self,
        user_id: int,
        raw: dict,
        board_to_project: dict[str, str] | None = None,
        board_titles: dict[str, str] | None = None,
        project_titles: dict[str, str] | None = None,
        column_titles: dict[str, str] | None = None,
    ) -> YouGileTask | None:
        task_id = str(raw.get("id") or "").strip()
        if not task_id:
            return None
        row = (
            self.db.query(YouGileTask)
            .filter(YouGileTask.user_id == user_id, YouGileTask.task_id == task_id)
            .one_or_none()
        )
        if not row:
            row = YouGileTask(user_id=user_id, task_id=task_id)
            self.db.add(row)
            self.db.flush()

        board_id = str(raw.get("boardId") or "").strip()
        column_id = str(raw.get("columnId") or "").strip()
        project_id_raw = str(raw.get("projectId") or "").strip()
        mapped_project = (board_to_project or {}).get(board_id) if board_id else None

        row.board_id = board_id or None
        row.project_id = project_id_raw or mapped_project or None
        row.column_id = column_id or None
        row.project_title_cache = (project_titles or {}).get(row.project_id or "")
        row.board_title_cache = (board_titles or {}).get(board_id)
        row.column_title_cache = (column_titles or {}).get(column_id)
        if not row.board_title_cache and board_id:
            board_row = (
                self.db.query(YouGileBoard)
                .filter(YouGileBoard.user_id == user_id, YouGileBoard.board_id == board_id)
                .one_or_none()
            )
            if board_row:
                row.board_title_cache = board_row.title
        if not row.project_title_cache and row.project_id:
            project_row = (
                self.db.query(YouGileProject)
                .filter(YouGileProject.user_id == user_id, YouGileProject.project_id == row.project_id)
                .one_or_none()
            )
            if project_row:
                row.project_title_cache = project_row.title
        if not row.column_title_cache and column_id:
            column_row = (
                self.db.query(YouGileColumn)
                .filter(YouGileColumn.user_id == user_id, YouGileColumn.column_id == column_id)
                .one_or_none()
            )
            if column_row:
                row.column_title_cache = column_row.title
        row.status_flag = row.column_id
        row.title = str(raw.get("title") or raw.get("name") or "Untitled task")
        row.description = raw.get("description")
        row.completed = bool(raw.get("completed", False))
        row.archived = bool(raw.get("archived", False))
        row.deleted = bool(raw.get("deleted", False))
        row.due_at = self._task_deadline(raw)
        row.raw_json = json.dumps(raw, ensure_ascii=False)
        self._replace_task_links(user_id, row, raw)
        return row

    def sync_all(self, user_id: int) -> dict[str, int]:
        projects = self.api.list_projects()
        boards = self.api.list_boards()
        columns = self.api.list_columns()
        tasks = self.api.list_tasks_raw()

        board_to_project: dict[str, str] = {}
        board_titles: dict[str, str] = {}
        project_titles: dict[str, str] = {
            str(project.get("id") or "").strip(): str(project.get("title") or project.get("name") or "").strip()
            for project in projects
            if str(project.get("id") or "").strip()
        }
        column_titles: dict[str, str] = {}
        for board in boards:
            board_id = str(board.get("id") or "").strip()
            project_id = str(board.get("projectId") or board.get("boardProjectId") or "").strip()
            if board_id and project_id:
                board_to_project[board_id] = project_id
            if board_id:
                board_titles[board_id] = str(board.get("title") or board.get("name") or "").strip()
        for column in columns:
            column_id = str(column.get("id") or "").strip()
            if column_id:
                column_titles[column_id] = str(column.get("title") or column.get("name") or "").strip()

        p_count = 0
        b_count = 0
        c_count = 0
        t_count = 0
        project_ids: set[str] = set()
        board_ids: set[str] = set()
        column_ids: set[str] = set()
        task_ids: set[str] = set()
        for raw in projects:
            row = self._upsert_project(user_id, raw)
            if row:
                project_ids.add(row.project_id)
                p_count += 1
        for raw in boards:
            row = self._upsert_board(user_id, raw)
            if row:
                board_ids.add(row.board_id)
                b_count += 1
        for raw in columns:
            row = self._upsert_column(user_id, raw, board_to_project=board_to_project)
            if row:
                column_ids.add(row.column_id)
                c_count += 1
        for raw in tasks:
            row = self._upsert_task(
                user_id,
                raw,
                board_to_project=board_to_project,
                board_titles=board_titles,
                project_titles=project_titles,
                column_titles=column_titles,
            )
            if row:
                task_ids.add(row.task_id)
                t_count += 1

        # Soft-clean stale rows from previous company selections to keep local cache consistent.
        if project_ids:
            (
                self.db.query(YouGileProject)
                .filter(YouGileProject.user_id == user_id, ~YouGileProject.project_id.in_(project_ids))
                .update({"deleted": True}, synchronize_session=False)
            )
        if board_ids:
            (
                self.db.query(YouGileBoard)
                .filter(YouGileBoard.user_id == user_id, ~YouGileBoard.board_id.in_(board_ids))
                .update({"deleted": True}, synchronize_session=False)
            )
        if column_ids:
            (
                self.db.query(YouGileColumn)
                .filter(YouGileColumn.user_id == user_id, ~YouGileColumn.column_id.in_(column_ids))
                .update({"deleted": True}, synchronize_session=False)
            )
        if task_ids:
            (
                self.db.query(YouGileTask)
                .filter(YouGileTask.user_id == user_id, ~YouGileTask.task_id.in_(task_ids))
                .update({"deleted": True}, synchronize_session=False)
            )
        self.db.commit()
        return {"projects": p_count, "boards": b_count, "columns": c_count, "tasks": t_count}

    def list_projects(self, user_id: int) -> list[YouGileProject]:
        return (
            self.db.query(YouGileProject)
            .filter(YouGileProject.user_id == user_id, YouGileProject.deleted.is_(False))
            .order_by(YouGileProject.title.asc())
            .all()
        )

    def list_boards(self, user_id: int, project_id: str | None = None) -> list[YouGileBoard]:
        q = self.db.query(YouGileBoard).filter(YouGileBoard.user_id == user_id, YouGileBoard.deleted.is_(False))
        if project_id:
            q = q.filter(YouGileBoard.project_id == project_id)
        return q.order_by(YouGileBoard.title.asc()).all()

    def list_columns(self, user_id: int, project_id: str | None = None, board_id: str | None = None) -> list[YouGileColumn]:
        q = self.db.query(YouGileColumn).filter(YouGileColumn.user_id == user_id)
        if project_id:
            q = q.filter(YouGileColumn.project_id == project_id)
        if board_id:
            q = q.filter(YouGileColumn.board_id == board_id)
        q = q.filter(YouGileColumn.deleted.is_(False))
        return q.order_by(YouGileColumn.title.asc()).all()

    def list_tasks(self, user_id: int, project_id: str | None = None, column_id: str | None = None) -> list[YouGileTask]:
        q = self.db.query(YouGileTask).filter(YouGileTask.user_id == user_id)
        if project_id:
            q = q.filter(YouGileTask.project_id == project_id)
        if column_id:
            q = q.filter(YouGileTask.column_id == column_id)
        return q.order_by(YouGileTask.updated_at.desc()).all()

    def create_project(self, user_id: int, title: str) -> YouGileProject | None:
        created = self.api.create_project(title)
        if not created:
            return None
        row = self._upsert_project(user_id, created)
        self.db.commit()
        return row

    def update_project(self, user_id: int, project_id: str, patch: dict) -> YouGileProject | None:
        updated = self.api.update_project(project_id, patch)
        if not updated:
            return None
        row = self._upsert_project(user_id, updated)
        self.db.commit()
        return row

    def delete_project(self, user_id: int, project_id: str) -> bool:
        ok = self.api.delete_project(project_id)
        if not ok:
            return False
        row = (
            self.db.query(YouGileProject)
            .filter(YouGileProject.user_id == user_id, YouGileProject.project_id == project_id)
            .one_or_none()
        )
        if row:
            row.deleted = True
            row.updated_at = datetime.utcnow()
            self.db.commit()
        return True

    def create_column(self, user_id: int, title: str, project_id: str, color: int | None = None) -> YouGileColumn | None:
        created = self.api.create_column(title, project_id, color=color)
        if not created:
            return None
        row = self._upsert_column(user_id, created)
        self.db.commit()
        return row

    def update_column(self, user_id: int, column_id: str, patch: dict) -> YouGileColumn | None:
        updated = self.api.update_column(column_id, patch)
        if not updated:
            return None
        row = self._upsert_column(user_id, updated)
        self.db.commit()
        return row

    def delete_column(self, user_id: int, column_id: str) -> bool:
        ok = self.api.delete_column(column_id)
        if not ok:
            return False
        row = (
            self.db.query(YouGileColumn)
            .filter(YouGileColumn.user_id == user_id, YouGileColumn.column_id == column_id)
            .one_or_none()
        )
        if row:
            row.deleted = True
            row.updated_at = datetime.utcnow()
            self.db.commit()
        return True

    def create_task(
        self,
        user_id: int,
        title: str,
        column_id: str,
        description: str | None = None,
        assigned: list[str] | None = None,
        stickers: dict[str, str] | None = None,
    ) -> YouGileTask | None:
        created = self.api.create_task_raw(title, column_id, description=description, assigned=assigned, stickers=stickers)
        if not created:
            return None
        row = self._upsert_task(user_id, created)
        self.db.commit()
        return row

    def update_task(self, user_id: int, task_id: str, patch: dict) -> YouGileTask | None:
        local_only_keys = {"ready_for_calendar", "linked_calendar_event_id"}
        local_patch = {k: patch[k] for k in list(patch.keys()) if k in local_only_keys}
        remote_patch = {k: v for k, v in patch.items() if k not in local_only_keys}

        updated: dict | None
        if remote_patch:
            updated = self.api.update_task_raw(task_id, remote_patch)
            if not updated:
                return None
        else:
            updated = self.api.get_task_by_id(task_id)
            if not updated:
                row = (
                    self.db.query(YouGileTask)
                    .filter(YouGileTask.user_id == user_id, YouGileTask.task_id == task_id)
                    .one_or_none()
                )
                if not row:
                    return None
                if "ready_for_calendar" in local_patch:
                    row.ready_for_calendar = bool(local_patch["ready_for_calendar"])
                if "linked_calendar_event_id" in local_patch:
                    row.linked_calendar_event_id = local_patch["linked_calendar_event_id"]
                self.db.commit()
                return row

        if not updated:
            return None
        row = self._upsert_task(user_id, updated)
        if row and "ready_for_calendar" in local_patch:
            row.ready_for_calendar = bool(local_patch["ready_for_calendar"])
        if row and "linked_calendar_event_id" in local_patch:
            row.linked_calendar_event_id = local_patch["linked_calendar_event_id"]
        self.db.commit()
        return row

    def move_task(self, user_id: int, task_id: str, target_column_id: str) -> YouGileTask | None:
        moved = self.api.move_task(task_id, target_column_id)
        if not moved:
            return None
        row = self._upsert_task(user_id, moved)
        self.db.commit()
        return row

    def delete_task(self, user_id: int, task_id: str) -> bool:
        ok = self.api.delete_task(task_id)
        if not ok:
            return False
        row = (
            self.db.query(YouGileTask)
            .filter(YouGileTask.user_id == user_id, YouGileTask.task_id == task_id)
            .one_or_none()
        )
        if row:
            row.deleted = True
            row.updated_at = datetime.utcnow()
            self.db.commit()
        return True
