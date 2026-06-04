#!/usr/bin/env python3
"""Convert an OpenAlex `papers.jsonl` dump into bib/openalex_geophysics.bib.

Companion to scripts/harvest_filter.py.

Input
-----
JSONL produced by harvest_filter.py.  Each record has the OpenAlex
shape: doi, title, authors (list of strings), year, venue, concepts,
primary_topic, primary_topic_subfield, abstract_inverted_index, etc.

Output
------
bib/openalex_geophysics.bib — third independent catalog alongside
bib/seg_abstracts.bib and bib/eage_abstracts.bib, NOT merged into
the curated bib/seismic.bib.

Filter rules (defaults)
-----------------------
* primary_topic_subfield == 'Geophysics' (strict)
* venue in the hardcoded CORE_VENUES allowlist (see below)
* title length >= 20 chars (drop "reply on rc1" cruft)
* DOI present and looks like a DOI

Usage
-----
    python scripts/proc_openalex_to_bib.py \\
        --jsonl ~/Desktop/seisbib_harvest/papers_mac.jsonl \\
        --out bib/openalex_geophysics.bib
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Curated allowlist (94k expected). Cuts off topical / regional venues
# like Lithos, Precambrian Research, JGR Atmospheres, etc.
CORE_VENUES = [
    # ---- SEG family (Geophysics, The Leading Edge) is already covered by
    #      bib/seg_abstracts.bib (Crossref DOI prefix 10.1190); skip here.
    # ---- EAGE family (Geophysical Prospecting, Near Surface Geophysics,
    #      First Break) is already covered by bib/eage_abstracts.bib
    #      (Crossref DOI prefix 10.3997); skip here.
    # JGR series (solid-earth subset)
    "Journal of Geophysical Research Solid Earth",
    "Journal of Geophysical Research Space Physics",
    "Journal of Geophysical Research Planets",
    "Journal of Geophysical Research Earth Surface",
    "Journal of Geophysical Research Machine Learning and Computation",
    "Journal of Geophysical Research",
    # Top global seismology / solid earth
    "Geophysical Journal International",
    "Bulletin of the Seismological Society of America",
    "Geophysical Research Letters",
    "Earth and Planetary Science Letters",
    "Tectonophysics",
    "Geochemistry, Geophysics, Geosystems",
    "Pure and Applied Geophysics",
    "Seismological Research Letters",
    "Geology",
    # Reviews / surveys
    "Reviews of Geophysics",
    "Surveys in Geophysics",
    "Annual Review of Earth and Planetary Sciences",
    "Earth-Science Reviews",
    # High-impact general
    "Nature",
    "Science",
    "Nature Geoscience",
    "Nature Communications",
    "Proceedings of the National Academy of Sciences",
    "Scientific Reports",
    "Communications Earth & Environment",
    # Applied / computational
    "Computers & Geosciences",
    "Journal of Applied Geophysics",
    "Exploration Geophysics",
    "ASEG Extended Abstracts",
    "IEEE Transactions on Geoscience and Remote Sensing",
    # Modern open access
    "Solid Earth",
    "Frontiers in Earth Science",
    "Geoscience Frontiers",
    # Other strong solid-earth journals
    "Marine and Petroleum Geology",
    "Marine Geophysical Researches",
    "Studia Geophysica et Geodaetica",
    "Acta Geophysica",
    "Chinese Journal of Geophysics",
]
CORE_SET = {v.lower().strip() for v in CORE_VENUES}

REQUIRED_SUBFIELD = "Geophysics"
TITLE_MIN_LEN = 20

# Map OpenAlex primary_topic strings to one or more controlled TAXONOMY.md
# keywords so the merged main bib can populate by-topic views meaningfully.
# Tags are at the 14-bucket top level of TAXONOMY.md.
TOPIC_MAP: dict[str, list[str]] = {
    "earthquake and tectonic studies":                ["earthquake"],
    "Geological and Geochemical Analysis":            ["rock-physics"],
    "High-pressure geophysics and materials":         ["rock-physics"],
    "Seismic Imaging and Inversion Techniques":       ["imaging", "inversion"],
    "Geophysical and Geoelectrical Methods":          ["near-surface"],
    "Seismic Waves and Analysis":                     ["modeling"],
    "Earthquake Detection and Analysis":              ["earthquake", "microseismic"],
    "Geological and Geophysical Studies Worldwide":   ["earthquake"],
    "Geological Formations and Processes Exploration":["interpretation"],
    "Geological and Tectonic Studies in Latin America":["earthquake"],
}

# Title-keyword overrides — when a title contains any of these strings,
# append the listed taxonomy keyword(s). Catches high-value entries that
# the topic-map alone would miss (e.g. FWI / RTM / DL / DAS papers).
TITLE_OVERRIDES: list[tuple[tuple[str, ...], list[str]]] = [
    (("full waveform inversion", "full-waveform inversion", "waveform inversion", " fwi ", " fwi:"),
     ["fwi", "inversion"]),
    (("reverse time migration", " rtm ", " rtm:"),
     ["imaging"]),
    (("least squares migration", "ls-rtm", "lsrtm", "least-squares rtm"),
     ["imaging"]),
    (("deep learning", "neural network", "convolutional neural", "transformer",
      "machine learning", "self-supervised", "diffusion model"),
     ["ml"]),
    (("distributed acoustic sensing", " das ", " das:"),
     ["das"]),
    (("physics-informed neural", "physics informed neural", "pinn"),
     ["ml"]),
    (("ambient noise", "noise interferometry", "seismic interferometry"),
     ["ambient-noise"]),
    (("receiver function",), ["earthquake"]),
    (("ground penetrating radar", " gpr ", " gpr:"), ["near-surface"]),
    (("4d seismic", "time-lapse seismic", "time lapse seismic", "co2 monitor"),
     ["monitoring"]),
]


def keywords_for(rec: dict) -> list[str]:
    """Build a controlled keyword list for one OpenAlex record."""
    kws: list[str] = []
    pt = rec.get("primary_topic") or ""
    if pt in TOPIC_MAP:
        kws.extend(TOPIC_MAP[pt])
    title_l = " " + (rec.get("title") or "").lower() + " "
    for needles, tags in TITLE_OVERRIDES:
        if any(n in title_l for n in needles):
            for t in tags:
                if t not in kws:
                    kws.append(t)
    if not kws:
        # last-resort fallback — at least something so by-topic doesn't
        # silently drop the paper
        kws = ["earthquake"]
    return kws


# ---------------------------------------------------------------- helpers

_STOP = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "with",
    "to", "from", "by", "into", "via", "using", "use", "is", "are",
    "be", "as", "at", "this", "that", "but", "we", "our",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def safe_cite_key(rec: dict, used: set[str]) -> str:
    authors = rec.get("authors") or []
    first = authors[0] if authors else "anon"
    # OpenAlex returns "First Last" — pull last token as surname
    last = first.strip().split()[-1] if first else "anon"
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
    n = 1
    while True:
        c = f"{last}_{kw}{n}_{year}"
        if c not in used:
            return c
        n += 1


def render_authors(authors: list[str]) -> str:
    """OpenAlex authors are 'First M. Last' strings → 'Last, First M.'."""
    out = []
    for raw in authors:
        name = (raw or "").strip()
        if not name:
            continue
        if "," in name:
            # already 'Last, First'
            out.append(name)
            continue
        parts = name.split()
        if len(parts) == 1:
            out.append(parts[0])
        else:
            out.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
    return " and ".join(out)


def reconstruct_abstract(inv_index: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Re-assemble."""
    if not inv_index or not isinstance(inv_index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, pos_list in inv_index.items():
        if isinstance(pos_list, list):
            for p in pos_list:
                positions.append((p, word))
    if not positions:
        return ""
    positions.sort()
    return " ".join(w for _, w in positions)


def first_sentence(text: str, cap: int = 260) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    m = re.search(r"^(.{30,}?\.)\s", text)
    if m:
        return m.group(1)[:cap]
    return text[:cap]


_HTML_ENTITIES = (
    ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
    ("&#39;", "'"), ("&apos;", "'"), ("&nbsp;", " "),
)


def escape(s: str) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").replace("\r", " ").strip()
    for old, new in _HTML_ENTITIES:
        s = s.replace(old, new)
    # BibTeX brace nesting
    s = s.replace("{", "(").replace("}", ")")
    return s


def looks_like_doi(doi: str) -> bool:
    return bool(re.match(r"^10\.\d{4,9}/\S+$", doi))


# ---------------------------------------------------------------- entry

def build_entry(rec: dict, used: set[str]) -> str | None:
    doi = (rec.get("doi") or "").strip().lower()
    if not doi or not looks_like_doi(doi):
        return None
    title = escape(rec.get("title") or "")
    if not title:
        return None
    venue = (rec.get("venue") or "").strip()
    authors = render_authors(rec.get("authors") or [])
    year = rec.get("year")
    annot = first_sentence(reconstruct_abstract(rec.get("abstract_inverted_index")))

    key = safe_cite_key(rec, used)
    used.add(key)
    kws = keywords_for(rec)

    lines = [f"@article{{{key},"]
    if authors:
        lines.append(f"  author     = {{{authors}}},")
    lines.append(f"  title      = {{{title}}},")
    if venue:
        lines.append(f"  journal    = {{{escape(venue)}}},")
    if year:
        lines.append(f"  year       = {{{year}}},")
    lines.append(f"  doi        = {{{doi}}},")
    lines.append(f"  keywords   = {{{', '.join(kws)}}},")
    if annot:
        lines.append(f"  annotation = {{{escape(annot)}}},")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------- main

def load_existing_dois(*paths: Path) -> set[str]:
    """Collect DOIs from existing .bib files for cross-catalog dedup."""
    dois: set[str] = set()
    pat = re.compile(r"doi\s*=\s*\{([^}]+)\}", re.IGNORECASE)
    for p in paths:
        if not p.exists():
            continue
        with p.open("r", encoding="utf-8") as fp:
            for line in fp:
                m = pat.search(line)
                if m:
                    dois.add(m.group(1).strip().lower())
    return dois


def load_existing_keys(*paths: Path) -> set[str]:
    """Collect cite-keys from existing .bib files to avoid collisions when
    generate.py reads multiple bib files as the main bib."""
    keys: set[str] = set()
    pat = re.compile(r"^@\w+\{([^,\s]+)\s*,", re.IGNORECASE | re.MULTILINE)
    for p in paths:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for m in pat.finditer(text):
            keys.add(m.group(1).strip())
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--jsonl", type=Path,
                    default=Path.home() / "Desktop" / "seisbib_harvest" / "papers_mac.jsonl")
    ap.add_argument("--out", type=Path, default=ROOT / "bib" / "openalex_geophysics.bib")
    ap.add_argument("--since", type=int, default=None, help="min year (default: all)")
    ap.add_argument("--min-cites", type=int, default=0, help="min cited_by_count")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sort-by-cites", action="store_true")
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"input not found: {args.jsonl}", file=sys.stderr)
        return 1

    # Dedup against the curated bib + existing SEG/EAGE catalogs so we
    # never emit an OpenAlex entry whose DOI is already represented.
    existing_dois = load_existing_dois(
        ROOT / "bib" / "seismic.bib",
        ROOT / "bib" / "seg_abstracts.bib",
        ROOT / "bib" / "eage_abstracts.bib",
    )
    print(f"loaded {len(existing_dois):,} existing DOIs for cross-bib dedup",
          file=sys.stderr)
    existing_keys = load_existing_keys(
        ROOT / "bib" / "seismic.bib",
        ROOT / "bib" / "seg_abstracts.bib",
        ROOT / "bib" / "eage_abstracts.bib",
    )
    print(f"loaded {len(existing_keys):,} existing cite-keys for collision-safety",
          file=sys.stderr)

    used_keys: set[str] = set(existing_keys)  # seed → never collide
    seen_dois: set[str] = set(existing_dois)  # seed → any match is a dupe
    kept: list[dict] = []
    n_total = 0
    n_not_geophy = n_not_venue = n_short_title = n_no_doi = n_bad_doi = 0
    n_dup = n_filter = 0

    with args.jsonl.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            rec = json.loads(line)
            if (rec.get("primary_topic_subfield") or "") != REQUIRED_SUBFIELD:
                n_not_geophy += 1
                continue
            venue = (rec.get("venue") or "").strip()
            if venue.lower() not in CORE_SET:
                n_not_venue += 1
                continue
            title = rec.get("title") or ""
            if not title or len(title) < TITLE_MIN_LEN:
                n_short_title += 1
                continue
            doi = (rec.get("doi") or "").strip().lower()
            if not doi:
                n_no_doi += 1
                continue
            if not looks_like_doi(doi):
                n_bad_doi += 1
                continue
            if doi in seen_dois:
                n_dup += 1
                continue
            seen_dois.add(doi)
            if args.since and (rec.get("year") or 0) < args.since:
                n_filter += 1
                continue
            if args.min_cites and (rec.get("cited_by_count") or 0) < args.min_cites:
                n_filter += 1
                continue
            kept.append(rec)

    print(
        f"scanned: {n_total:,}\n"
        f"  rejected non-Geophysics subfield: {n_not_geophy:,}\n"
        f"  rejected non-core venue: {n_not_venue:,}\n"
        f"  rejected short title: {n_short_title:,}\n"
        f"  rejected no-DOI: {n_no_doi:,}\n"
        f"  rejected bad-DOI: {n_bad_doi:,}\n"
        f"  rejected duplicate DOI: {n_dup:,}\n"
        f"  rejected by --since/--min-cites: {n_filter:,}\n"
        f"  kept: {len(kept):,}",
        file=sys.stderr,
    )

    if args.sort_by_cites:
        kept.sort(key=lambda r: r.get("cited_by_count") or 0, reverse=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.out.open("w", encoding="utf-8") as fp:
        fp.write(
            f"% Auto-generated by scripts/proc_openalex_to_bib.py from {args.jsonl.name}\n"
            f"% Source: OpenAlex (filter=topics.subfield.id:1908 = Geophysics).\n"
            f"% Venue allowlist: {len(CORE_VENUES)} core venues. See script for list.\n"
            f"% Do not hand-edit — regenerate from JSONL instead.\n\n"
        )
        for rec in kept:
            entry = build_entry(rec, used_keys)
            if not entry:
                continue
            fp.write(entry + "\n\n")
            written += 1
            if args.limit and written >= args.limit:
                break
    print(f"wrote {written:,} entries -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
