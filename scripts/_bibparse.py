"""Minimal BibTeX parser for fwibib.

Our bib file is hand-curated and follows a uniform format
(see CONTRIBUTING.md), so we avoid the bibtexparser dependency and
parse with a small regex-based scanner. Returns entries as dicts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

def _parse_fields(body: str) -> dict[str, str]:
    """Parse a BibTeX entry body (everything between { and the matching })."""
    fields: dict[str, str] = {}
    pos = 0
    n = len(body)
    while pos < n:
        while pos < n and body[pos] in ", \t\r\n":
            pos += 1
        if pos >= n:
            break
        name_start = pos
        while pos < n and (body[pos].isalnum() or body[pos] == "_"):
            pos += 1
        name = body[name_start:pos].lower()
        if not name:
            pos += 1
            continue
        while pos < n and body[pos] in " \t\r\n":
            pos += 1
        if pos < n and body[pos] == "=":
            pos += 1
        while pos < n and body[pos] in " \t\r\n":
            pos += 1
        if pos >= n:
            break
        if body[pos] == "{":
            depth = 0
            value_start = pos
            while pos < n:
                ch = body[pos]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        pos += 1
                        break
                pos += 1
            value = body[value_start + 1 : pos - 1]
        elif body[pos] == '"':
            pos += 1
            value_start = pos
            while pos < n and body[pos] != '"':
                pos += 1
            value = body[value_start:pos]
            if pos < n:
                pos += 1
        else:
            value_start = pos
            while pos < n and body[pos] not in ",\n":
                pos += 1
            value = body[value_start:pos].strip()
        fields[name] = value.strip()
    return fields


def parse_bib(path: Path) -> list[dict[str, Any]]:
    """Parse a BibTeX file. Handles arbitrary brace nesting (LaTeX accents,
    nested macros) by tracking depth instead of relying on regex.

    Line numbers are tracked incrementally. The original implementation
    used ``text.count("\\n", 0, at_idx)`` per entry, which is O(N²)
    overall and made 37k-entry catalogs take 6+ minutes to parse.
    """
    text = path.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    cur_line = 1  # running line number, advanced past each consumed range
    while i < n:
        at_idx = text.find("@", i)
        if at_idx < 0:
            break
        # advance the running line counter across the skipped prefix
        if at_idx > i:
            cur_line += text.count("\n", i, at_idx)
        # skip @ inside a comment line
        if at_idx > 0 and text[at_idx - 1] == "%":
            i = at_idx + 1
            continue
        j = at_idx + 1
        while j < n and (text[j].isalnum() or text[j] == "_"):
            j += 1
        etype = text[at_idx + 1 : j].lower()
        if not etype:
            i = at_idx + 1
            continue
        while j < n and text[j] in " \t\r\n":
            j += 1
        if j >= n or text[j] != "{":
            i = at_idx + 1
            continue
        line_of_at = cur_line
        depth = 1
        k = j + 1
        while k < n and depth > 0:
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth != 0:
            break
        body = text[j + 1 : k]
        comma_idx = body.find(",")
        if comma_idx < 0:
            i = k + 1
            continue
        key = body[:comma_idx].strip()
        fields = _parse_fields(body[comma_idx + 1 :])
        entries.append(
            {
                "type": etype,
                "key": key,
                "fields": fields,
                "line": line_of_at,
            }
        )
        # advance line counter past the consumed entry before next find()
        cur_line += text.count("\n", at_idx, k + 1)
        i = k + 1
    return entries


_TAXONOMY_KW_RE = re.compile(r"`([a-z0-9-]+)`")
_TAXONOMY_SECTION_RE = re.compile(r"^##\s+([A-Z])\.\s", re.MULTILINE)


def parse_taxonomy(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    # Only look inside the dimensional sections (## A. ... onward), skipping
    # the intro prose, to avoid picking up backticked words used as examples.
    first_section = _TAXONOMY_SECTION_RE.search(text)
    body = text[first_section.start():] if first_section else text
    return set(_TAXONOMY_KW_RE.findall(body))


def parse_taxonomy_sections(path: Path) -> dict[str, set[str]]:
    """Return {section_letter -> set of keywords in that section}.

    Sub-headings (e.g. ### B.1, ### B.2) stay inside their parent letter,
    so all of `B.1`–`B.10` collapse into `B`.
    """
    text = path.read_text(encoding="utf-8")
    sections: dict[str, set[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        m = _TAXONOMY_SECTION_RE.match(line)
        if m:
            current = m.group(1)
            sections.setdefault(current, set())
            continue
        if current is not None:
            sections[current].update(_TAXONOMY_KW_RE.findall(line))
    return sections


def split_keywords(raw: str) -> list[str]:
    return [k.strip() for k in raw.split(",") if k.strip()]
