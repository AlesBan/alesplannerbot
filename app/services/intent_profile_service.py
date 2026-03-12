from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import IntentProfile
from app.services.query_profile_matcher import QueryProfile


DEFAULT_PROFILES_PATH = Path(__file__).resolve().parents[1] / "data" / "intent_profiles.json"


class IntentProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _parse_row(row: IntentProfile) -> QueryProfile | None:
        if not row.enabled:
            return None
        try:
            tokens = json.loads(row.token_keywords_json or "[]")
            phrases = json.loads(row.phrase_keywords_json or "[]")
            aliases = json.loads(row.aliases_json or "[]")
        except Exception:
            return None
        if not isinstance(tokens, list) or not isinstance(phrases, list) or not isinstance(aliases, list):
            return None
        return QueryProfile(
            name=row.profile_name,
            token_keywords=frozenset(str(t).strip().lower() for t in tokens if str(t).strip()),
            phrase_keywords=frozenset(str(t).strip().lower() for t in phrases if str(t).strip()),
            aliases=frozenset(str(t).strip().lower() for t in aliases if str(t).strip()),
            threshold=float(row.threshold or 0.5),
        )

    @classmethod
    def seed_defaults(cls, db: Session) -> None:
        if not DEFAULT_PROFILES_PATH.exists():
            return
        payload = json.loads(DEFAULT_PROFILES_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return

        existing_rows = list(db.scalars(select(IntentProfile).where(IntentProfile.user_id.is_(None))))
        existing_by_name = {row.profile_name: row for row in existing_rows}
        changed = False
        for item in payload:
            name = str(item.get("profile_name") or "").strip().lower()
            if not name:
                continue
            tokens_json = json.dumps(item.get("token_keywords") or [], ensure_ascii=False)
            phrases_json = json.dumps(item.get("phrase_keywords") or [], ensure_ascii=False)
            aliases_json = json.dumps(item.get("aliases") or [], ensure_ascii=False)
            threshold = float(item.get("threshold") or 0.5)
            enabled = bool(item.get("enabled", True))

            row = existing_by_name.get(name)
            if row:
                if (
                    row.token_keywords_json != tokens_json
                    or row.phrase_keywords_json != phrases_json
                    or row.aliases_json != aliases_json
                    or float(row.threshold or 0.0) != threshold
                    or bool(row.enabled) != enabled
                ):
                    row.token_keywords_json = tokens_json
                    row.phrase_keywords_json = phrases_json
                    row.aliases_json = aliases_json
                    row.threshold = threshold
                    row.enabled = enabled
                    changed = True
                continue

            db.add(
                IntentProfile(
                    user_id=None,
                    profile_name=name,
                    token_keywords_json=tokens_json,
                    phrase_keywords_json=phrases_json,
                    aliases_json=aliases_json,
                    threshold=threshold,
                    enabled=enabled,
                )
            )
            changed = True
        if changed:
            db.commit()

    def get_profiles(self, user_id: int | None = None) -> list[QueryProfile]:
        global_rows = list(self.db.scalars(select(IntentProfile).where(IntentProfile.user_id.is_(None))))
        by_name: dict[str, QueryProfile] = {}
        for row in global_rows:
            profile = self._parse_row(row)
            if profile:
                by_name[profile.name] = profile

        if user_id is not None:
            user_rows = list(self.db.scalars(select(IntentProfile).where(IntentProfile.user_id == user_id)))
            for row in user_rows:
                profile = self._parse_row(row)
                if profile:
                    by_name[profile.name] = profile

        return list(by_name.values())

    def _get_row(self, profile_name: str, user_id: int | None = None) -> IntentProfile | None:
        normalized = (profile_name or "").strip().lower()
        if not normalized:
            return None
        return (
            self.db.query(IntentProfile)
            .filter(IntentProfile.profile_name == normalized, IntentProfile.user_id == user_id)
            .one_or_none()
        )

    def _materialize_user_row(self, user_id: int, profile_name: str) -> IntentProfile | None:
        normalized = (profile_name or "").strip().lower()
        if not normalized:
            return None
        row = self._get_row(normalized, user_id=user_id)
        if row:
            return row

        global_row = self._get_row(normalized, user_id=None)
        if global_row:
            row = IntentProfile(
                user_id=user_id,
                profile_name=normalized,
                token_keywords_json=global_row.token_keywords_json or "[]",
                phrase_keywords_json=global_row.phrase_keywords_json or "[]",
                aliases_json=global_row.aliases_json or "[]",
                threshold=float(global_row.threshold or 0.5),
                enabled=bool(global_row.enabled),
            )
            self.db.add(row)
            self.db.commit()
            self.db.refresh(row)
            return row
        return None

    def list_profiles(self, user_id: int | None = None) -> list[dict]:
        profiles = sorted(self.get_profiles(user_id), key=lambda p: p.name)
        rows: list[dict] = []
        for p in profiles:
            rows.append(
                {
                    "name": p.name,
                    "threshold": p.threshold,
                    "tokens_count": len(p.token_keywords),
                    "phrases_count": len(p.phrase_keywords),
                    "aliases_count": len(p.aliases),
                }
            )
        return rows

    def export_profiles(self, user_id: int | None = None) -> list[dict]:
        profiles = sorted(self.get_profiles(user_id), key=lambda p: p.name)
        return [
            {
                "name": p.name,
                "threshold": float(p.threshold),
                "enabled": True,
                "token_keywords": sorted(list(p.token_keywords)),
                "phrase_keywords": sorted(list(p.phrase_keywords)),
                "aliases": sorted(list(p.aliases)),
            }
            for p in profiles
        ]

    def replace_user_profiles(self, user_id: int, profiles: list[dict]) -> None:
        existing = list(self.db.scalars(select(IntentProfile).where(IntentProfile.user_id == user_id)))
        for row in existing:
            self.db.delete(row)
        self.db.flush()

        for item in profiles:
            name = str(item.get("name") or "").strip().lower()
            if not name:
                continue
            tokens = item.get("token_keywords") or []
            phrases = item.get("phrase_keywords") or []
            aliases = item.get("aliases") or []
            threshold = float(item.get("threshold") or 0.5)
            enabled = bool(item.get("enabled", True))
            self.db.add(
                IntentProfile(
                    user_id=user_id,
                    profile_name=name,
                    token_keywords_json=json.dumps(tokens, ensure_ascii=False),
                    phrase_keywords_json=json.dumps(phrases, ensure_ascii=False),
                    aliases_json=json.dumps(aliases, ensure_ascii=False),
                    threshold=threshold,
                    enabled=enabled,
                )
            )
        self.db.commit()

    def add_phrase(self, user_id: int, profile_name: str, phrase: str) -> bool:
        row = self._materialize_user_row(user_id=user_id, profile_name=profile_name)
        if not row:
            return False
        candidate = (phrase or "").strip().lower()
        if not candidate:
            return False
        phrases = json.loads(row.phrase_keywords_json or "[]")
        if candidate not in phrases:
            phrases.append(candidate)
            row.phrase_keywords_json = json.dumps(phrases, ensure_ascii=False)
            self.db.commit()
        return True

    def set_threshold(self, user_id: int, profile_name: str, threshold: float) -> bool:
        if threshold < 0.0 or threshold > 1.0:
            return False
        row = self._materialize_user_row(user_id=user_id, profile_name=profile_name)
        if not row:
            return False
        row.threshold = float(threshold)
        self.db.commit()
        return True
