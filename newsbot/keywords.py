"""Broad candidate filtering; model relevance assessment comes afterward."""

import re
import unicodedata


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


class KeywordMatcher:
    def __init__(self, groups: dict[str, list[str]]):
        self._patterns = {}
        for key, synonyms in groups.items():
            terms = {_normalize(term) for term in [key, *synonyms] if term.strip()}
            if not terms:
                raise ValueError("Keyword groups must not be empty")
            self._patterns[key] = re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(term) for term in sorted(terms)) + r")(?!\w)"
            )

    def match(self, text: str) -> tuple[str, ...]:
        normalized = _normalize(text)
        return tuple(key for key, pattern in self._patterns.items() if pattern.search(normalized))
