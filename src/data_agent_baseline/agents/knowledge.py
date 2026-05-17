from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any

DEFAULT_CHUNK_MAX_CHARS = 1600
PREVIEW_CHARS = 220

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_QUOTED_VALUE_RE = re.compile(r"['\"]([^'\"]{2,80})['\"]")

_TAG_KEYWORDS = {
    "entity": ["core entities", "fields", "business entities", "attributes"],
    "metric": ["metric definitions", "kpi", "key performance indicators"],
    "formula": ["formula", "calculation logic", "calculated as", "ratio", "percentage"],
    "filter": ["filtering criteria", "common filters", "where", "threshold"],
    "temporal": ["temporal", "date format", "fiscal year", "season", "yyyymm", "yyyy"],
    "unit": ["unit conversion", "unit", "milliseconds", "time metrics", "currency"],
    "ambiguity": ["ambiguity", "ambiguous", "similar fields", "recommended usage"],
    "example": ["exemplar", "example", "use case", "sql example"],
}

_PLAN_TAG_BOOST = {
    "formula": 3.5,
    "metric": 3.0,
    "filter": 2.8,
    "temporal": 2.4,
    "unit": 2.2,
    "ambiguity": 2.4,
    "example": 1.6,
    "entity": 0.8,
    "general": 0.0,
}

_SQL_TAG_BOOST = {
    "formula": 3.2,
    "filter": 2.8,
    "temporal": 2.6,
    "unit": 2.4,
    "ambiguity": 2.6,
    "example": 0.8,
    "metric": 1.4,
    "entity": 0.4,
    "general": 0.0,
}

_SQL_RELEVANT_TAGS = {"formula", "filter", "temporal", "unit", "ambiguity"}
_COMMON_QUERY_TOKENS = {
    "list",
    "show",
    "give",
    "provide",
    "state",
    "identify",
    "calculate",
    "what",
    "which",
    "who",
    "how",
    "many",
    "much",
    "all",
    "their",
    "with",
    "for",
    "the",
    "and",
    "patient",
    "patients",
    "member",
    "members",
    "id",
    "name",
    "names",
    "date",
    "type",
    "value",
    "values",
    "disease",
    "diagnosed",
}


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    chunk_id: str
    heading_path: tuple[str, ...]
    tag: str
    text: str
    keywords: tuple[str, ...]
    score: float = 0.0

    @property
    def heading(self) -> str:
        return " > ".join(self.heading_path) if self.heading_path else "(root)"


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text)]


def _shorten(text: str, max_chars: int) -> str:
    normalized = " ".join(text.strip().split())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3]}..."


def _slug(value: str) -> str:
    normalized = "_".join(_tokens(value))
    return normalized[:80] or "chunk"


def _classify_chunk(heading_path: tuple[str, ...], text: str) -> str:
    heading_text = " ".join(heading_path).lower()
    last_heading = heading_path[-1].lower() if heading_path else ""
    if "introduction" in heading_text:
        return "general"
    if len(heading_path) == 1 and (
        "knowledge guide" in last_heading or "data governance" in last_heading
    ):
        return "general"

    scoped_headings = heading_path[1:] if len(heading_path) > 1 else heading_path
    combined = f"{' '.join(scoped_headings)}\n{text}".lower()
    best_tag = "general"
    best_score = 0
    for tag, markers in _TAG_KEYWORDS.items():
        score = sum(1 for marker in markers if marker in combined)
        if score > best_score:
            best_tag = tag
            best_score = score
    return best_tag


def _keyword_tuple(text: str, *, max_keywords: int = 36) -> tuple[str, ...]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "where",
        "when",
        "into",
        "using",
        "use",
        "are",
        "has",
        "have",
        "all",
        "its",
        "not",
        "can",
        "each",
        "such",
        "data",
        "field",
        "fields",
    }
    counts = Counter(token for token in _tokens(text) if len(token) > 2 and token not in stop_words)
    return tuple(token for token, _ in counts.most_common(max_keywords))


def parse_knowledge_markdown(
    text: str,
    *,
    chunk_max_chars: int = DEFAULT_CHUNK_MAX_CHARS,
) -> list[KnowledgeChunk]:
    if not text.strip():
        return []

    chunks: list[KnowledgeChunk] = []
    heading_stack: list[tuple[int, str]] = []
    current_heading_path: tuple[str, ...] = ("root",)
    current_lines: list[str] = []
    chunk_index = 1

    def flush() -> None:
        nonlocal chunk_index, current_lines
        body = "\n".join(current_lines).strip()
        if not body:
            current_lines = []
            return

        clipped_body = _shorten(body, max(chunk_max_chars, 200))
        tag = _classify_chunk(current_heading_path, clipped_body)
        heading_slug = _slug(current_heading_path[-1] if current_heading_path else "root")
        chunk_id = f"knowledge:{chunk_index:03d}:{tag}:{heading_slug}"
        scoped_headings = current_heading_path[1:] if len(current_heading_path) > 1 else current_heading_path
        chunks.append(
            KnowledgeChunk(
                chunk_id=chunk_id,
                heading_path=current_heading_path,
                tag=tag,
                text=clipped_body,
                keywords=_keyword_tuple(f"{' '.join(scoped_headings)} {clipped_body}"),
            )
        )
        chunk_index += 1
        current_lines = []

    for line in text.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match is not None:
            flush()
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))
            current_heading_path = tuple(title for _, title in heading_stack)
            current_lines.append(line)
        else:
            current_lines.append(line)
    flush()
    return chunks


