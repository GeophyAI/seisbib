#!/usr/bin/env python3
"""OpenAlex harvester using `filter=` (10× cheaper than `search=`).

Background
----------
OpenAlex (since 2025) is paid: `search=` queries cost $0.001 each,
`filter=` queries cost $0.0001 each.  $1/day budget → 1,000 searches
OR 10,000 filtered result-pages.

This script enumerates papers by concept / topic / venue filters,
which is BOTH cheaper and more comprehensive than search keywords.

Default filter
--------------
`topics.subfield.id:1908`  —  the "Geophysics" subfield in OpenAlex's
newer topic taxonomy.  If 1908 turns out NOT to be Geophysics on your
account, just pass `--filters` with the right ID.

The script's FIRST action is to print the result count for each filter
so you can ctrl-C if the IDs are wrong.

Output format matches scripts/harvest_safe.py.

Usage
-----
    # default: Geophysics subfield
    python3 harvest_filter.py --outdir ~/Desktop/seisbib_harvest_mac

    # custom filters (one per arg or comma-separated)
    python3 harvest_filter.py --filters topics.subfield.id:1908 \\
                              --filters concepts.id:C198031144

    # year window
    python3 harvest_filter.py --year-from 2000 --year-to 2026

Env vars
--------
    OPENALEX_API_KEY    paid Premium key (URL `api_key=` param)
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ============================================================ defaults

DEFAULT_OUTDIR = Path.home() / "Desktop" / "seisbib_harvest_mac"

# Best single filter for "all geophysics" in OpenAlex's Topics taxonomy.
# Subfield 1908 = Geophysics under "Earth and Planetary Sciences".  If
# 1908 is wrong on your account, override with --filters.
#
# Other useful ones (uncomment / pass on CLI):
#   concepts.id:C198031144   # Seismology  (Level 2 concept)
#   concepts.id:C49204034    # Geomagnetism
#   concepts.id:C56907956    # Geodesy
#   concepts.id:C127504880   # Geodynamics
#   concepts.id:C61048295    # Volcanology
#   concepts.id:C161176658   # Geothermal energy
#   concepts.id:C111919701   # Paleomagnetism
DEFAULT_FILTERS: list[str] = [
    "topics.subfield.id:1908",  # Geophysics subfield
]

PER_PAGE = 200

_RUNNING = True


def _sigterm(*_a):
    global _RUNNING
    _RUNNING = False
    log("received signal, will exit after current page")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


# ============================================================ I/O helpers

def load_seen(seen_file: Path) -> set[str]:
    if not seen_file.exists():
        return set()
    return {line.strip() for line in seen_file.read_text().splitlines() if line.strip()}


def append_seen(seen_file: Path, doi: str) -> None:
    with seen_file.open("a", encoding="utf-8") as fp:
        fp.write(doi + "\n")


def load_state(state_file: Path) -> dict:
    s: dict = {}
    if state_file.exists():
        try:
            s = json.loads(state_file.read_text())
        except Exception:
            s = {}
    # backfill any missing keys (so we can read state files written by
    # earlier siblings like harvest_safe.py without crashing)
    s.setdefault("completed_filters", [])
    s.setdefault("cursors", {})
    s.setdefault("cycle", 0)
    return s


def save_state(state_file: Path, state: dict) -> None:
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(state_file)


# ============================================================ API

SELECT_FIELDS = (
    "id,doi,display_name,authorships,publication_year,publication_date,"
    "primary_location,abstract_inverted_index,concepts,primary_topic,"
    "type,open_access,cited_by_count"
)


def build_url(
    filt: str,
    cursor: str,
    mailto: str,
    api_key: str | None,
    *,
    year_from: int | None,
    year_to: int | None,
    per_page: int = PER_PAGE,
) -> str:
    # Combine the primary filter with optional year range.
    combined = [filt]
    if year_from:
        combined.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        combined.append(f"to_publication_date:{year_to}-12-31")
    params = {
        "filter": ",".join(combined),
        "per-page": str(per_page),
        "cursor": cursor,
        "select": SELECT_FIELDS,
        "mailto": mailto,
    }
    if api_key:
        params["api_key"] = api_key
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def fetch_page(
    filt: str,
    cursor: str,
    mailto: str,
    api_key: str | None,
    *,
    year_from: int | None,
    year_to: int | None,
    per_page: int = PER_PAGE,
) -> tuple[dict, dict]:
    url = build_url(
        filt, cursor, mailto, api_key,
        year_from=year_from, year_to=year_to, per_page=per_page,
    )
    headers = {"User-Agent": f"seisbib-harvest-filter/1.0 (mailto:{mailto})"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8")), dict(resp.headers)


def to_record(work: dict, query: str, cycle: int) -> dict:
    doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
    auths = []
    for a in (work.get("authorships") or [])[:30]:
        au = a.get("author") or {}
        if au.get("display_name"):
            auths.append(au["display_name"])
    pl = work.get("primary_location") or {}
    src = pl.get("source") or {}
    pt = work.get("primary_topic") or {}
    return {
        "openalex_id": work.get("id"),
        "doi": doi,
        "title": work.get("display_name") or "",
        "authors": auths,
        "year": work.get("publication_year"),
        "pub_date": work.get("publication_date"),
        "venue": src.get("display_name"),
        "venue_issn_l": src.get("issn_l"),
        "venue_type": src.get("type"),
        "type": work.get("type"),
        "is_oa": (work.get("open_access") or {}).get("is_oa"),
        "cited_by_count": work.get("cited_by_count"),
        "abstract_inverted_index": work.get("abstract_inverted_index"),
        "concepts": [c.get("display_name") for c in (work.get("concepts") or [])[:10]],
        "primary_topic": pt.get("display_name"),
        "primary_topic_subfield": (pt.get("subfield") or {}).get("display_name"),
        "discovered_via_query": query,
        "harvest_cycle": cycle,
        "harvested_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================ rate-limit

def parse_retry_after(headers: dict, fallback_s: int) -> int:
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if not ra:
        return fallback_s
    try:
        return max(int(ra), 5)
    except ValueError:
        return fallback_s


def _interruptible_sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while _RUNNING and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


# ============================================================ per-filter loop

def probe_count(
    filt: str, mailto: str, api_key: str | None,
    *, year_from: int | None, year_to: int | None,
) -> int | None:
    """One small call to see total result count for this filter."""
    try:
        data, _ = fetch_page(
            filt, "*", mailto, api_key,
            year_from=year_from, year_to=year_to, per_page=1,
        )
        return (data.get("meta") or {}).get("count")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        log(f"  probe failed for {filt!r}: HTTP {e.code}: {body}")
        return None
    except Exception as e:
        log(f"  probe failed for {filt!r}: {e!r}")
        return None


def harvest_filter(
    filt: str,
    state: dict,
    seen: set[str],
    papers_file: Path,
    seen_file: Path,
    state_file: Path,
    *,
    mailto: str,
    api_key: str | None,
    year_from: int | None,
    year_to: int | None,
    max_pages: int,
    sleep_between_pages: float,
    max_backoff: int,
) -> int:
    log(f"  >> filter {filt!r}")
    cursor = state["cursors"].get(filt, "*")
    page = 0
    added_total = 0
    streak = 0

    while _RUNNING and cursor:
        page += 1
        if page > max_pages:
            log(f"     cap {max_pages} pages; moving on")
            break
        try:
            data, hdrs = fetch_page(
                filt, cursor, mailto, api_key,
                year_from=year_from, year_to=year_to,
            )
            streak = 0
        except urllib.error.HTTPError as e:
            streak += 1
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:200]
            except Exception:
                pass
            if e.code == 429:
                fallback = min(60 * (2 ** (streak - 1)), max_backoff)
                wait = parse_retry_after(dict(e.headers), fallback)
                log(f"     HTTP 429 (streak={streak}); sleeping {wait}s; body={body}")
                page -= 1
                _interruptible_sleep(wait)
                continue
            wait = min(30 * (2 ** (streak - 1)), max_backoff)
            log(f"     HTTP {e.code}: {e.reason}; body={body}; sleeping {wait}s")
            page -= 1
            _interruptible_sleep(wait)
            continue
        except Exception as e:
            streak += 1
            wait = min(30 * (2 ** (streak - 1)), max_backoff)
            log(f"     fetch error: {e!r}; sleeping {wait}s")
            page -= 1
            _interruptible_sleep(wait)
            continue

        results = data.get("results", [])
        if not results:
            break

        added = 0
        with papers_file.open("a", encoding="utf-8") as fp:
            for w in results:
                doi_raw = w.get("doi") or ""
                doi = (
                    doi_raw.replace("https://doi.org/", "")
                    .replace("http://doi.org/", "")
                    .lower()
                )
                if not doi or doi in seen:
                    continue
                rec = to_record(w, filt, state["cycle"])
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seen.add(doi)
                append_seen(seen_file, doi)
                added += 1
        added_total += added
        cursor = (data.get("meta") or {}).get("next_cursor")
        state["cursors"][filt] = cursor or ""
        save_state(state_file, state)

        log(
            f"     page {page}: +{added} new "
            f"({len(results)-added} dupes); seen={len(seen)}; "
            f"cursor={'(end)' if not cursor else 'next'}"
        )
        if not cursor:
            break
        _interruptible_sleep(sleep_between_pages)

    return added_total


# ============================================================ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--mailto", default="shaowen.wang@kaust.edu.sa")
    ap.add_argument(
        "--filters", action="append",
        help=(
            "OpenAlex filter expression. Can be repeated, or comma-separated "
            "within one arg. Default: " + ",".join(DEFAULT_FILTERS)
        ),
    )
    ap.add_argument("--year-from", type=int, default=None)
    ap.add_argument("--year-to", type=int, default=None)
    ap.add_argument("--only-new", action="store_true",
                    help="skip filters already in completed_filters")
    ap.add_argument("--max-pages-per-filter", type=int, default=50000,
                    help="hard cap per filter (default unlimited-ish)")
    ap.add_argument("--sleep-between-pages", type=float, default=0.5)
    ap.add_argument("--sleep-between-filters", type=float, default=5.0)
    ap.add_argument("--max-backoff", type=int, default=1800)
    ap.add_argument("--probe-only", action="store_true",
                    help="just print result-counts and exit (sanity check)")
    args = ap.parse_args()

    # filters: split comma-separated
    raw_filters = args.filters or DEFAULT_FILTERS
    filters: list[str] = []
    for f in raw_filters:
        filters.extend([x.strip() for x in f.split(",") if x.strip()])

    api_key = os.environ.get("OPENALEX_API_KEY") or None

    outdir: Path = args.outdir.expanduser()
    outdir.mkdir(parents=True, exist_ok=True)
    papers_file = outdir / "papers.jsonl"
    seen_file = outdir / "seen_dois.txt"
    state_file = outdir / "state.json"

    state = load_state(state_file)
    seen = load_seen(seen_file)

    signal.signal(signal.SIGINT, _sigterm)
    signal.signal(signal.SIGTERM, _sigterm)

    log(
        f"start cycle {state['cycle']}; outdir={outdir}; "
        f"api_key={'YES' if api_key else 'no'}; "
        f"seen={len(seen)}; "
        f"filters={filters}; "
        f"years={args.year_from}-{args.year_to}"
    )

    # ----- probe: print result-count for each filter (cheap, 1 call each) -----
    log("== probe (sanity-check filter counts) ==")
    total_expected = 0
    for f in filters:
        n = probe_count(
            f, args.mailto, api_key,
            year_from=args.year_from, year_to=args.year_to,
        )
        log(f"  filter={f!r}  count={n}")
        if n:
            total_expected += n
    log(f"== expected total hits (sum across filters, NOT deduped): {total_expected:,} ==")
    if args.probe_only:
        log("probe-only mode; exiting")
        return 0
    if not _RUNNING:
        return 0

    # ----- harvest -----
    while _RUNNING:
        todo = list(filters)
        if args.only_new:
            todo = [f for f in todo if f not in state["completed_filters"]]
        if not todo:
            log("all filters done. exiting (this script does NOT auto-cycle).")
            break

        for f in todo:
            if not _RUNNING:
                break
            if f in state["completed_filters"]:
                continue
            try:
                added = harvest_filter(
                    f, state, seen, papers_file, seen_file, state_file,
                    mailto=args.mailto,
                    api_key=api_key,
                    year_from=args.year_from,
                    year_to=args.year_to,
                    max_pages=args.max_pages_per_filter,
                    sleep_between_pages=args.sleep_between_pages,
                    max_backoff=args.max_backoff,
                )
            except KeyboardInterrupt:
                log("interrupted")
                break
            log(f"  << filter {f!r}: +{added} new")
            state["completed_filters"].append(f)
            state["cursors"].pop(f, None)
            save_state(state_file, state)
            _interruptible_sleep(args.sleep_between_filters)
        break  # one pass; no auto-recycle

    log(f"exiting cleanly. seen={len(seen)}; outdir={outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
