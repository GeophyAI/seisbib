#!/usr/bin/env python3
"""Validate bib/seismic.bib against the conventions in CONTRIBUTING.md.

Checks:
  - Required fields are present and non-empty
  - DOI is unique across the library (case-insensitive)
  - cite-key is unique and matches firstauthor_keyword_year
  - cite-key year matches the year field
  - Every keyword appears in TAXONOMY.md
  - Each entry carries >=1 keyword from TAXONOMY section A (Topic)

Exits with status 1 (and prints line numbers) on any failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from _bibparse import (
    parse_bib,
    parse_taxonomy,
    parse_taxonomy_sections,
    split_keywords,
)

ROOT = Path(__file__).resolve().parent.parent
BIB_FILE = ROOT / "bib" / "seismic.bib"
TAXONOMY_FILE = ROOT / "TAXONOMY.md"

REQUIRED_FIELDS = ("author", "title", "year", "keywords")
VENUE_FIELDS = ("journal", "booktitle")
LINK_FIELDS = ("doi", "url")
DOI_UNVERIFIED_MARKER = "doi unverified"  # note = {DOI unverified...}

CITE_KEY_RE = re.compile(r"^[a-z][a-z0-9]*_[a-z0-9]+_(\d{4})$")


def main() -> int:
    if not BIB_FILE.exists():
        print(f"error: {BIB_FILE} not found", file=sys.stderr)
        return 1
    if not TAXONOMY_FILE.exists():
        print(f"error: {TAXONOMY_FILE} not found", file=sys.stderr)
        return 1

    entries = parse_bib(BIB_FILE)
    allowed_keywords = parse_taxonomy(TAXONOMY_FILE)
    topic_keywords = parse_taxonomy_sections(TAXONOMY_FILE).get("A", set())

    errors: list[str] = []
    warnings: list[str] = []
    seen_keys: dict[str, int] = {}
    seen_dois: dict[str, int] = {}

    for entry in entries:
        key = entry["key"]
        line = entry["line"]
        fields = entry["fields"]
        loc = f"{BIB_FILE.name}:{line} ({key})"

        for f in REQUIRED_FIELDS:
            if not fields.get(f):
                errors.append(f"{loc}: missing required field `{f}`")

        # Venue is only required for @article / @inproceedings.
        # @book uses publisher; @misc uses howpublished/url; @techreport etc.
        if entry["type"] in ("article", "inproceedings", "conference"):
            if not any(fields.get(f) for f in VENUE_FIELDS):
                errors.append(f"{loc}: needs `journal` or `booktitle`")

        if not any(fields.get(f) for f in LINK_FIELDS):
            note = fields.get("note", "").lower()
            if DOI_UNVERIFIED_MARKER in note:
                warnings.append(f"{loc}: no doi/url (marked unverified)")
            else:
                errors.append(f"{loc}: needs `doi` or `url`")

        m = CITE_KEY_RE.match(key)
        if not m:
            errors.append(
                f"{loc}: cite-key `{key}` does not match "
                f"firstauthor_keyword_year (e.g. he_reparameterized_2021)"
            )
        elif fields.get("year") and m.group(1) != fields["year"]:
            errors.append(
                f"{loc}: cite-key year {m.group(1)} != year field {fields['year']}"
            )

        if key in seen_keys:
            errors.append(
                f"{loc}: duplicate cite-key (first seen at line {seen_keys[key]})"
            )
        else:
            seen_keys[key] = line

        doi = fields.get("doi", "").lower()
        if doi:
            if doi in seen_dois:
                errors.append(
                    f"{loc}: duplicate DOI `{doi}` "
                    f"(first seen at line {seen_dois[doi]})"
                )
            else:
                seen_dois[doi] = line

        raw_keywords = fields.get("keywords", "")
        entry_kws = split_keywords(raw_keywords)
        for kw in entry_kws:
            if kw not in allowed_keywords:
                errors.append(
                    f"{loc}: keyword `{kw}` not in TAXONOMY.md "
                    f"(add it there first, or fix the spelling)"
                )
        if entry_kws and not (set(entry_kws) & topic_keywords):
            errors.append(
                f"{loc}: needs >=1 Topic keyword from TAXONOMY section A "
                f"(e.g. inversion, modeling, imaging, processing, ...)"
            )

    print(f"checked {len(entries)} entries in {BIB_FILE.name}")
    if warnings:
        print(f"\n{len(warnings)} warning(s) (entries with unverified DOI):")
        for w in warnings:
            print(f"  ~ {w}")
    if errors:
        print(f"\nfound {len(errors)} problem(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1
    if warnings:
        print("ok (with warnings)")
    else:
        print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
