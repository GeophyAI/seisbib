#!/usr/bin/env python3
"""Harvest ALL EAGE proceedings abstracts from Crossref.

EAGE (European Association of Geoscientists & Engineers) publishes
everything under DOI prefix `10.3997/`. As of 2026 this yields
~71K proceedings-articles (Annual Conference & Exhibition, Near
Surface Geophysics, Petroleum Geostatistics, EAGE/SBGf, IPTC, etc.).

OpenAlex's coverage of EAGE proceedings is very sparse — Crossref is
the canonical source. This script mirrors `harvest_seg.py`'s scheme
year-by-year and writes a JSONL matching `harvest.py`'s schema so
`proc_jsonl_to_bib.py` can consume it directly.

Usage
-----
    python scripts/harvest_eage.py
    # or in tsp:
    tsp -L eage-harvest python scripts/harvest_eage.py

Output
------
    ~/Desktop/seisbib_harvest/eage_papers.jsonl       JSONL (one paper/line)
    ~/Desktop/seisbib_harvest/eage_seen_dois.txt      dedup
    ~/Desktop/seisbib_harvest/eage_state.json         resume state

Integration
-----------
    python scripts/proc_jsonl_to_bib.py \
        --jsonl ~/Desktop/seisbib_harvest/eage_papers.jsonl \
        --source eage \
        --out bib/eage_abstracts.bib
"""

from __future__ import annotations

import json
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUTDIR = Path("~/Desktop/seisbib_harvest").expanduser()
PAPERS_FILE = OUTDIR / "eage_papers.jsonl"
SEEN_FILE = OUTDIR / "eage_seen_dois.txt"
STATE_FILE = OUTDIR / "eage_state.json"

USER_EMAIL = "shaowen.wang@kaust.edu.sa"

# EAGE Annual goes back to 1963 but DOI assignment to back issues starts
# only after ~1990. Anything earlier returns 0 hits but costs almost
# nothing to query.
YEAR_RANGE = list(range(1980, datetime.now(timezone.utc).year + 1))
PER_PAGE = 1000          # Crossref max is 1000 rows per request
SLEEP_BETWEEN_PAGES = 1.5
SLEEP_BETWEEN_YEARS = 3.0

_RUNNING = True


def _shutdown(signum, frame):
    global _RUNNING
    log(f"signal {signum} received — finishing current page then exiting")
    _RUNNING = False


signal.signal(signal.SIGINT, _shutdown)
signal.signal(signal.SIGTERM, _shutdown)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{ts}] {msg}", flush=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"completed_years": [], "cursors": {}}


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        return set(SEEN_FILE.read_text(encoding="utf-8").splitlines())
    return set()


def append_seen(doi: str) -> None:
    with SEEN_FILE.open("a", encoding="utf-8") as f:
        f.write(doi + "\n")


_JATS_TAG = re.compile(r"<[^>]+>")


def strip_jats(html: str | None) -> str:
    if not html:
        return ""
    return _JATS_TAG.sub("", html).replace("&amp;", "&").strip()


def fetch_page(year: int, cursor: str) -> dict:
    params = {
        "filter": (
            f"prefix:10.3997,type:proceedings-article,"
            f"from-pub-date:{year}-01-01,until-pub-date:{year}-12-31"
        ),
        "rows": str(PER_PAGE),
        "cursor": cursor,
        "select": (
            "DOI,title,author,published,container-title,volume,issue,page,"
            "type,abstract,subject,is-referenced-by-count,event,publisher"
        ),
        "mailto": USER_EMAIL,
    }
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"seisbib-eageharvest/1.0 (mailto:{USER_EMAIL})"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def render_authors(crossref_authors: list[dict] | None) -> list[str]:
    out: list[str] = []
    for a in crossref_authors or []:
        family = a.get("family") or ""
        given = a.get("given") or ""
        if family and given:
            out.append(f"{family}, {given}")
        elif family:
            out.append(family)
        elif a.get("name"):
            out.append(a["name"])
    return out


