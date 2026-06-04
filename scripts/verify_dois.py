"""Batch-verify DOIs in bib/seismic.bib against the Crossref API.

For each entry that has a DOI field, query:
  https://api.crossref.org/works/{DOI}

Compare the Crossref-returned title with the bib title using token-Jaccard
similarity.  Flag entries as:
  ok           – similarity >= 0.80
  mismatch     – entry found but similarity < 0.80
  not_found    – Crossref returned 404
  error        – network / parse error

Output is a JSONL file (default: scripts/doi_check.jsonl) so the run is
resumable: already-checked DOIs are skipped.

Usage:
  python scripts/verify_dois.py [--bib bib/seismic.bib] [--out scripts/doi_check.jsonl]
  python scripts/verify_dois.py --report          # print summary from existing jsonl
  python scripts/verify_dois.py --show-mismatches # print mismatch entries
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

# ---------------------------------------------------------------------------
# Add repo root to path so we can import _bibparse
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
import _bibparse  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CROSSREF_API = "https://api.crossref.org/works/{doi}"
MAILTO = "shaowen.wang@kaust.edu.sa"
UA = f"fwibib-verify/1.0 (mailto:{MAILTO})"
THRESHOLD = 0.80   # Jaccard similarity below this → mismatch
PAUSE = 0.12       # seconds between requests (polite pool)
TIMEOUT = 20       # seconds per HTTP request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tokenize(s: str) -> set[str]:
    """Lower-case word tokens, strip punctuation."""
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokenize(a), _tokenize(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cr_title(data: dict) -> str:
    """Extract the first title string from a Crossref work record."""
    titles = data.get("message", {}).get("title", [])
    return titles[0] if titles else ""


def _fetch_crossref(doi: str) -> tuple[str, dict]:
    """
    Returns (status, info_dict).
    status ∈ {"ok", "mismatch", "not_found", "error"}
    info_dict contains: cr_title, sim, error (if any)
    """
    url = CROSSREF_API.format(doi=urllib.parse.quote(doi, safe="/:"))
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        cr_t = _cr_title(data)
        return cr_t, None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", "not_found"
        return "", f"http_{e.code}"
    except Exception as e:
        return "", f"error:{e}"


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------
def load_done(out_path: Path) -> dict[str, dict]:
    """Load already-checked DOIs from the output JSONL."""
    done: dict[str, dict] = {}
    if not out_path.exists():
        return done
    with out_path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[rec["doi"].lower()] = rec
            except Exception:
                pass
    return done


def run_verify(bib_path: Path, out_path: Path, verbose: bool = True) -> None:
    entries = _bibparse.parse_bib(bib_path)
    with_doi = [(e["key"], e["fields"]) for e in entries if e["fields"].get("doi")]

    done = load_done(out_path)
    print(f"Entries with DOI: {len(with_doi)}  |  Already checked: {len(done)}", flush=True)

    todo = [(k, f) for k, f in with_doi if f["doi"].lower() not in done]
    print(f"Remaining: {len(todo)}", flush=True)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fout = out_path.open("a")
    try:
        for i, (key, fields) in enumerate(todo):
            doi = fields["doi"]
            bib_t = fields.get("title", "")

            cr_t, err = _fetch_crossref(doi)

            if err == "not_found":
                status = "not_found"
                sim = 0.0
            elif err:
                status = "error"
                sim = 0.0
            else:
                sim = _jaccard(bib_t, cr_t)
                status = "ok" if sim >= THRESHOLD else "mismatch"

            rec = {
                "key": key,
                "doi": doi,
                "status": status,
                "sim": round(sim, 4),
                "bib_title": bib_t,
                "cr_title": cr_t,
                "error": err,
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fout.flush()

            if verbose and status != "ok":
                tag = f"[{status.upper()}]"
                print(f"  {tag:14s} {key}  sim={sim:.2f}")
                if status == "mismatch":
                    print(f"    bib: {bib_t[:90]}")
                    print(f"    cr:  {cr_t[:90]}")

            if (i + 1) % 100 == 0:
                print(f"  ... {i+1}/{len(todo)} done", flush=True)

            time.sleep(PAUSE)
    finally:
        fout.close()

    print("\nDone. Writing summary...")
    print_report(out_path)


def print_report(out_path: Path) -> None:
    done = load_done(out_path)
    counts: dict[str, int] = {}
    mismatches = []
    not_found = []
    errors = []
    for rec in done.values():
        s = rec["status"]
        counts[s] = counts.get(s, 0) + 1
        if s == "mismatch":
            mismatches.append(rec)
        elif s == "not_found":
            not_found.append(rec)
        elif s == "error":
            errors.append(rec)

    total = sum(counts.values())
    print(f"\n{'='*60}")
    print(f"Total checked: {total}")
    for s in ("ok", "mismatch", "not_found", "error"):
        n = counts.get(s, 0)
        pct = 100 * n / total if total else 0
        print(f"  {s:12s}: {n:5d}  ({pct:.1f}%)")

    if mismatches:
        mismatches.sort(key=lambda r: r["sim"])
        print(f"\n--- MISMATCHES ({len(mismatches)}) ---")
        for r in mismatches:
            print(f"  [{r['sim']:.2f}] {r['key']}")
            print(f"    bib: {r['bib_title'][:90]}")
            print(f"    cr:  {r['cr_title'][:90]}")

    if not_found:
        print(f"\n--- NOT FOUND in Crossref ({len(not_found)}) ---")
        for r in not_found:
            print(f"  {r['key']:40s}  doi={r['doi']}")

    if errors:
        print(f"\n--- ERRORS ({len(errors)}) ---")
        for r in errors:
            print(f"  {r['key']}  {r['error']}")
    print('='*60)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bib", default="bib/seismic.bib")
    ap.add_argument("--out", default="scripts/doi_check.jsonl")
    ap.add_argument("--report", action="store_true", help="Print report from existing JSONL and exit")
    ap.add_argument("--show-mismatches", action="store_true")
    args = ap.parse_args()

    bib_path = Path(args.bib)
    out_path = Path(args.out)

    if args.report or args.show_mismatches:
        print_report(out_path)
        return

    run_verify(bib_path, out_path)


if __name__ == "__main__":
    main()
