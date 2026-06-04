#!/usr/bin/env python3
"""Continuously harvest seismic / seismology papers from OpenAlex.

Runs as a long-lived daemon. Append-only JSONL output. Safe to Ctrl-C and
restart — resumes from where it left off via state file. Uses only stdlib.

Usage
-----
    python scripts/harvest.py
    # or in background:
    nohup python scripts/harvest.py > harvest.log 2>&1 &

Output
------
    ~/Desktop/seisbib_harvest/papers.jsonl    append-only, one paper per line
    ~/Desktop/seisbib_harvest/seen_dois.txt   DOIs already saved (dedup)
    ~/Desktop/seisbib_harvest/state.json      resume state

Stop with Ctrl-C; resume by running again.

OpenAlex API
------------
- Free, no auth. We use the "polite pool" by including a mailto in queries.
- Cursor pagination (`cursor=*`, then `meta.next_cursor`).
- Filter `has_doi:true` to drop entries without a DOI (unusable for bib).
- See: https://docs.openalex.org/
"""

from __future__ import annotations

import json
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------- config

OUTDIR = Path("~/Desktop/seisbib_harvest").expanduser()
PAPERS_FILE = OUTDIR / "papers.jsonl"
SEEN_FILE = OUTDIR / "seen_dois.txt"
STATE_FILE = OUTDIR / "state.json"

# OpenAlex requires (recommends) a contact email for the "polite pool".
USER_EMAIL = "shaowen.wang@kaust.edu.sa"