def to_record(work: dict, year: int) -> dict:
    doi = (work.get("DOI") or "").lower()
    title = (work.get("title") or [""])[0]
    containers = work.get("container-title") or []
    venue = containers[0] if containers else ""
    page = work.get("page") or ""
    fp = lp = ""
    if page:
        m = re.match(r"(\S+?)\s*[-–—]+\s*(\S+)$", page)
        if m:
            fp, lp = m.group(1), m.group(2)
        else:
            fp = page
    published = work.get("published") or {}
    parts = (published.get("date-parts") or [[None]])[0]
    pub_year = parts[0] if parts else year
    event = work.get("event") or {}
    return {
        "doi": doi,
        "title": title,
        "authors": render_authors(work.get("author")),
        "year": pub_year or year,
        "type": "proceedings-article",
        "venue": venue or "EAGE Conference Proceedings",
        "container_titles": containers,
        "event_name": event.get("name"),
        # Strip trailing comma that Crossref sometimes adds to location strings.
        "event_location": (event.get("location") or "").rstrip(", ").strip() or None,
        "event_acronym": event.get("acronym"),
        "publisher": work.get("publisher"),
        "volume": work.get("volume"),
        "issue": work.get("issue"),
        "first_page": fp,
        "last_page": lp,
        "abstract": strip_jats(work.get("abstract"))[:2000],
        "concepts": work.get("subject") or [],
        "cited_by_count": work.get("is-referenced-by-count") or 0,
        "openalex_id": None,
        "discovered_via_query": f"crossref/eage/{year}",
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "cycle": 0,
    }


def harvest_year(year: int, seen: set[str], state: dict) -> int:
    log(f"  >> year {year}")
    cursor = state["cursors"].get(str(year), "*")
    page = 0
    added_total = 0
    while _RUNNING and cursor:
        page += 1
        try:
            data = fetch_page(year, cursor)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log("     429 rate limit; 60s sleep")
                time.sleep(60)
                continue
            log(f"     HTTP {e.code} {e.reason}; 30s sleep + retry")
            time.sleep(30)
            try:
                data = fetch_page(year, cursor)
            except Exception as e2:
                log(f"     retry failed: {e2}; skip year")
                return added_total
        except Exception as e:
            log(f"     fetch err: {e}; 30s sleep + retry")
            time.sleep(30)
            try:
                data = fetch_page(year, cursor)
            except Exception as e2:
                log(f"     retry failed: {e2}; skip year")
                return added_total

        msg = data.get("message") or {}
        items = msg.get("items") or []
        if not items:
            break
        added = 0
        with PAPERS_FILE.open("a", encoding="utf-8") as fp:
            for w in items:
                doi = (w.get("DOI") or "").lower()
                if not doi or doi in seen:
                    continue
                rec = to_record(w, year)
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seen.add(doi)
                append_seen(doi)
                added += 1
        added_total += added
        cursor = msg.get("next-cursor")
        state["cursors"][str(year)] = cursor or ""
        save_state(state)
        total_for_year = msg.get("total-results") or 0
        log(
            f"     page {page}: +{added} new ({len(items)-added} dupes); "
            f"year {year} total = {total_for_year}; total seen = {len(seen)}; "
            f"cursor={'(end)' if not cursor else 'next'}"
        )
        if not cursor:
            break
        time.sleep(SLEEP_BETWEEN_PAGES)
    return added_total


def main() -> int:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    seen = load_seen()
    log(
        f"EAGE harvest start. "
        f"{len(seen)} DOIs seen; "
        f"{len(state['completed_years'])}/{len(YEAR_RANGE)} years done"
    )

    # iterate newest year first (high-value, recent abstracts)
    for year in sorted(YEAR_RANGE, reverse=True):
        if not _RUNNING:
            break
        if year in state["completed_years"]:
            continue
        added = harvest_year(year, seen, state)
        log(f"  << year {year}: +{added} new")
        state["completed_years"].append(year)
        state["cursors"].pop(str(year), None)
        save_state(state)
        if not _RUNNING:
            break
        time.sleep(SLEEP_BETWEEN_YEARS)

    log(f"exiting cleanly. total seen = {len(seen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
