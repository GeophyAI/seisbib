#!/usr/bin/env python3
"""Convert harvested OpenAlex JSONL into candidate BibTeX entries for seisbib.

Each output entry is a *candidate* — keywords are auto-suggested from the
record's OpenAlex `concepts` field mapped to seisbib's controlled
vocabulary. **You still need to review** each entry (esp. keywords) before
appending to `bib/seismic.bib`.

Usage
-----
    # convert everything new since last run:
    python scripts/jsonl_to_bib.py

    # filter by year:
    python scripts/jsonl_to_bib.py --since 2024

    # only papers with >=N citations (drops obscure / preprint noise):
    python scripts/jsonl_to_bib.py --min-cites 5

    # output to a specific staging file (default: /tmp/seisbib_candidates.bib):
    python scripts/jsonl_to_bib.py --out /tmp/my_review.bib

Output
------
- `/tmp/seisbib_candidates.bib` (or `--out`): candidate BibTeX block.
- Skips any DOI already present in `bib/seismic.bib`.
- Prints a per-entry decision log so you can spot issues.

Review workflow
---------------
1. Run this; open the candidate .bib in an editor.
2. Inspect each entry, fix keywords (auto-suggested ones are coarse).
3. Drop entries you don't want.
4. Append the kept entries to `bib/seismic.bib`.
5. `python scripts/validate.py` — fix any complaints.
6. `python scripts/generate.py && mkdocs build` to refresh site.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bibparse import parse_bib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HARVEST = Path("~/Desktop/seisbib_harvest/papers.jsonl").expanduser()
DEFAULT_OUT = Path("/tmp/seisbib_candidates.bib")
SEISMIC_BIB = ROOT / "bib" / "seismic.bib"

# ----------------------- OpenAlex concept -> seisbib keyword mapping ---------
# Mapped against TAXONOMY.md. Multiple hits accumulate; we prepend a Topic.

CONCEPT_MAP: dict[str, list[str]] = {
    # Topic (workflow stage)
    "Seismology": ["earthquake"],
    "Seismic wave": ["modeling"],
    "Geophysics": [],
    "Inverse problem": ["inversion", "theory"],
    "Full waveform inversion": ["inversion", "fwi"],
    "Reverse time migration": ["imaging", "rtm"],
    "Seismic migration": ["imaging"],
    "Migration (geology)": ["imaging"],
    "Tomography": ["inversion", "traveltime-tomography"],
    "Seismic tomography": ["earthquake", "traveltime-tomography"],
    "Seismic noise": ["ambient-noise", "noise-interferometry"],
    "Receiver function": ["earthquake", "receiver-function"],
    "Microseismicity": ["microseismic", "event-detection"],
    "Induced seismicity": ["microseismic", "monitoring"],
    "Earthquake source": ["earthquake", "source-mechanism"],
    "Earthquake prediction": ["earthquake"],
    "Volcano seismology": ["monitoring", "volcanology"],
    "Glaciology": ["monitoring", "glaciology"],
    "Seismometer": ["acquisition"],
    "Ground-penetrating radar": ["near-surface", "gpr"],
    "Rock physics": ["rock-physics"],
    "Reservoir characterization": ["interpretation"],
    "Reservoir simulation": ["monitoring"],
    "Reservoir engineering": ["monitoring"],
    "Anisotropy": ["modeling", "anisotropic-vti"],
    "Attenuation": ["modeling", "viscoelastic"],
    "Distributed acoustic sensing": ["acquisition", "das"],
    # Method (specific)
    "Finite difference": ["finite-difference"],
    "Spectral element method": ["spectral-element"],
    "Pseudo-spectral method": ["pseudospectral"],
    "Boundary element method": ["boundary-element"],
    "Wave equation": ["modeling"],
    "Acoustic wave equation": ["acoustic"],
    "Elastic-wave equation": ["elastic"],
    "Adjoint state method": ["adjoint-tomography"],
    "Marchenko equation": ["imaging"],
    "Deconvolution": ["processing", "deconvolution"],
    "Denoising": ["processing", "denoising"],
    "Surface wave": ["earthquake", "surface-wave-tomography"],
    "Surface-related multiple elimination": ["processing", "srme"],
    # ML
    "Convolutional neural network": ["ml", "cnn"],
    "Deep learning": ["ml"],
    "Generative adversarial network": ["ml", "gan"],
    "Transformer (machine learning model)": ["ml", "transformer"],
    "Diffusion model": ["ml", "diffusion-model"],
    "Physics-informed neural network": ["ml", "pinn", "physics-informed"],
    "Foundation model": ["ml", "foundation-model"],
    # Physics
    "Viscoelasticity": ["viscoelastic"],
    "Plane wave": ["theory"],
    # Acquisition / data
    "Earth science": [],
    "Mineralogy": [],
    "Geochemistry": [],
}

GENERIC_KEYWORDS = {"theory", "review"}  # safe fallbacks


def safe_cite_key(record: dict, used: set[str]) -> str:
    """Build firstauthorlastname_keyword_year. Append a/b/c on collisions."""
    authors = record.get("authors") or []
    first = authors[0] if authors else "anon"
    # "Last, First" or "First Last"
    if "," in first:
        last = first.split(",")[0]
    else:
        last = first.split()[-1] if first else "anon"
    last = re.sub(r"[^A-Za-z]", "", last).lower() or "anon"
    title = (record.get("title") or "x").lower()
    # first non-stopword token
    stop = {
        "a", "an", "the", "of", "for", "and", "or", "in", "on", "with",
        "to", "from", "by", "into", "via", "using", "use",
    }
    title_toks = [re.sub(r"[^a-z0-9]", "", t) for t in title.split()]
    kw = next((t for t in title_toks if t and t not in stop and len(t) > 2), "x")
    kw = kw[:18]
    year = str(record.get("year") or "0000")
    base = f"{last}_{kw}_{year}"
    if base not in used:
        return base
    for suffix in "abcdefghijklmnop":
        candidate = f"{last}_{kw}{suffix}_{year}"
        if candidate not in used:
            return candidate
    return base  # give up


def map_concepts(concepts: list[str]) -> list[str]:
    out: list[str] = []
    for c in concepts:
        for k in CONCEPT_MAP.get(c, []):
            if k not in out:
                out.append(k)
    return out


def render_authors(authors: list[str]) -> str:
    """Convert ['First Last', ...] or ['Last, First', ...] to BibTeX
    'Last, First and Last, First'.
    """
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


def entry_type(rec: dict) -> str:
    t = (rec.get("type") or "").lower()
    if "proceedings" in t or "conference" in t:
        return "inproceedings"
    if "book" in t:
        return "book"
    if "report" in t:
        return "techreport"
    return "article"


def escape_braces(text: str) -> str:
    """Lightly escape text for a BibTeX field value."""
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").strip()


def build_entry(rec: dict, used_keys: set[str]) -> str | None:
    if not rec.get("doi"):
        return None
    key = safe_cite_key(rec, used_keys)
    used_keys.add(key)
    et = entry_type(rec)
    venue_field = "booktitle" if et == "inproceedings" else "journal"
    venue = rec.get("venue") or ""
    fp = rec.get("first_page") or ""
    lp = rec.get("last_page") or ""
    pages = f"{fp}--{lp}" if fp and lp else (fp or "")
    concepts = rec.get("concepts") or []
    kws = map_concepts(concepts)
    if not kws:
        kws = ["earthquake", "review"]
    elif not any(
        k in {"acquisition", "processing", "modeling", "imaging", "inversion",
              "interpretation", "rock-physics", "monitoring", "microseismic",
              "ambient-noise", "near-surface", "earthquake", "computing", "ml"}
        for k in kws
    ):
        kws = ["earthquake"] + kws
    annot = escape_braces((rec.get("abstract") or "")[:240])
    if annot:
        annot = annot.split(".")[0][:200] + "."
    fields = [f"@{et}{{{key},"]
    fields.append(f"  author     = {{{render_authors(rec.get('authors') or [])}}},")
    fields.append(f"  title      = {{{escape_braces(rec.get('title') or 'Untitled')}}},")
    if venue:
        fields.append(f"  {venue_field:<10} = {{{escape_braces(venue)}}},")
    if rec.get("year"):
        fields.append(f"  year       = {{{rec['year']}}},")
    if rec.get("volume"):
        fields.append(f"  volume     = {{{rec['volume']}}},")
    if rec.get("issue"):
        fields.append(f"  number     = {{{rec['issue']}}},")
    if pages:
        fields.append(f"  pages      = {{{pages}}},")
    fields.append(f"  doi        = {{{rec['doi']}}},")
    fields.append(f"  keywords   = {{{', '.join(kws)}}},")
    if annot:
        fields.append(f"  annotation = {{{annot}}}, ")
    fields.append("}")
    return "\n".join(fields)


def load_existing_dois(bib_path: Path) -> set[str]:
    if not bib_path.exists():
        return set()
    out: set[str] = set()
    for e in parse_bib(bib_path):
        d = (e["fields"].get("doi") or "").strip().lower()
        if d:
            out.add(d)
        out.add(e["key"])
    return out


# Strong-seismic concepts: require ≥1 of these in `concepts` to keep the entry.
# Drops generic-ML / medical-ultrasound / generic-radar noise that OpenAlex
# returns for broadly-phrased queries like "deep learning" or "Marchenko".
SEISMIC_REQUIRED_CONCEPTS = {
    # Direct seismic / seismology
    "Seismology", "Seismic wave", "Seismic migration", "Seismic noise",
    "Seismic tomography", "Seismic anisotropy", "Seismic refraction",
    "Seismic reflection", "Seismogram", "Seismometer",
    "Reflection seismology", "Exploration geophysics",
    # FWI / inversion
    "Full waveform inversion", "Reverse time migration",
    "Inverse problem",
    # Earthquake
    "Seismic moment", "Earthquake", "Earthquake source", "Earthquake prediction",
    "Earthquake catalog", "Microseismicity", "Induced seismicity",
    "Hypocenter", "Focal mechanism", "Moment magnitude scale",
    "Aftershock", "Foreshock", "Earthquake swarm",
    "Receiver function", "Surface wave",
    # Geophysics overall
    "Geophysics", "Geophysical imaging",
    "Geophysical exploration",
    # Earth interior
    "Mantle (geology)", "Crust", "Lithosphere", "Lower mantle",
    "Upper mantle (Earth)", "Core (anatomy)",
}


def has_seismic_concept(rec: dict) -> bool:
    concepts = set(rec.get("concepts") or [])
    return bool(concepts & SEISMIC_REQUIRED_CONCEPTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--jsonl", type=Path, default=DEFAULT_HARVEST,
        help=f"input JSONL (default {DEFAULT_HARVEST})",
    )
    ap.add_argument(
        "--out", type=Path, default=DEFAULT_OUT,
        help=f"output BibTeX file (default {DEFAULT_OUT})",
    )
    ap.add_argument("--since", type=int, default=None, help="min publication year")
    ap.add_argument("--min-cites", type=int, default=0, help="min cited_by_count")
    ap.add_argument(
        "--limit", type=int, default=0,
        help="max entries to write (0 = no limit)",
    )
    ap.add_argument(
        "--strict-seismic", action="store_true",
        help="require ≥1 OpenAlex concept from a curated seismic whitelist; "
             "drops generic-ML / medical-ultrasound / generic-radar noise",
    )
    ap.add_argument(
        "--sort-by-cites", action="store_true",
        help="emit candidates sorted by cited_by_count desc (best first)",
    )
    args = ap.parse_args()

    if not args.jsonl.exists():
        print(f"input not found: {args.jsonl}", file=sys.stderr)
        return 1

    seen_dois = load_existing_dois(SEISMIC_BIB)
    seen_keys: set[str] = set()
    skipped_dup = 0
    skipped_filter = 0
    skipped_noseismic = 0
    kept: list[dict] = []

    with args.jsonl.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            doi = (rec.get("doi") or "").strip().lower()
            if not doi:
                continue
            if doi in seen_dois:
                skipped_dup += 1
                continue
            if args.since and (rec.get("year") or 0) < args.since:
                skipped_filter += 1
                continue
            if args.min_cites and (rec.get("cited_by_count") or 0) < args.min_cites:
                skipped_filter += 1
                continue
            if args.strict_seismic and not has_seismic_concept(rec):
                skipped_noseismic += 1
                continue
            kept.append(rec)

    if args.sort_by_cites:
        kept.sort(key=lambda r: r.get("cited_by_count") or 0, reverse=True)

    out_lines: list[str] = []
    written = 0
    for rec in kept:
        entry = build_entry(rec, seen_keys)
        if not entry:
            continue
        out_lines.append(entry)
        written += 1
        if args.limit and written >= args.limit:
            break

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n\n".join(out_lines) + "\n", encoding="utf-8")

    print(
        f"wrote {written} candidates -> {args.out}\n"
        f"  skipped {skipped_dup} dupes (already in {SEISMIC_BIB.name})\n"
        f"  skipped {skipped_filter} by --since / --min-cites filters\n"
        f"  skipped {skipped_noseismic} by --strict-seismic concept filter"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
