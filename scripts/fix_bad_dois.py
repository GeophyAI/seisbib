"""Remove bad DOIs from bib/seismic.bib using the doi_check.jsonl report.

Strategy:
  - status=not_found AND doi.org returns 404 → DOI is hallucinated → remove doi field
  - status=mismatch AND sim < SIM_HARD   → DOI belongs to a different paper → remove doi
  - status=mismatch AND sim >= SIM_HARD  → likely a formatting false-positive → keep

The fix replaces the doi = {...} line with a note = {DOI unverified} line so the entry
is visibly flagged for manual follow-up.  Entries whose DOI was already removed / fixed
in a previous pass are left untouched.

Usage:
  python scripts/fix_bad_dois.py [--jsonl scripts/doi_check.jsonl] [--bib bib/seismic.bib]
                                  [--sim-hard 0.25] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

SIM_HARD = 0.25   # mismatches below this are treated as definitely-wrong DOIs

VERIFY_CACHE: dict[str, bool] = {}


def doi_resolves(doi: str) -> bool:
    """Return True if doi.org redirects (HTTP 2xx/3xx), False on 404."""
    if doi in VERIFY_CACHE:
        return VERIFY_CACHE[doi]
    url = f"https://doi.org/{doi}"
    try:
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "fwibib-fix/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            VERIFY_CACHE[doi] = True
            return True
    except urllib.error.HTTPError as e:
        ok = e.code not in (404, 410)
        VERIFY_CACHE[doi] = ok
        return ok
    except Exception:
        # network error – don't remove
        VERIFY_CACHE[doi] = True
        return True


def load_report(jsonl_path: Path) -> dict[str, dict]:
    """Load doi_check.jsonl into a dict keyed by lowercase DOI."""
    records: dict[str, dict] = {}
    with jsonl_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                records[rec["doi"].lower()] = rec
            except Exception:
                pass
    return records


_LATEX_BRACE = re.compile(r'\{([^{}]*)\}')


def _strip_latex(s: str) -> str:
    """Remove LaTeX braces, converting {F}ourier → fourier, {3-D} → 3-d, etc."""
    prev = None
    while prev != s:
        prev = s
        s = _LATEX_BRACE.sub(r'\1', s)
    return s.lower()


def _cr_is_prefix_of_bib(bib_t: str, cr_t: str) -> bool:
    """True if cr_title is a leading substring of the bib_title (after LaTeX strip).

    Catches the case where Crossref stores an abbreviated title and the bib
    has the full title (e.g. "Algorithm 799: revolve" vs full title with subtitle).
    """
    if not cr_t:
        return False
    bib_norm = _strip_latex(bib_t)
    cr_norm = _strip_latex(cr_t)
    return bib_norm.startswith(cr_norm)


_ALWAYS_VERIFY_PREFIXES = ("10.48550",)  # arXiv DOIs — Crossref 404 but doi.org works


def decide_bad(rec: dict, verify_404: bool, sim_hard: float = SIM_HARD) -> tuple[bool, str]:
    """Return (is_bad, reason).  If verify_404, double-check not_found via doi.org."""
    status = rec["status"]
    sim = rec["sim"]
    doi = rec["doi"]

    if status == "not_found":
        # Always verify DOIs from prefixes that bypass Crossref (e.g. arXiv)
        should_verify = verify_404 or any(doi.startswith(p) for p in _ALWAYS_VERIFY_PREFIXES)
        if should_verify:
            if not doi_resolves(doi):
                return True, f"not_found + doi.org 404 (sim={sim:.2f})"
            else:
                return False, f"not_found but doi.org resolves – keeping (sim={sim:.2f})"
        else:
            return True, f"not_found in Crossref (sim={sim:.2f})"

    if status == "mismatch" and sim < sim_hard:
        # False-positive guard: Crossref sometimes stores abbreviated titles
        if _cr_is_prefix_of_bib(rec["bib_title"], rec["cr_title"]):
            return False, f"mismatch but cr_title is prefix of bib_title (sim={sim:.2f})"
        return True, f"mismatch sim={sim:.2f} (bib: {rec['bib_title'][:50]} | cr: {rec['cr_title'][:50]})"

    return False, f"ok or acceptable ({status} sim={sim:.2f})"


# ---------------------------------------------------------------------------
# BibTeX entry-level text surgery
# ---------------------------------------------------------------------------
DOI_LINE_RE = re.compile(
    r'^(\s*)doi\s*=\s*\{[^}]*\}\s*,?\s*$',
    re.IGNORECASE | re.MULTILINE
)


def _remove_doi_field(entry_text: str, reason: str, key: str) -> str:
    """Replace doi = {...} line with a note marking the DOI as unverified."""
    if not DOI_LINE_RE.search(entry_text):
        return entry_text   # already removed or not present

    # Strip braces from the reason to avoid unbalanced BibTeX brace depth
    short_reason = reason[:120].replace('{', '(').replace('}', ')')
    note_line = f"  note       = {{DOI unverified: {short_reason}}},\n"
    return DOI_LINE_RE.sub(note_line, entry_text, count=1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jsonl", default="scripts/doi_check.jsonl")
    ap.add_argument("--bib", default="bib/seismic.bib")
    ap.add_argument("--sim-hard", type=float, default=0.15,
                    help="Sim threshold below which mismatch → bad")
    ap.add_argument("--verify-404", action="store_true",
                    help="Double-check not_found entries via doi.org (slower)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing")
    args = ap.parse_args()

    sim_hard = args.sim_hard
    jsonl_path = Path(args.jsonl)
    bib_path = Path(args.bib)

    report = load_report(jsonl_path)
    print(f"Loaded {len(report)} checked DOIs from {jsonl_path}")

    bib_text = bib_path.read_text(encoding="utf-8")

    # Parse entry boundaries: collect @type{key, ... } blocks
    entry_pattern = re.compile(
        r'(@\w+\s*\{\s*(\S+?),.*?\n\})',
        re.DOTALL
    )

    changes: list[tuple[str, str, str]] = []   # (key, old_snippet, reason)
    new_bib = bib_text

    for m in entry_pattern.finditer(bib_text):
        entry_text = m.group(1)
        key = m.group(2)

        # Find DOI value in this entry
        doi_m = re.search(r'\bdoi\s*=\s*\{([^}]+)\}', entry_text, re.IGNORECASE)
        if not doi_m:
            continue

        doi = doi_m.group(1).strip().lower()
        rec = report.get(doi)
        if not rec:
            continue   # not checked yet

        is_bad, reason = decide_bad(rec, args.verify_404, sim_hard)
        if not is_bad:
            continue

        new_entry = _remove_doi_field(entry_text, reason, key)
        if new_entry != entry_text:
            changes.append((key, reason, ""))
            if not args.dry_run:
                new_bib = new_bib.replace(entry_text, new_entry, 1)

    print(f"\nEntries flagged for DOI removal: {len(changes)}")
    for key, reason, _ in sorted(changes):
        print(f"  {key:45s} | {reason}")

    if args.dry_run:
        print("\nDry run — no changes written.")
        return

    if changes:
        bib_path.write_text(new_bib, encoding="utf-8")
        print(f"\nWrote updated {bib_path}")
    else:
        print("Nothing to update.")


if __name__ == "__main__":
    main()