# Broad seismic / seismology search terms. Cover acquisition → processing →
# modeling → imaging → inversion → interpretation → microseismic →
# earthquake source → global seismology → near-surface → ML/AI.
SEARCH_TERMS = [
    # Imaging / migration
    "reverse time migration",
    "least squares reverse time migration",
    "Kirchhoff depth migration",
    "Gaussian beam migration",
    "one-way wave equation migration",
    "Marchenko imaging",
    # Inversion
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
    # Modeling
    "spectral element method seismic",
    "discontinuous Galerkin seismic",
    "finite difference wave equation",
    "pseudospectral wave equation",
    "perfectly matched layer absorbing boundary",
    "adjoint state method seismic",
    "automatic differentiation seismic",
    "Helmholtz solver seismic",
    # Processing
    "seismic deconvolution",
    "surface-related multiple elimination",
    "seismic interpolation deep learning",
    "seismic denoising deep learning",
    "ground roll suppression",
    "deblending simultaneous source",
    "seismic compressed sensing",
    "curvelet seismic denoising",
    # Interpretation / attributes / facies / picking
    "seismic facies machine learning",
    "salt body segmentation deep learning",
    "seismic fault detection neural network",
    "horizon picking deep learning",
    "first break picking deep learning",
    "seismic attribute analysis",
    # Microseismic / induced
    "microseismic event detection",
    "induced seismicity injection",
    "hydraulic fracturing microseismic",
    "moment tensor inversion",
    # Earthquake / global seismology
    "earthquake source inversion",
    "rupture imaging back projection",
    "receiver function",
    "shear wave splitting anisotropy",
    "global mantle tomography",
    "earthquake early warning deep learning",
    "phase picking PhaseNet",
    "earthquake catalog enhancement",
    # 4D / monitoring / CO2
    "4D time-lapse seismic",
    "CO2 storage seismic monitoring",
    "time-lapse full waveform inversion",
    "reservoir monitoring time lapse",
    # DAS / fiber / OBN / OBS
    "distributed acoustic sensing seismic",
    "ocean bottom node FWI",
    "ocean bottom seismometer tomography",
    # Near-surface / GPR / engineering
    "multichannel analysis of surface waves",
    "ground penetrating radar full waveform inversion",
    "urban seismic noise",
    # ML / generative
    "diffusion model seismic",
    "physics informed neural network seismic",
    "neural operator wave equation",
    "transformer seismic interpretation",
    "self supervised seismic learning",
    "foundation model seismic",
    # Theory / inverse problem
    "optimal transport waveform inversion",
    "cycle skipping waveform inversion",
    "regularization seismic inversion",
    # Specific datasets / benchmarks
    "Marmousi velocity model",
    "OpenFWI benchmark",
    "SEAM benchmark",
    # Misc seismic topics
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
    # Gravity & magnetics (potential field)
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

# Pagination / pacing
PER_PAGE = 200                 # OpenAlex max is 200
SLEEP_BETWEEN_PAGES = 1.0      # be polite
SLEEP_BETWEEN_TERMS = 5.0
MAX_PAGES_PER_TERM = 50        # cap so one term can't monopolize
REST_AFTER_FULL_CYCLE = 6 * 3600  # 6h sleep before re-cycling for new pubs

# ---------------------------------------------------------------------- impl

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
    return {"completed_terms": [], "cursors": {}, "cycle": 0}


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


def reconstruct_abstract(inv_index: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index. Rebuild ordered text."""
    if not inv_index:
        return ""
    pos_to_word: list[tuple[int, str]] = []
    for word, positions in inv_index.items():
        for p in positions:
            pos_to_word.append((p, word))
    pos_to_word.sort()
    return " ".join(w for _, w in pos_to_word)


def fetch_page(term: str, cursor: str) -> dict:
    """One OpenAlex /works request."""
    params = {
        "search": term,
        "per-page": str(PER_PAGE),
        "cursor": cursor,
        "mailto": USER_EMAIL,
        "filter": "has_doi:true",
        "select": (
            "id,doi,title,publication_year,authorships,"
            "primary_location,abstract_inverted_index,concepts,"
            "cited_by_count,type,biblio"
        ),
    }
    url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url, headers={"User-Agent": f"seisbib-harvest/1.0 (mailto:{USER_EMAIL})"}
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8"))


def to_record(work: dict, query: str, cycle: int) -> dict:
    authors = []
    for a in work.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    doi = work.get("doi") or ""
    if doi:
        doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    primary = work.get("primary_location") or {}
    src = primary.get("source") or {}
    biblio = work.get("biblio") or {}
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))
    return {
        "doi": doi or None,
        "title": work.get("title"),
        "authors": authors,
        "year": work.get("publication_year"),
        "type": work.get("type"),
        "venue": src.get("display_name"),
        "volume": biblio.get("volume"),
        "issue": biblio.get("issue"),
        "first_page": biblio.get("first_page"),
        "last_page": biblio.get("last_page"),
        "abstract": abstract[:2000] if abstract else None,
        "concepts": [c["display_name"] for c in (work.get("concepts") or [])[:10]],
        "cited_by_count": work.get("cited_by_count"),
        "openalex_id": work.get("id"),
        "discovered_via_query": query,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "cycle": cycle,
    }


def harvest_term(term: str, seen: set[str], state: dict) -> int:
    log(f"  >> term {term!r}")
    cursor = state["cursors"].get(term, "*")
    page = 0
    added_total = 0
    rate_limit_streak = 0  # consecutive 429s → exponential backoff
    while _RUNNING and cursor:
        page += 1
        if page > MAX_PAGES_PER_TERM:
            log(f"     cap {MAX_PAGES_PER_TERM} pages; moving on")
            break
        try:
            data = fetch_page(term, cursor)
            rate_limit_streak = 0  # reset on success
        except urllib.error.HTTPError as e:
            if e.code == 429:
                # exponential backoff: 60s, 5min, 15min, 30min, 30min, ...
                rate_limit_streak += 1
                backoff_s = [60, 300, 900, 1800][min(rate_limit_streak - 1, 3)]
                log(
                    f"     HTTP 429 (rate limit, streak={rate_limit_streak}); "
                    f"sleeping {backoff_s}s"
                )
                time.sleep(backoff_s)
                page -= 1  # don't count this as a page attempt
                continue
            log(f"     HTTP {e.code}: {e.reason}; sleeping 30s then retry")
            time.sleep(30)
            try:
                data = fetch_page(term, cursor)
            except Exception as e2:
                log(f"     retry failed: {e2}; skipping term")
                return added_total
        except Exception as e:
            log(f"     fetch error: {e}; sleeping 30s then retry")
            time.sleep(30)
            try:
                data = fetch_page(term, cursor)
            except Exception as e2:
                log(f"     retry failed: {e2}; skipping term")
                return added_total

        results = data.get("results", [])
        if not results:
            break
        added = 0
        with PAPERS_FILE.open("a", encoding="utf-8") as fp:
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
                append_seen(doi)
                added += 1
        added_total += added
        cursor = (data.get("meta") or {}).get("next_cursor")
        state["cursors"][term] = cursor or ""
        save_state(state)
        log(
            f"     page {page}: +{added} new "
            f"({len(results)-added} dupes); total {len(seen)}; "
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
        f"start cycle {state['cycle']}; "
        f"{len(seen)} DOIs seen; "
        f"{len(state['completed_terms'])}/{len(SEARCH_TERMS)} terms done"
    )

    while _RUNNING:
        todo = [t for t in SEARCH_TERMS if t not in state["completed_terms"]]
        if not todo:
            log(
                f"cycle {state['cycle']} complete. "
                f"sleeping {REST_AFTER_FULL_CYCLE}s before re-cycling for new pubs"
            )
            for _ in range(REST_AFTER_FULL_CYCLE // 10):
                if not _RUNNING:
                    break
                time.sleep(10)
            if not _RUNNING:
                break
            state["cycle"] += 1
            state["completed_terms"] = []
            state["cursors"] = {}
            save_state(state)
            continue

        term = todo[0]
        added = harvest_term(term, seen, state)
        log(f"  << term {term!r}: +{added} new this term")
        state["completed_terms"].append(term)
        state["cursors"].pop(term, None)
        save_state(state)
        if not _RUNNING:
            break
        time.sleep(SLEEP_BETWEEN_TERMS)

    log(f"exiting cleanly. seen={len(seen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
