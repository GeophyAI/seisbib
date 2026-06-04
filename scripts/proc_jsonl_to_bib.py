#!/usr/bin/env python3
"""Convert a Crossref proceedings JSONL into an independent BibTeX catalog.

Companion to `harvest_seg.py` / `harvest_eage.py`. Produces an
independent `.bib` (e.g. `bib/seg_abstracts.bib`, `bib/eage_abstracts.bib`)
that is **separate from** the curated `bib/seismic.bib`:

* No dedup against `bib/seismic.bib` (these conference catalogs are
  intentionally distinct from the curated journal-paper bib).
* Dedup happens within the produced catalog only.
* Every entry is emitted as `@inproceedings`.
* `keywords` defaults to a tiny conference-specific tag so the
  generator can group / filter them; they intentionally do not need
  to match `TAXONOMY.md`.
* `annotation` is set to the first sentence of the Crossref abstract
  (most SEG/EAGE entries have none, but a fraction do).

Usage
-----
    python scripts/proc_jsonl_to_bib.py \\
        --jsonl ~/Desktop/seisbib_harvest/seg_papers.jsonl \\
        --source seg \\
        --out bib/seg_abstracts.bib

    python scripts/proc_jsonl_to_bib.py \\
        --jsonl ~/Desktop/seisbib_harvest/eage_papers.jsonl \\
        --source eage \\
        --out bib/eage_abstracts.bib

Flags
-----
    --since YEAR        skip entries before YEAR (default: all)
    --min-cites N       only keep entries with >= N citations (default: 0)
    --limit N           cap output (default: no cap)
    --sort-by-cites     emit most-cited first
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# source-specific config: default venue (when the Crossref record's
# container-title is empty), default keywords, and a hint string used
# in cite-keys when the auto-generated keyword is too generic.
SOURCE_CONFIG: dict[str, dict[str, object]] = {
    "seg": {
        "default_venue": "SEG Technical Program Expanded Abstracts",
        "default_keywords": ["seg", "expanded-abstract"],
        "default_out": ROOT / "bib" / "seg_abstracts.bib",
        "default_jsonl": Path("~/Desktop/seisbib_harvest/seg_papers.jsonl").expanduser(),
    },
    "eage": {
        "default_venue": "EAGE Conference Proceedings",
        "default_keywords": ["eage", "expanded-abstract"],
        "default_out": ROOT / "bib" / "eage_abstracts.bib",
        "default_jsonl": Path("~/Desktop/seisbib_harvest/eage_papers.jsonl").expanduser(),
    },
}


_STOP = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "with",
    "to", "from", "by", "into", "via", "using", "use", "is", "are",
    "be", "as", "at", "this", "that",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_for_cmp(s: str) -> str:
    """Normalise a title string for deduplication comparison.

    Collapses different dash variants (em/en/hyphen) and whitespace so
    that e.g. ``event.name`` (em dash) and ``container-title`` (en dash)
    from Crossref compare as equal.
    """
    # Replace em/en dashes and multiple spaces with a regular hyphen/space
    s = re.sub(r"[–—―]", "-", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip().lower()


def safe_cite_key(rec: dict, used: set[str]) -> str:
    """Build firstauthorlastname_kw_year, suffix on collision."""
    authors = rec.get("authors") or []
    first = authors[0] if authors else "anon"
    last = first.split(",")[0] if "," in first else first.split()[-1] if first else "anon"
    last = _slug(last) or "anon"
    title = (rec.get("title") or "x").lower()
    title_toks = [_slug(t) for t in title.split()]
    kw = next((t for t in title_toks if t and t not in _STOP and len(t) > 2), "x")
    kw = kw[:18] or "x"
    year = str(rec.get("year") or "0000")
    base = f"{last}_{kw}_{year}"
    if base not in used:
        return base
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        c = f"{last}_{kw}{suffix}_{year}"
        if c not in used:
            return c
    # last resort: append numeric suffix
    n = 1
    while True:
        c = f"{last}_{kw}{n}_{year}"
        if c not in used:
            return c
        n += 1


def render_authors(authors: list[str]) -> str:
    out = []
    for name in authors:
        if "," in name:
            out.append(name.strip())
        else:
            parts = name.strip().split()
            if len(parts) == 1:
                out.append(parts[0])
            else:
                out.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
    return " and ".join(out)


_HTML_ENTITIES = (
    ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
    ("&#39;", "'"), ("&apos;", "'"), ("&nbsp;", " "),
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lang import looks_english  # noqa: E402


def escape(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ").strip()
    for old, new in _HTML_ENTITIES:
        s = s.replace(old, new)
    # Crossref data occasionally contains literal `{` / `}` (typos,
    # mojibake, OCR errors). They break BibTeX brace nesting, so strip
    # them — we don't use LaTeX-style protection groups here.
    s = s.replace("{", "(").replace("}", ")")
    # Also collapse backslashes that aren't part of TeX commands we want
    # to keep — Crossref payloads have stray `\` from broken encoding.
    return s


def first_sentence(text: str, cap: int = 260) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    # cut at the first '.' that follows >= 30 chars, else hard-truncate.
    m = re.search(r"^(.{30,}?\.)\s", text)
    if m:
        return m.group(1)[:cap]
    return text[:cap]


def build_entry(rec: dict, cfg: dict, used: set[str]) -> str | None:
    doi = (rec.get("doi") or "").strip().lower()
    if not doi:
        return None
    key = safe_cite_key(rec, used)
    used.add(key)
    # Prefer the most-specific conference designation as `booktitle`.
    # Crossref's `event.name` (e.g. "Fifth International Meeting for
    # Applied Geoscience & Energy") is more descriptive than the
    # generic series name in `container-title[0]`. Fall back to:
    #   container-title[1] (Crossref sometimes lists [series, specific])
    #   container-title[0]
    #   default_venue
    containers = rec.get("container_titles") or []
    # Unescape HTML entities in container titles (Crossref JSON can contain
    # raw XML-escaped content, e.g. "&amp;" instead of "&").
    containers = [escape(c) for c in containers if c]
    event_name = (rec.get("event_name") or "").strip()
    if event_name:
        booktitle = event_name
    elif len(containers) > 1 and containers[1]:
        booktitle = containers[1]
    elif containers and containers[0]:
        booktitle = containers[0]
    else:
        booktitle = (rec.get("venue") or "").strip() or cfg["default_venue"]
    # `series` is the umbrella name (the parent series) when it differs
    # from booktitle — e.g. SEG IMAGE keeps "International Meeting…"
    # as the series and the "Fifth…" variant as the specific event.
    # Compare normalised forms so that "&amp;" vs "&", em vs en dashes,
    # etc. don't prevent dedup when the conference name is effectively the same.
    series = containers[0].strip() if containers else ""
    if _norm_for_cmp(series) in (_norm_for_cmp(booktitle), _norm_for_cmp(event_name)):
        series = ""
    publisher = (rec.get("publisher") or "").strip()
    # Strip trailing punctuation from Crossref event.location
    # (Crossref occasionally emits "City, Country," with a trailing comma).
    event_location = (rec.get("event_location") or "").strip().rstrip(",")\
        .strip()
    fp = rec.get("first_page") or ""
    lp = rec.get("last_page") or ""
    pages = f"{fp}--{lp}" if fp and lp else (fp or "")
    annot = first_sentence(rec.get("abstract") or "")

    lines = [f"@inproceedings{{{key},"]
    lines.append(f"  author     = {{{render_authors(rec.get('authors') or [])}}},")
    lines.append(f"  title      = {{{escape(rec.get('title') or 'Untitled')}}},")
    lines.append(f"  booktitle  = {{{escape(booktitle)}}},")
    if series:
        lines.append(f"  series     = {{{escape(series)}}},")
    if rec.get("year"):
        lines.append(f"  year       = {{{rec['year']}}},")
    if rec.get("volume"):
        lines.append(f"  volume     = {{{rec['volume']}}},")
    if rec.get("issue"):
        lines.append(f"  number     = {{{rec['issue']}}},")
    if pages:
        lines.append(f"  pages      = {{{pages}}},")
    if publisher:
        lines.append(f"  publisher  = {{{escape(publisher)}}},")
    if event_location:
        lines.append(f"  address    = {{{escape(event_location)}}},")
    lines.append(f"  doi        = {{{doi}}},")
    lines.append(f"  keywords   = {{{', '.join(cfg['default_keywords'])}}},")
    if annot:
        lines.append(f"  annotation = {{{escape(annot)}}},")
    lines.append("}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=sorted(SOURCE_CONFIG.keys()), required=True,
                    help="conference family (drives default venue/keywords/paths)")
    ap.add_argument("--jsonl", type=Path, default=None,
                    help="input JSONL (default: source-specific)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output .bib (default: source-specific)")
    ap.add_argument("--since", type=int, default=None, help="min year")
    ap.add_argument("--min-cites", type=int, default=0,
                    help="min cited_by_count")
    ap.add_argument("--limit", type=int, default=0, help="cap output (0 = no cap)")
    ap.add_argument("--sort-by-cites", action="store_true",
                    help="emit most-cited first")
    ap.add_argument("--english-only", action=argparse.BooleanOptionalAction,
                    default=False,
                    help="drop entries whose title is not English "
                         "(default: off — keep everything; the website "
                         "has a UI toggle that does the same filtering "
                         "client-side without losing data)")
    args = ap.parse_args()

    cfg = SOURCE_CONFIG[args.source]
    jsonl_path = args.jsonl or cfg["default_jsonl"]
    out_path = args.out or cfg["default_out"]
    if not jsonl_path.exists():
        print(f"input not found: {jsonl_path}", file=sys.stderr)
        return 1

    used_keys: set[str] = set()
    seen_dois: set[str] = set()
    kept: list[dict] = []
    skipped_dup = skipped_filter = skipped_nodoi = skipped_non_en = 0

    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            doi = (rec.get("doi") or "").strip().lower()
            if not doi:
                skipped_nodoi += 1
                continue
            if doi in seen_dois:
                skipped_dup += 1
                continue
            seen_dois.add(doi)
            if args.since and (rec.get("year") or 0) < args.since:
                skipped_filter += 1
                continue
            if args.min_cites and (rec.get("cited_by_count") or 0) < args.min_cites:
                skipped_filter += 1
                continue
            if args.english_only and not looks_english(rec.get("title") or ""):
                skipped_non_en += 1
                continue
            kept.append(rec)

    if args.sort_by_cites:
        kept.sort(key=lambda r: r.get("cited_by_count") or 0, reverse=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with out_path.open("w", encoding="utf-8") as fp:
        fp.write(
            f"% Auto-generated by scripts/proc_jsonl_to_bib.py from {jsonl_path.name}\n"
            f"% Source: {args.source.upper()} proceedings (Crossref).\n"
            f"% Do not hand-edit — regenerate from JSONL instead.\n\n"
        )
        for rec in kept:
            entry = build_entry(rec, cfg, used_keys)
            if not entry:
                continue
            fp.write(entry + "\n\n")
            written += 1
            if args.limit and written >= args.limit:
                break

    print(
        f"wrote {written} {args.source.upper()} entries -> {out_path}\n"
        f"  source JSONL: {jsonl_path} ({len(kept)} kept after filters)\n"
        f"  skipped {skipped_dup} dupes, {skipped_filter} by year/cites, "
        f"{skipped_nodoi} no-DOI, {skipped_non_en} non-English "
        f"({'english-only=on' if args.english_only else 'english-only=off'})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
