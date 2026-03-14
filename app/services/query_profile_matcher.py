from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from dataclasses import dataclass


@dataclass(frozen=True)
class QueryProfile:
    name: str
    token_keywords: frozenset[str]
    phrase_keywords: frozenset[str]
    aliases: frozenset[str]
    threshold: float = 0.55


def _normalize_tokens(text: str) -> list[str]:
    lowered = re.sub(r"[^a-zA-Zа-яА-Я0-9\s]", " ", (text or "").lower())
    tokens = [t.strip() for t in lowered.split() if len(t.strip()) >= 2]
    stop_words = {
        "что",
        "как",
        "это",
        "мне",
        "мои",
        "мой",
        "у",
        "на",
        "и",
        "в",
        "по",
        "the",
        "and",
        "to",
        "of",
    }
    return [t for t in tokens if t not in stop_words]


def _char_ngrams(text: str, n: int = 3) -> list[str]:
    compact = re.sub(r"\s+", " ", (text or "").lower()).strip()
    if len(compact) < n:
        return [compact] if compact else []
    return [compact[i : i + n] for i in range(0, len(compact) - n + 1)]


@lru_cache(maxsize=8192)
def _hash64(text: str) -> int:
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, byteorder="big", signed=False)


def _to_hash_set(items: list[str]) -> set[int]:
    return {_hash64(item) for item in items if item}


def _weighted_jaccard(a: set[int], b: set[int], w_intersection: float = 1.0, w_union: float = 1.0) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return (w_intersection * inter) / max(1.0, w_union * union)


class QueryProfileMatcher:
    """
    Lightweight deterministic matcher for query intent profiles.
    Uses hash-set token overlap + phrase boosts.
    """

    def _index_profiles(self, profiles: list[QueryProfile]) -> tuple[dict[str, QueryProfile], dict[str, set[int]], dict[str, set[int]]]:
        profile_index = {p.name: p for p in profiles}
        token_hashes = {p.name: _to_hash_set(list(p.token_keywords) + list(p.aliases)) for p in profiles}
        phrase_hashes = {p.name: _to_hash_set(list(p.phrase_keywords)) for p in profiles}
        return profile_index, token_hashes, phrase_hashes

    def score(self, text: str, profile_name: str, profiles: list[QueryProfile]) -> float:
        profile_index, profile_token_hashes, profile_phrase_hashes = self._index_profiles(profiles)
        profile = profile_index.get(profile_name)
        if not profile:
            return 0.0
        lower = (text or "").lower().strip()
        if lower in profile.aliases:
            return 1.0

        token_hashes = _to_hash_set(_normalize_tokens(lower))
        ngram_hashes = _to_hash_set(_char_ngrams(lower, n=3))
        profile_tokens = profile_token_hashes[profile_name]
        profile_phrases = profile_phrase_hashes[profile_name]

        token_score = _weighted_jaccard(token_hashes, profile_tokens, w_intersection=1.0, w_union=0.9)
        phrase_score = _weighted_jaccard(ngram_hashes, profile_phrases, w_intersection=1.2, w_union=1.0)
        literal_bonus = 0.0
        if any(phrase in lower for phrase in profile.phrase_keywords):
            literal_bonus = 0.25
        return min(1.0, token_score * 0.65 + phrase_score * 0.35 + literal_bonus)

    def matches(self, text: str, profile_name: str, profiles: list[QueryProfile]) -> bool:
        profile_index, _token_hashes, _phrase_hashes = self._index_profiles(profiles)
        profile = profile_index.get(profile_name)
        if not profile:
            return False
        return self.score(text, profile_name, profiles) >= profile.threshold

    def classify(self, text: str, profiles: list[QueryProfile], candidates: tuple[str, ...] | None = None) -> tuple[str | None, float]:
        profile_index, _token_hashes, _phrase_hashes = self._index_profiles(profiles)
        names = candidates or tuple(profile_index.keys())
        best_name = None
        best_score = 0.0
        for name in names:
            profile = profile_index.get(name)
            if not profile:
                continue
            current = self.score(text, name, profiles)
            if current > best_score and current >= profile.threshold:
                best_score = current
                best_name = name
        return best_name, best_score
