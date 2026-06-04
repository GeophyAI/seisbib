#!/usr/bin/env python3
"""Robust, portable OpenAlex harvester (single-file, stdlib only).

Designed to scp to any machine (e.g. your Mac) and run there when the
primary machine's IP gets rate-limited by OpenAlex.

Output JSONL format matches scripts/harvest.py so the outputs can be
concatenated.

Usage (typical)
---------------
    # On Mac, after scp'ing this file + (optionally) seen_dois.txt
    export OPENALEX_API_KEY=...            # optional, recommended
    python3 harvest_safe.py \\
        --outdir ~/Desktop/seisbib_harvest_mac \\
        --mailto shaowen.wang@kaust.edu.sa

    # Later, scp papers.jsonl back to Linux and concatenate:
    cat ~/Desktop/seisbib_harvest_mac/papers.jsonl \\
        >> ~/Desktop/seisbib_harvest/papers.jsonl

Resume from an existing seen_dois.txt (recommended — skips already-fetched
DOIs):

    cp ~/Desktop/seisbib_harvest/seen_dois.txt \\
       ~/Desktop/seisbib_harvest_mac/seen_dois.txt
    python3 harvest_safe.py --outdir ~/Desktop/seisbib_harvest_mac

CLI flags
---------
    --outdir DIR            output dir (default ~/Desktop/seisbib_harvest_mac)
    --mailto EMAIL          email for OpenAlex polite pool
    --start-term N          skip first N terms (default 0)
    --only-new              skip terms listed in state.json's completed_terms
    --max-pages-per-term N  hard cap on pages per term (default 50)
    --sleep-between-pages S seconds (default 1.5)
    --sleep-between-terms S seconds (default 5)
    --max-backoff S         cap for 429 exponential backoff (default 1800s)

Env vars
--------
    OPENALEX_API_KEY        if set, sent as 'Authorization: Bearer ...'
                            for the authenticated pool (much higher limits)

Stops cleanly on SIGINT/SIGTERM; resume just by running again.
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

# ============================================================ search terms

# Copied verbatim from scripts/harvest.py (the broad geophysics set).
# Edit/extend in-place; this file is meant to be self-contained.
SEARCH_TERMS: list[str] = [
    # === Imaging / migration ===
    "reverse time migration",
    "least squares reverse time migration",
    "Kirchhoff depth migration",
    "Gaussian beam migration",
    "one-way wave equation migration",
    "Marchenko imaging",
    # === Inversion ===
    "full waveform inversion",
    "elastic full waveform inversion",
    "viscoacoustic full waveform inversion",
    "anisotropic full waveform inversion",
    "neural network full waveform inversion",
    "reflection waveform inversion",
    "extended full waveform inversion",
    "wave equation migration velocity analysis",
    "seismic traveltime tomography",
    "ambient noise tomography",
    "adjoint tomography",
    "joint geophysical inversion",
    "Bayesian seismic inversion",
    "pre-stack seismic inversion",
    "AVO inversion",
    "elastic impedance inversion",
    "rock physics inversion",
    "geostatistical seismic inversion",
    "uncertainty quantification seismic",
    # === Modeling ===
    "spectral element method seismic",
    "discontinuous Galerkin seismic",
    "finite difference wave equation",
    "pseudospectral wave equation",
    "perfectly matched layer absorbing boundary",
    "adjoint state method seismic",
    "automatic differentiation seismic",
    "Helmholtz solver seismic",
    # === Processing ===
    "seismic deconvolution",
    "surface-related multiple elimination",
    "seismic interpolation deep learning",
    "seismic denoising deep learning",
    "ground roll suppression",
    "deblending simultaneous source",
    "seismic compressed sensing",
    "curvelet seismic denoising",
    # === Interpretation / attributes ===
    "seismic facies machine learning",
    "salt body segmentation deep learning",
    "seismic fault detection neural network",
    "horizon picking deep learning",
    "first break picking deep learning",
    "seismic attribute analysis",
    # === Microseismic / induced ===
    "microseismic event detection",
    "induced seismicity injection",
    "hydraulic fracturing microseismic",
    "moment tensor inversion",
    # === Earthquake / global seismology ===
    "earthquake source inversion",
    "rupture imaging back projection",
    "receiver function",
    "shear wave splitting anisotropy",
    "global mantle tomography",
    "earthquake early warning deep learning",
    "phase picking PhaseNet",
    "earthquake catalog enhancement",
    # === 4D / monitoring / CO2 ===
    "4D time-lapse seismic",
    "CO2 storage seismic monitoring",
    "time-lapse full waveform inversion",
    "reservoir monitoring time lapse",
    # === DAS / fiber / OBN / OBS ===
    "distributed acoustic sensing seismic",
    "ocean bottom node FWI",
    "ocean bottom seismometer tomography",
    # === Near-surface / GPR ===
    "multichannel analysis of surface waves",
    "ground penetrating radar full waveform inversion",
    "urban seismic noise",
    # === ML / generative ===
    "diffusion model seismic",
    "physics informed neural network seismic",
    "neural operator wave equation",
    "transformer seismic interpretation",
    "self supervised seismic learning",
    "foundation model seismic",
    # === Theory / inverse problem ===
    "optimal transport waveform inversion",
    "cycle skipping waveform inversion",
    "regularization seismic inversion",
    # === Datasets / benchmarks ===
    "Marmousi velocity model",
    "OpenFWI benchmark",
    "SEAM benchmark",
    # === Misc seismic ===
    "ambient seismic noise interferometry",
    "passive seismic monitoring",
    "tsunami earthquake source",
    "seismic hazard probabilistic",
    "ground motion prediction equation",
    "site amplification Vs30",
    "Mars seismology InSight",
    "lunar seismology Apollo",
    # === Broader geophysics (added 2026-05) ===
    # Electromagnetic methods
    "magnetotelluric inversion",
    "controlled source electromagnetic CSEM",
    "marine controlled source electromagnetic",
    "transient electromagnetic method",
    "audiomagnetotelluric AMT",
    "airborne electromagnetic survey",
    "induced polarization geophysics",
    "spectral induced polarization",
    "electrical resistivity tomography",
    "self potential geophysics",
    # Gravity & magnetics
    "gravity inversion geophysics",
    "gravity gradiometry full tensor",
    "magnetic anomaly inversion",
    "Euler deconvolution gravity",
    "aeromagnetic survey interpretation",
    "satellite gravity GRACE",
    "isostatic gravity anomaly",
    "potential field continuation",
    # Well logging / borehole geophysics
    "sonic log inversion",
    "NMR well logging",
    "resistivity log formation evaluation",
    "borehole geophysics",
    "vertical seismic profile VSP",
    "cross-well seismic tomography",
    "formation evaluation petrophysics",
    "dielectric logging",
    "borehole gravity",
    # Hydrogeophysics
    "electrical resistivity hydrogeophysics",
    "groundwater geophysics",
    "saltwater intrusion ERT",
    "aquifer characterization geophysics",
    "vadose zone geophysics",
    "hydrogeophysical inversion",
    "time lapse ERT monitoring",
    # Mining / mineral exploration
    "mineral exploration geophysics",
    "ore deposit geophysics",
    "drone magnetic survey",
    "mineral prospectivity machine learning",
    # Geothermal
    "geothermal exploration geophysics",
    "enhanced geothermal system EGS",
    "magnetotelluric geothermal",
    # Planetary geophysics
    "planetary geophysics",
    "Mars crustal structure InSight",
    "Europa ice shell seismology",
    "asteroid gravity field",
    "Venus surface geophysics",
    "Moon interior structure",
    # Cryosphere / glaciology
    "ice penetrating radar",
    "glacier seismology",
    "cryoseismology",
    "ice sheet GPS deformation",
    "subglacial hydrology geophysics",
    # Volcano / geodesy / deformation
    "volcano seismology monitoring",
    "volcano deformation InSAR",
    "InSAR time series deformation",
    "GPS geodesy crustal deformation",
    "postseismic deformation modeling",
    "slow slip event detection",
    "tremor non-volcanic",
    # Geodynamics / mantle
    "mantle convection geodynamics",
    "plate boundary deformation",
    "lithosphere asthenosphere boundary",
    "subduction zone geodynamics",
    "core mantle boundary",
    # Geomagnetism / paleomagnetism
    "geomagnetic secular variation",
    "paleomagnetism reversal",
    "geomagnetic jerk",
    "ionosphere total electron content",
    "magnetic field model satellite",
    # Rock physics / lab
    "rock physics elastic moduli",
    "ultrasonic laboratory rock physics",
    "digital rock physics",
    "effective medium theory rock",
    "fluid substitution Gassmann",
    "anisotropic rock physics",
    # Inverse theory / computational geophysics
    "Tikhonov regularization geophysics",
    "Bayesian inverse problem geophysics",
    "MCMC geophysical inversion",
    "Gaussian process geophysics",
    "graph neural network geophysics",
    "open source python geophysics",
    "deep learning geophysical inversion",
    # Marine / ocean-bottom geophysics
    "marine geophysics survey",
    "multibeam bathymetry",
    "sub-bottom profiler",
    "ocean floor magnetic anomaly",
    # Seismology — additional coverage
    "ambient noise cross correlation",
    "earthquake focal mechanism",
    "fault zone trapped waves",
    "seismic anisotropy upper mantle",
    "global seismic body wave tomography",
    # Geophysics + AI broad
    "machine learning geophysics survey",
    "self supervised learning geophysics",
    "generative model geophysics",
]

# ============================================================ defaults

DEFAULT_OUTDIR = Path.home() / "Desktop" / "seisbib_harvest_mac"
PER_PAGE = 200  # OpenAlex max

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
    if state_file.exists():
        try:
            return json.loads(state_file.read_text())
        except Exception:
            pass
    return {"completed_terms": [], "cursors": {}, "cycle": 0}


def save_state(state_file: Path, state: dict) -> None:
    tmp = state_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(state_file)


# ============================================================ fetch

def build_url(term: str, cursor: str, mailto: str, api_key: str | None) -> str:
    params = {
        "search": term,
        "per-page": str(PER_PAGE),
        "cursor": cursor,
        "filter": "has_doi:true",
        "select": (
            "id,doi,display_name,authorships,publication_year,publication_date,"
            "primary_location,abstract_inverted_index,concepts,"
            "type,open_access,cited_by_count"
        ),
        "mailto": mailto,
    }
    # OpenAlex API key goes in the URL as `api_key=...`, NOT as a Bearer header.
    # See https://docs.openalex.org/how-to-use-the-api/rate-limits-and-authentication
    if api_key:
        params["api_key"] = api_key
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def fetch_page(
    term: str, cursor: str, mailto: str, api_key: str | None
) -> tuple[dict, dict]:
    """Return (json_body, response_headers)."""
    url = build_url(term, cursor, mailto, api_key)
    headers = {"User-Agent": f"seisbib-harvest-safe/1.0 (mailto:{mailto})"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        body = json.loads(resp.read().decode("utf-8"))
        hdrs = dict(resp.headers)
    return body, hdrs


def to_record(work: dict, query: str, cycle: int) -> dict:
    """Pack an OpenAlex work into the same JSONL shape harvest.py produces."""
    doi = (work.get("doi") or "").replace("https://doi.org/", "").lower()
    auths = []
    for a in (work.get("authorships") or [])[:30]:
        au = a.get("author") or {}
        nm = au.get("display_name")
        if nm:
            auths.append(nm)
    pl = work.get("primary_location") or {}
    src = pl.get("source") or {}
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
        "discovered_via_query": query,
        "harvest_cycle": cycle,
        "harvested_at": datetime.now(timezone.utc).isoformat(),
    }


# ============================================================ rate-limit handling

def parse_retry_after(headers: dict, fallback_s: int) -> int:
    """Honor Retry-After header (seconds or HTTP-date)."""
    ra = headers.get("Retry-After") or headers.get("retry-after")
    if not ra:
        return fallback_s
    try:
        return max(int(ra), 5)
    except ValueError:
        # HTTP-date form — too lazy to parse; just use fallback
        return fallback_s


def proactive_pace(headers: dict, base_sleep: float) -> float:
    """If remaining-rate is low, sleep extra."""
    rem = headers.get("x-ratelimit-remaining") or headers.get("X-RateLimit-Remaining")
    try:
        rem_int = int(rem) if rem else None
    except ValueError:
        rem_int = None
    if rem_int is not None and rem_int < 10:
        return base_sleep + 5.0
    return base_sleep


# ============================================================ per-term loop

def harvest_term(
    term: str,
    state: dict,
    seen: set[str],
    papers_file: Path,
    seen_file: Path,
    state_file: Path,
    *,
    mailto: str,
    api_key: str | None,
    max_pages: int,
    sleep_between_pages: float,
    max_backoff: int,
) -> int:
    log(f"  >> term {term!r}")
    cursor = state["cursors"].get(term, "*")
    page = 0
    added_total = 0
    streak = 0  # consecutive errors

    while _RUNNING and cursor:
        page += 1
        if page > max_pages:
            log(f"     cap {max_pages} pages; moving on")
            break
        try:
            data, hdrs = fetch_page(term, cursor, mailto, api_key)
            streak = 0
        except urllib.error.HTTPError as e:
            if e.code == 429:
                streak += 1
                # honor Retry-After; else exponential
                fallback = min(60 * (2 ** (streak - 1)), max_backoff)
                wait = parse_retry_after(dict(e.headers), fallback)
                log(
                    f"     HTTP 429 (streak={streak}); "
                    f"sleeping {wait}s (Retry-After={e.headers.get('Retry-After')})"
                )
                page -= 1  # not counted
                _interruptible_sleep(wait)
                continue
            streak += 1
            wait = min(30 * (2 ** (streak - 1)), max_backoff)
            log(f"     HTTP {e.code}: {e.reason}; sleeping {wait}s")
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
                rec = to_record(w, term, state["cycle"])
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
                seen.add(doi)
                append_seen(seen_file, doi)
                added += 1
        added_total += added
        cursor = (data.get("meta") or {}).get("next_cursor")
        state["cursors"][term] = cursor or ""
        save_state(state_file, state)

        log(
            f"     page {page}: +{added} new "
            f"({len(results)-added} dupes); seen={len(seen)}; "
            f"cursor={'(end)' if not cursor else 'next'}"
        )
        if not cursor:
            break
        # proactive pacing if rate-limit budget low
        pace = proactive_pace(hdrs, sleep_between_pages)
        _interruptible_sleep(pace)

    return added_total


def _interruptible_sleep(seconds: float) -> None:
    """Sleep, but check _RUNNING each second so SIGTERM exits promptly."""
    end = time.monotonic() + seconds
    while _RUNNING and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


# ============================================================ main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--outdir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--mailto", default="shaowen.wang@kaust.edu.sa")
    ap.add_argument("--start-term", type=int, default=0,
                    help="skip the first N terms in SEARCH_TERMS")
    ap.add_argument("--only-new", action="store_true",
                    help="skip terms already in state.json's completed_terms")
    ap.add_argument("--max-pages-per-term", type=int, default=50)
    ap.add_argument("--sleep-between-pages", type=float, default=1.5)
    ap.add_argument("--sleep-between-terms", type=float, default=5.0)
    ap.add_argument("--max-backoff", type=int, default=1800,
                    help="cap (seconds) for exponential backoff (default 30min)")
    ap.add_argument("--cycle-rest-hours", type=float, default=12.0,
                    help="hours to sleep after each full cycle (default 12)")
    args = ap.parse_args()

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
        f"api_key={'YES' if api_key else 'no (polite pool)'}; "
        f"seen={len(seen)}; "
        f"completed_terms={len(state['completed_terms'])}/{len(SEARCH_TERMS)}; "
        f"start_term={args.start_term}; only_new={args.only_new}"
    )

    while _RUNNING:
        # choose terms for this pass
        all_terms = SEARCH_TERMS[args.start_term :]
        if args.only_new:
            all_terms = [t for t in all_terms if t not in state["completed_terms"]]
        if not all_terms:
            rest_s = int(args.cycle_rest_hours * 3600)
            log(f"all terms done. sleeping {rest_s}s before next cycle.")
            _interruptible_sleep(rest_s)
            if not _RUNNING:
                break
            state["cycle"] += 1
            state["completed_terms"] = []
            state["cursors"] = {}
            save_state(state_file, state)
            continue

        for term in all_terms:
            if not _RUNNING:
                break
            if term in state["completed_terms"]:
                continue
            try:
                added = harvest_term(
                    term, state, seen, papers_file, seen_file, state_file,
                    mailto=args.mailto,
                    api_key=api_key,
                    max_pages=args.max_pages_per_term,
                    sleep_between_pages=args.sleep_between_pages,
                    max_backoff=args.max_backoff,
                )
            except KeyboardInterrupt:
                log("interrupted")
                break
            log(f"  << term {term!r}: +{added} new this term")
            state["completed_terms"].append(term)
            state["cursors"].pop(term, None)
            save_state(state_file, state)
            _interruptible_sleep(args.sleep_between_terms)

    log(f"exiting cleanly. seen={len(seen)}; outdir={outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
