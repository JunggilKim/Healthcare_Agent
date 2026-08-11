from __future__ import annotations

import re
import unicodedata

TOKEN_PATTERN = re.compile(r"(?:[가-힣]+|[a-z]+(?:[-/][a-z0-9]+)*|\d+(?:\.\d+)?|[%<>≤≥]+)")
CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
STOPWORDS = frozenset(
    {"a", "an", "and", "are", "as", "at", "by", "for", "in", "of", "or", "the", "to", "with"}
)


class RegexMedicalTokenizer:
    def tokenize(self, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text)
        normalized = CAMEL_BOUNDARY.sub(" ", normalized).lower()
        return [token for token in TOKEN_PATTERN.findall(normalized) if token not in STOPWORDS]