def _quoted_values(text: str) -> set[str]:
    return {match.group(1).strip().lower() for match in _QUOTED_VALUE_RE.finditer(text)}


def _score_chunk(
    chunk: KnowledgeChunk,
    *,
    query_tokens: set[str],
    quoted_values: set[str],
    schema_terms: set[str],
    catalog_terms: set[str],
    mode: str,
) -> float:
    chunk_tokens = set(chunk.keywords)
    if not chunk_tokens:
        chunk_tokens = set(_tokens(f"{chunk.heading} {chunk.text}"))

    significant_query_tokens = {
        token for token in query_tokens if len(token) > 3 and token not in _COMMON_QUERY_TOKENS
    }

    score = 0.0
    schema_weight = 0.1 if chunk.tag in {"entity", "example"} else 0.35
    catalog_weight = 0.15 if chunk.tag in {"entity", "example"} else 0.55

    score += len(query_tokens & chunk_tokens) * 1.0
    score += len(schema_terms & chunk_tokens) * schema_weight
    score += len(catalog_terms & chunk_tokens) * catalog_weight

    lowered_text = chunk.text.lower()
    for value in quoted_values:
        if value and value in lowered_text:
            score += 4.0

    boost_table = _SQL_TAG_BOOST if mode == "sql" else _PLAN_TAG_BOOST
    score += boost_table.get(chunk.tag, 0.0)

    if chunk.tag == "general" and not (query_tokens & chunk_tokens):
        score -= 2.0
    if chunk.tag == "example" and significant_query_tokens:
        if not (significant_query_tokens & chunk_tokens):
            score -= 5.0
    return score


def retrieve_knowledge_chunks(
    chunks: list[KnowledgeChunk],
    *,
    query: str,
    schema_terms: list[str] | None = None,
    catalog_terms: list[str] | None = None,
    top_k: int = 6,
    mode: str = "plan",
) -> list[KnowledgeChunk]:
    if top_k <= 0 or not chunks:
        return []

    query_tokens = set(_tokens(query))
    quoted_values = _quoted_values(query)
    schema_token_set = set(_tokens(" ".join(schema_terms or [])))
    catalog_token_set = set(_tokens(" ".join(catalog_terms or [])))

    scored_chunks = [
        replace(
            chunk,
            score=_score_chunk(
                chunk,
                query_tokens=query_tokens,
                quoted_values=quoted_values,
                schema_terms=schema_token_set,
                catalog_terms=catalog_token_set,
                mode=mode,
            ),
        )
        for chunk in chunks
    ]
    scored_chunks.sort(key=lambda item: (item.score, item.tag != "general"), reverse=True)
    candidates = [chunk for chunk in scored_chunks if chunk.score > 0]

    selected: list[KnowledgeChunk] = []
    skipped: list[KnowledgeChunk] = []
    max_examples = 1 if mode == "sql" else 2
    example_count = 0
    general_count = 0
    for chunk in candidates:
        if chunk.tag == "example" and example_count >= max_examples:
            skipped.append(chunk)
            continue
        if chunk.tag == "general" and general_count >= 1:
            skipped.append(chunk)
            continue
        selected.append(chunk)
        if chunk.tag == "example":
            example_count += 1
        if chunk.tag == "general":
            general_count += 1
        if len(selected) >= top_k:
            return selected

    for chunk in skipped:
        selected.append(chunk)
        if len(selected) >= top_k:
            break
    return selected


def knowledge_chunk_summary(
    chunk: KnowledgeChunk,
    *,
    preview_chars: int = PREVIEW_CHARS,
) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "tag": chunk.tag,
        "heading": chunk.heading,
        "score": round(chunk.score, 3),
        "preview": _shorten(chunk.text, preview_chars),
    }


def knowledge_corpus_summary(chunks: list[KnowledgeChunk]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": chunk.chunk_id,
            "tag": chunk.tag,
            "heading": chunk.heading,
            "char_count": len(chunk.text),
        }
        for chunk in chunks
    ]


def render_retrieved_knowledge(chunks: list[KnowledgeChunk], *, max_chars: int = 4200) -> str:
    if not chunks:
        return "(none)"

    sections = [
        "Use these context/knowledge.md excerpts as semantic guidance only. "
        "Observed DataEngine schema remains authoritative for table and column names."
    ]
    for index, chunk in enumerate(chunks, start=1):
        sections.append(
            f"[K{index}] tag={chunk.tag}; heading={chunk.heading}; score={chunk.score:.2f}\n"
            f"{chunk.text}"
        )

    rendered = "\n\n".join(sections)
    return _shorten(rendered, max_chars)


def build_sql_knowledge_constraints(
    chunks: list[KnowledgeChunk],
    *,
    top_k: int = 3,
    max_chars: int = 900,
) -> str:
    relevant = [chunk for chunk in chunks if chunk.tag in _SQL_RELEVANT_TAGS]
    relevant.sort(key=lambda item: item.score, reverse=True)
    selected = relevant[: max(top_k, 0)]
    if not selected:
        return "(none)"

    lines = [
        "Use these compact constraints only as guardrails while translating the plan. "
        "Do not change the plan intent and do not use non-schema table or column names "
        "from knowledge."
    ]
    for chunk in selected:
        lines.append(f"- [{chunk.tag}] {chunk.heading}: {_shorten(chunk.text, 320)}")
    return _shorten("\n".join(lines), max_chars)
