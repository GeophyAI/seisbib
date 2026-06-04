#!/usr/bin/env python3
"""Build the MkDocs documentation source from bib/seismic.bib.

Outputs into ./docs (gitignored — regenerated on every build):

    docs/index.md                Site landing
    docs/recent.md               Newest 50 entries
    docs/filter.md               Client-side keyword/year/text filter
    docs/by-topic/index.md       Topic landing (lists every keyword)
    docs/by-topic/<keyword>.md   One page per keyword (entry may appear in many)
    docs/by-year/index.md        Year landing
    docs/by-year/<year>.md       One page per year
    docs/contributing.md         Synced copy of root CONTRIBUTING.md
    docs/taxonomy.md             Synced copy of root TAXONOMY.md

Top-level nav order is defined explicitly in mkdocs.yml. Sub-pages
under by-topic/ and by-year/ are reached from the section index pages.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

from _bibparse import parse_bib, split_keywords
from _lang import looks_english

# Parallelism for write_entries_page dispatch. Bib catalogs produce
# 400+ markdown pages of varying size; serial write takes minutes.
PAGE_WORKERS = max(2, min(16, (os.cpu_count() or 2)))

# Server-side pagination: pages with more than PAGE_SIZE entries are split
# into PAGE_SIZE-entry chunks so the browser never downloads a 6+ MB file.
# The first page keeps the original filename; subsequent pages get a -{n}
# suffix, e.g. by-year/2019.md  +  by-year/2019-2.md  +  by-year/2019-3.md
PAGE_SIZE = 200

SEARCH_EXCLUDE_FM = "search:\n  exclude: true\n"

# Display-only topic merges. The bib (and validate.py) keep the
# fine-grained original keywords; by-topic grouping renames them so
# the topic index isn't cluttered with near-synonyms or singletons.
# Mapping value of None means "drop this topic from the index entirely".
TOPIC_MERGE_MAP: dict[str, str | None] = {
    # AVO family — historically AVA / AVO / EEI are used interchangeably
    "ava":              "avo",
    "ava-inversion":    "avo-inversion",
    "eei":              "avo-inversion",
    # Marmousi variants
    "marmousi2":        "marmousi",
    # Beam migration
    "gaussian-beam":    "beam-migration",
    # Seismic attributes — drop the over-specific sub-flavours
    "dip-attribute":    "attribute",
    "curvature":        "attribute",
    # Rock physics models — too fine-grained for the topic index
    "kuster-toksoz":    "rock-physics",
    "dem-rockphysics":  "rock-physics",
    "gassmann":         "rock-physics",
    # Anisotropic FWI — equivalent to FWI + anisotropic-X combination
    "vti-fwi":          "fwi",
    "tti-fwi":          "fwi",
    "efwi":             "fwi",
    # Neural-operator architectures
    "fno":              "neural-operator",
    "deeponet":         "neural-operator",
    # Acquisition / migration / statics / grid singletons
    "acquisition-geometry": "acquisition",
    "time-migration":   "depth-migration",
    "residual-statics": "statics",
    "lebedev-grid":     "staggered-grid",
    # Drop entirely (too vague or one-off)
    "adaptive":         None,
    "nonlinear-cg":     None,
    "comparison":       None,
    "index":            None,
}


def normalize_topics(keywords: list[str]) -> list[str]:
    """Apply TOPIC_MERGE_MAP, drop Nones, dedupe while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        merged = TOPIC_MERGE_MAP.get(kw, kw)
        if merged is None or merged in seen:
            continue
        out.append(merged)
        seen.add(merged)
    return out

ROOT = Path(__file__).resolve().parent.parent
BIB_FILE = ROOT / "bib" / "seismic.bib"
# Additional main-bib files merged in alongside seismic.bib. They go
# through identical parsing / by-topic / by-year / by-journal pipelines
# (NOT the catalog code path with lazy-loaded JSON). DOI / cite-key
# uniqueness is handled by proc_openalex_to_bib.py at conversion time.
EXTRA_MAIN_BIBS = [
    ROOT / "bib" / "openalex_geophysics.bib",
]
OUT_DIR = ROOT / "docs"

REPO_URL = "https://github.com/GeophyAI/seisbib"


def strip_bib_braces(s: str) -> str:
    """Remove BibTeX protection braces (e.g. `{D}` `{Mars}` `{InSight}`)
    for display. The braces stay in the field values stored in the bib
    and re-appear in render_bibtex output (so the copied .bib still
    parses correctly); only the human-facing rendering drops them.
    """
    if not s:
        return s
    return s.replace("{", "").replace("}", "")


def short_author(field: str) -> str:
    authors = [a.strip() for a in field.split(" and ") if a.strip()]
    if not authors:
        return ""
    first = authors[0]
    last = first.split(",")[0].strip() if "," in first else first.split()[-1]
    return strip_bib_braces(f"{last} et al." if len(authors) > 1 else last)


def make_link(fields: dict[str, str]) -> tuple[str, str] | None:
    doi = fields.get("doi", "").strip()
    if doi:
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if doi.startswith(prefix):
                doi = doi[len(prefix):]
        return ("DOI", f"https://doi.org/{doi}")
    url = fields.get("url", "").strip()
    if url:
        return ("Link", url)
    return None


# Canonical order in which fields appear inside a rendered BibTeX block,
# matching the convention documented in CONTRIBUTING.md. Any field not in
# this list (rare but possible — e.g. `month`) is appended afterwards in
# the order it was parsed.
_BIB_FIELD_ORDER = (
    "author", "title", "journal", "booktitle", "year",
    "volume", "number", "pages", "month", "doi", "url",
    "keywords", "annotation",
)


def render_bibtex(entry: dict[str, Any]) -> str:
    """Re-emit an entry as a clean BibTeX block, suitable for one-click
    copy via Material's `content.code.copy` button. Field order follows
    CONTRIBUTING.md."""
    et = entry.get("type", "misc")
    key = entry["key"]
    fields = entry["fields"]
    seen: set[str] = set()
    lines = [f"@{et}{{{key},"]
    name_w = max((len(n) for n in fields if fields[n]), default=10)
    name_w = max(name_w, 10)
    for name in _BIB_FIELD_ORDER:
        val = fields.get(name, "").strip()
        if not val:
            continue
        seen.add(name)
        lines.append(f"  {name:<{name_w}} = {{{val}}},")
    for name, val in fields.items():
        if name in seen:
            continue
        v = (val or "").strip()
        if not v:
            continue
        lines.append(f"  {name:<{name_w}} = {{{v}}},")
    lines.append("}")
    return "\n".join(lines)


def render_entry(entry: dict[str, Any]) -> str:
    f = entry["fields"]
    title = strip_bib_braces(f.get("title", "").replace("\n", " ").strip())
    venue = strip_bib_braces(f.get("journal") or f.get("booktitle", ""))
    year = f.get("year", "")
    author = short_author(f.get("author", ""))
    link = make_link(f)

    # H4 heading per paper so Material's search splits each paper into its
    # own indexable subsection. toc_depth in mkdocs.yml hides h4 from the
    # right sidebar so the TOC doesn't explode on long pages.
    body = [f"#### {title}"]
    meta = f"({author}, *{venue}*, {year})"
    if link:
        label, url = link
        meta += f" [[{label}]]({url})"
    meta += f" `@{entry['key']}`"
    # Lazy-copy button. The BibTeX text is NOT inlined per-entry (that
    # bloated `by-topic/inversion` to 4.3 MB of HTML); instead each page
    # appends one compact `<script type="application/json">` map at the
    # end and the click handler in seisbib-copy.js looks up by data-id.
    meta += (
        f' <button type="button" class="seisbib-copy" '
        f'data-id="{entry["key"]}" title="Copy BibTeX entry">'
        f'BibTeX</button>'
    )
    body.append(meta)

    ann = strip_bib_braces(f.get("annotation", "").strip())
    if ann:
        body.append(f"> {ann}")

    # Wrap in <div data-lang="..."> so the UI toggle in seisbib-copy.js
    # can hide non-English entries client-side. `markdown="1"` lets
    # python-markdown still parse the H4/paragraph/blockquote inside.
    # The visual separator lives *inside* the wrapper as a CSS rule on
    # the wrapper itself, so when the entry is hidden (lang or text
    # filter) the separator goes with it — without that, hidden entries
    # left a row of orphan horizontal rules with empty gaps.
    #
    # data-keywords / data-authors: searchable metadata that is NOT
    # visible in the rendered HTML (full author list, all keywords).
    # The per-page JS filter searches these alongside textContent so
    # that keyword and co-author searches actually work.
    lang = "en" if looks_english(title) else "other"
    raw_kws = " ".join(split_keywords(f.get("keywords", "")))
    # Full author list: strip LaTeX braces, keep "Last, First and …" form.
    # Replace double-quotes to avoid breaking the HTML attribute value.
    raw_authors = strip_bib_braces(f.get("author", "")).lower().replace('"', "'")
    inner = "\n\n".join(body)
    return (
        f'<div class="seisbib-entry" data-lang="{lang}"'
        f' data-keywords="{raw_kws}"'
        f' data-authors="{raw_authors}"'
        f' markdown="1">\n\n'
        f'{inner}\n\n'
        f'</div>'
    )


def year_int(entry: dict[str, Any]) -> int:
    try:
        return int(entry["fields"].get("year", "0") or "0")
    except ValueError:
        return 0


def sort_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(entries, key=lambda e: (-year_int(e), e["key"]))


def write_entries_page(
    path: Path,
    title: str,
    entries: list[dict[str, Any]],
    intro: str = "",
    frontmatter: str | None = None,
) -> None:
    sorted_entries = sort_entries(entries)
    with path.open("w", encoding="utf-8") as fp:
        if frontmatter:
            fp.write("---\n" + frontmatter + "---\n\n")
        fp.write(f"# {title}\n\n")
        if intro:
            fp.write(intro + "\n\n")
        fp.write(f"**{len(entries)}** entries.\n\n")
        # No more "---" markdown HR between entries: the separator is
        # CSS-driven (border-bottom on each .seisbib-entry) so hidden
        # entries don't leave orphan rules behind.
        for e in sorted_entries:
            fp.write(render_entry(e) + "\n\n")
        # Per-page BibTeX map: read on each "Copy BibTeX" click by
        # seisbib-copy.js. Stored inside a hidden <div> rather than
        # a <script type="application/json"> because Material's
        # instant-loading navigation strips script tags from swapped-in
        # bodies, which broke the lookup after the first cross-page nav.
        # `&` and `<` are HTML-escaped so a stray `<` in a JSON string
        # value doesn't terminate the div early; textContent in the
        # browser auto-decodes them back before JSON.parse.
        bibtex_map = {e["key"]: render_bibtex(e) for e in sorted_entries}
        json_text = json.dumps(bibtex_map, ensure_ascii=False)
        json_html = json_text.replace("&", "&amp;").replace("<", "&lt;")
        fp.write(
            '<div id="seisbib-bibtex-map" hidden>'
            + json_html
            + "</div>\n"
        )


def _page_task(args: tuple) -> str:
    """Worker entry point for ProcessPoolExecutor — must be top-level
    and picklable. Reconstructs the call to write_entries_page."""
    path, title, entries, intro, frontmatter = args
    write_entries_page(path, title, entries, intro, frontmatter)
    return str(path)


def parallel_write_pages(tasks: list[tuple]) -> None:
    """Dispatch a batch of write_entries_page calls to a process pool.

    Falls back to sequential execution for tiny batches (overhead of
    spawning workers > work).
    """
    if not tasks:
        return
    if len(tasks) <= 4 or PAGE_WORKERS <= 1:
        for t in tasks:
            _page_task(t)
        return
    workers = min(PAGE_WORKERS, len(tasks))
    # chunksize tuned so each worker gets several pages before
    # round-tripping back to the dispatcher.
    chunksize = max(1, len(tasks) // (workers * 4))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        # exhaust the iterator so exceptions propagate
        for _ in ex.map(_page_task, tasks, chunksize=chunksize):
            pass


def to_paper_dict(entry: dict[str, Any], slim: bool = False) -> dict[str, Any]:
    """Convert a bib entry to the JSON shape the filter page consumes.

    ``slim=True`` drops the bibtex string and short-truncates annotation;
    used for the SEG/EAGE catalogs where 100K+ entries × full bibtex
    would balloon the filter download. The Copy button rebuilds bibtex
    on the fly from the remaining fields for slim entries.
    """
    f = entry["fields"]
    doi = f.get("doi", "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    ann = strip_bib_braces(f.get("annotation", "").strip())
    if slim and ann and len(ann) > 200:
        ann = ann[:200].rstrip() + "..."
    d = {
        "id": entry["key"],
        "title": strip_bib_braces(f.get("title", "").replace("\n", " ").strip()),
        "authors": strip_bib_braces(f.get("author", "").strip()),
        "first_author": short_author(f.get("author", "")),
        "venue": strip_bib_braces(
            (f.get("journal") or f.get("booktitle", "")).strip()
        ),
        "year": f.get("year", "").strip(),
        "doi": doi or None,
        "url": f.get("url", "").strip() or None,
        "keywords": split_keywords(f.get("keywords", "")),
        "annotation": ann or None,
    }
    if not slim:
        d["bibtex"] = render_bibtex(entry)
    else:
        d["source"] = "catalog"  # marker: rebuild bibtex on the fly
        d["type"] = entry.get("type", "inproceedings")
    return d


def write_papers_json(out: Path, entries: list[dict[str, Any]]) -> None:
    data = [to_paper_dict(e) for e in entries]
    (out / "papers.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_catalog_papers_json(
    out: Path,
    name: str,
    entries: list[dict[str, Any]],
) -> int:
    """Write a slim per-catalog JSON for the filter page's on-demand
    SEG/EAGE search. Returns byte count.
    """
    data = [to_paper_dict(e, slim=True) for e in entries]
    path = out / f"{name}-papers.json"
    text = json.dumps(data, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return len(text)


_COPY_JS = """\
// seisbib-copy.js — click-to-copy BibTeX for the lazy-loaded entry pages.
//
// Each generated entries page emits one compact JSON object at the end:
//   <script type="application/json" id="seisbib-bibtex-map">{"key": "@..."}</script>
// The filter page exposes its map as window.__seisbibFilterBibtexMap.
// This file binds one delegated click handler and does the lookup +
// clipboard write on demand. No bibtex strings are embedded in the
// rendered HTML body, so per-entry size stays small.
(function() {
  // Diagnostic: confirm this version of the script is what's running.
  // Visible in DevTools Console; rev each change so the user can
  // tell whether their browser is on stale cache.
  var SEISBIB_COPY_REV = "rev14";
  console.log("[seisbib-copy] loaded " + SEISBIB_COPY_REV);

  // Re-parse the embedded map on every click. JSON.parse on a few
  // hundred KB takes < 5 ms; the previous caching approach proved
  // fragile when Material's instant-loading swapped the script element
  // out of the DOM (filter page was reported broken on the 2nd click).
  function getMap() {
    var el = document.getElementById('seisbib-bibtex-map');
    if (el) {
      try {
        return JSON.parse(el.textContent || el.innerText || '{}');
      } catch (e) {
        console.log('[seisbib-copy] JSON parse failed: ' + e);
        return {};
      }
    }
    if (window.__seisbibFilterBibtexMap) {
      return window.__seisbibFilterBibtexMap;
    }
    return {};
  }
  function copyToClip(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise(function(resolve, reject) {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.left = '-1000px';
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      try { document.execCommand('copy'); resolve(); }
      catch (e) { reject(e); }
      document.body.removeChild(ta);
    });
  }
  function flash(btn, label) {
    if (!btn.dataset.origLabel) btn.dataset.origLabel = btn.textContent;
    btn.textContent = label;
    btn.classList.add('seisbib-copy-flash');
    setTimeout(function() {
      btn.textContent = btn.dataset.origLabel;
      btn.classList.remove('seisbib-copy-flash');
    }, 1400);
  }
  // English-only toggle: a small checkbox injected at the top of any
  // page that contains entries with data-lang="other". Persists via
  // localStorage so the choice carries across pages.
  function isEnglishOnly() {
    var v = null;
    try { v = localStorage.getItem('seisbibEnglishOnly'); } catch (e) {}
    // default ON — most users want to see English titles only
    return v === null ? true : (v === '1');
  }
  function setEnglishOnly(on) {
    try { localStorage.setItem('seisbibEnglishOnly', on ? '1' : '0'); } catch (e) {}
    document.body.classList.toggle('seisbib-en-only', on);
    // Re-run filter so the pagination count reflects the new visible set.
    if (document.getElementById('seisbib-entry-search')) applyEntryFilter();
  }
  function injectLangToggle() {
    var anyNonEn = document.querySelector('.seisbib-entry[data-lang="other"]');
    if (!anyNonEn) return;
    if (document.querySelector('.seisbib-lang-toggle')) return;
    var article = document.querySelector('article')
               || document.querySelector('main')
               || document.body;
    var nonEnCount = document.querySelectorAll(
      '.seisbib-entry[data-lang="other"]'
    ).length;
    var enCount = document.querySelectorAll(
      '.seisbib-entry[data-lang="en"]'
    ).length;
    var wrap = document.createElement('div');
    wrap.className = 'seisbib-lang-toggle';
    var on = isEnglishOnly();
    wrap.innerHTML =
      '<label>' +
        '<input type="checkbox" id="seisbib-en-toggle"' +
        (on ? ' checked' : '') + '> ' +
        'Show only English titles' +
        ' <span class="seisbib-lang-counts">' +
          '(' + enCount + ' English, ' + nonEnCount + ' other)' +
        '</span>' +
      '</label>';
    var h1 = article.querySelector('h1');
    if (h1 && h1.parentNode) {
      h1.parentNode.insertBefore(wrap, h1.nextSibling);
    } else {
      article.insertBefore(wrap, article.firstChild);
    }
    document.getElementById('seisbib-en-toggle').addEventListener(
      'change',
      function(ev) { setEnglishOnly(ev.target.checked); }
    );
  }
  function applyLangFilter() {
    // Apply persisted state on every page load (so user's choice
    // carries across navigation, including direct URL loads).
    setEnglishOnly(isEnglishOnly());
    injectLangToggle();
  }

  // Per-page entry search box. Injected at the top of every page that
  // has `.seisbib-entry` divs (by-topic / by-year / by-journal /
  // recent / SEG / EAGE). Filters in-place via display:none.
  function escRegex(s) {
    return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  }
  function clearHighlight(entry) {
    if (entry._origHTML !== undefined) {
      entry.innerHTML = entry._origHTML;
      delete entry._origHTML;
      delete entry._seisbibText;
    }
  }
  function highlightTokensIn(entry, tokens) {
    if (entry._origHTML === undefined) entry._origHTML = entry.innerHTML;
    else entry.innerHTML = entry._origHTML;
    if (!tokens.length) return;
    var pat = tokens.map(escRegex).filter(Boolean).join('|');
    if (!pat) return;
    var re = new RegExp('(' + pat + ')', 'gi');
    var walker = document.createTreeWalker(entry, NodeFilter.SHOW_TEXT, {
      acceptNode: function(n) {
        // Skip text inside <button> (the "BibTeX" label) and <code>
        // (cite-keys + inline keyword chips) so we don't visually
        // mark static UI chrome that isn't part of the entry content.
        var p = n.parentNode;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.nodeName;
        if (tag === 'BUTTON' || tag === 'SCRIPT' || tag === 'STYLE') {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var nodes = [];
    var node;
    while ((node = walker.nextNode())) nodes.push(node);
    nodes.forEach(function(textNode) {
      var text = textNode.textContent;
      re.lastIndex = 0;
      if (!re.test(text)) return;
      re.lastIndex = 0;
      var frag = document.createDocumentFragment();
      var last = 0, m;
      while ((m = re.exec(text))) {
        if (m.index > last) {
          frag.appendChild(document.createTextNode(text.slice(last, m.index)));
        }
        var mark = document.createElement('mark');
        mark.className = 'seisbib-highlight';
        mark.textContent = m[0];
        frag.appendChild(mark);
        last = m.index + m[0].length;
        if (m[0].length === 0) re.lastIndex++;
      }
      if (last < text.length) {
        frag.appendChild(document.createTextNode(text.slice(last)));
      }
      if (textNode.parentNode) {
        textNode.parentNode.replaceChild(frag, textNode);
      }
    });
  }
  // Client-side pagination state.
  // All entries are always in the DOM; JS shows PAGE_SIZE at a time.
  // Search always spans ALL entries — only the display window is paginated.
  var SEISBIB_PAGE_SIZE = 200;
  var seisbibCurrentPage = 0;
  var seisbibLastQ = null;   // detect query change → reset to page 0

  function updatePaginationControls(currentPage, totalPages) {
    var ctrl = document.getElementById('seisbib-pagination');
    if (!ctrl) return;
    ctrl.hidden = (totalPages <= 1);
    if (totalPages <= 1) return;
    document.getElementById('seisbib-prev-btn').disabled = (currentPage === 0);
    document.getElementById('seisbib-next-btn').disabled = (currentPage >= totalPages - 1);
    document.getElementById('seisbib-page-info').textContent =
      (currentPage + 1) + ' / ' + totalPages;
  }

  function applyEntryFilter() {
    var input = document.getElementById('seisbib-entry-search');
    if (!input) return;
    var q = input.value.trim().toLowerCase();
    var tokens = q ? q.split(/\\s+/).filter(Boolean) : [];

    // Reset to page 0 whenever the query changes.
    if (q !== seisbibLastQ) {
      seisbibCurrentPage = 0;
      seisbibLastQ = q;
    }

    var allEntries = Array.from(document.querySelectorAll('.seisbib-entry'));
    var enOnly = document.body.classList.contains('seisbib-en-only');

    // Pass 1: collect all entries that satisfy lang + text filters.
    // This search spans every entry in the DOM — pagination does NOT
    // restrict what is searchable, only what is rendered at once.
    var matching = [];
    allEntries.forEach(function(entry) {
      if (enOnly && entry.dataset.lang === 'other') return;
      if (tokens.length) {
        // Build a searchable string from visible text PLUS hidden metadata
        // stored in data-keywords / data-authors (keywords and full author
        // list are not rendered in the HTML, so textContent alone misses them).
        var text = (entry._seisbibText
                    || (entry._seisbibText = (
                         (entry.textContent || '') + ' ' +
                         (entry.dataset.keywords || '') + ' ' +
                         (entry.dataset.authors || '')
                       ).toLowerCase()));
        for (var i = 0; i < tokens.length; i++) {
          if (text.indexOf(tokens[i]) < 0) return;
        }
      }
      matching.push(entry);
    });

    // Pass 2: paginate the matching set.
    var totalMatching = matching.length;
    var totalPages = Math.max(1, Math.ceil(totalMatching / SEISBIB_PAGE_SIZE));
    seisbibCurrentPage = Math.max(0, Math.min(seisbibCurrentPage, totalPages - 1));
    var pageStart = seisbibCurrentPage * SEISBIB_PAGE_SIZE;
    // Use a Set for O(1) membership test in Pass 3.
    var pageSet = new Set(matching.slice(pageStart, pageStart + SEISBIB_PAGE_SIZE));

    // Pass 3: show/hide + highlight. Only paint highlights on visible entries
    // — saves work on the hidden majority during a busy keystroke.
    allEntries.forEach(function(entry) {
      var show = pageSet.has(entry);
      entry.classList.toggle('seisbib-entry-nomatch', !show);
      if (show) {
        highlightTokensIn(entry, tokens);
      } else {
        clearHighlight(entry);
      }
    });

    // Update counter.
    var counter = document.getElementById('seisbib-entry-count');
    if (counter) {
      var totalAll = allEntries.length;
      counter.textContent = (tokens.length && totalMatching < totalAll)
        ? (totalMatching + ' / ' + totalAll + ' matched')
        : (totalAll + ' entries');
    }

    updatePaginationControls(seisbibCurrentPage, totalPages);
  }

  function injectEntryFilter() {
    if (!document.querySelector('.seisbib-entry')) return;
    if (document.querySelector('.seisbib-entry-filter')) return;
    var article = document.querySelector('article')
               || document.querySelector('main')
               || document.body;

    // Search bar + entry counter.
    var wrap = document.createElement('div');
    wrap.className = 'seisbib-entry-filter';
    wrap.innerHTML =
      '<input type="search" id="seisbib-entry-search" ' +
        'placeholder="Filter by title / author / venue / year / keyword...">' +
      '<span id="seisbib-entry-count" class="seisbib-entry-count"></span>';
    // Insert after the lang-toggle if present, else after H1.
    var anchor = document.querySelector('.seisbib-lang-toggle')
              || article.querySelector('h1');
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(wrap, anchor.nextSibling);
    } else {
      article.insertBefore(wrap, article.firstChild);
    }

    // Pagination controls, injected at the bottom of the article.
    var ctrl = document.createElement('div');
    ctrl.id = 'seisbib-pagination';
    ctrl.className = 'seisbib-pagination';
    ctrl.hidden = true;
    ctrl.innerHTML =
      '<button id="seisbib-prev-btn" type="button">← Prev</button>' +
      '<span class="seisbib-page-label">Page ' +
        '<span id="seisbib-page-info"></span>' +
      '</span>' +
      '<button id="seisbib-next-btn" type="button">Next →</button>';
    article.appendChild(ctrl);

    document.getElementById('seisbib-prev-btn').addEventListener('click', function() {
      if (seisbibCurrentPage > 0) {
        seisbibCurrentPage--;
        applyEntryFilter();
        var first = document.querySelector('.seisbib-entry:not(.seisbib-entry-nomatch)');
        if (first) first.scrollIntoView({behavior: 'smooth', block: 'start'});
      }
    });
    document.getElementById('seisbib-next-btn').addEventListener('click', function() {
      seisbibCurrentPage++;
      applyEntryFilter();
      var first = document.querySelector('.seisbib-entry:not(.seisbib-entry-nomatch)');
      if (first) first.scrollIntoView({behavior: 'smooth', block: 'start'});
    });

    document.getElementById('seisbib-entry-search')
      .addEventListener('input', applyEntryFilter);
    applyEntryFilter();
  }

  function bindClicks(root) {
    if (!root) return;
    if (root._seisbibBound) return;
    root._seisbibBound = true;
    console.log("[seisbib-copy] bindClicks attached to body (" + SEISBIB_COPY_REV + ")");
    root.addEventListener('click', function(ev) {
      var btn = ev.target.closest && ev.target.closest('.seisbib-copy');
      if (!btn) return;
      ev.preventDefault();
      ev.stopPropagation();
      var id = btn.dataset.id;
      var source = btn.dataset.source || 'page';
      var m;
      if (source === 'filter') {
        m = window.__seisbibFilterBibtexMap || {};
      } else {
        m = getMap();
      }
      var text = m && m[id];
      // Filter page slim catalogs (SEG/EAGE) don't have bibtex inline;
      // fall back to a JS builder that synthesises a bibtex string
      // from the in-memory paper record.
      if (!text && source === 'filter' && typeof window.__seisbibCatalogBuilder === 'function') {
        text = window.__seisbibCatalogBuilder(id);
      }
      var nKeys = Object.keys(m).length;
      console.log(
        '[seisbib-copy] click id=' + id +
        ' source=' + source +
        ' map.size=' + nKeys +
        ' found=' + !!text +
        (text ? ' len=' + text.length : '')
      );
      if (!text) { flash(btn, '! not found'); return; }
      copyToClip(text).then(
        function() { flash(btn, '✓ copied'); },
        function() { flash(btn, '! failed'); }
      );
    });
  }
  // ---------------------------------------------------------------------------
  // Homepage inline search (seisbib-home-q input on index.md).
  // Fetches papers.json once, then filters and renders results as-you-type.
  // Only activates when the homepage widget is present in the DOM.
  // ---------------------------------------------------------------------------
  var _homeData = null;       // null = not loaded; [] = loading; Array = ready
  var _homeCallbacks = [];    // queued callbacks while loading

  function _homeLoad(cb) {
    if (_homeData) { cb(_homeData); return; }
    _homeCallbacks.push(cb);
    if (_homeCallbacks.length > 1) return;   // fetch already in flight
    fetch('papers.json')
      .then(function(r) { return r.json(); })
      .then(function(d) {
        _homeData = d;
        var cbs = _homeCallbacks.splice(0);
        cbs.forEach(function(fn) { fn(d); });
      })
      .catch(function(e) {
        console.warn('[seisbib-home] papers.json load failed:', e);
        _homeCallbacks = [];
      });
  }

  function _homeEsc(s) {
    return String(s || '')
      .replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }

  function _homeSearch(q, data) {
    var tokens = q.toLowerCase().split(/\\s+/).filter(Boolean);
    var out = [];
    for (var i = 0; i < data.length; i++) {
      var p = data[i];
      var hay = [
        p.title || '', p.authors || '', p.venue || '',
        String(p.year || ''), (p.keywords || []).join(' '), p.id || '',
        p.annotation || ''
      ].join(' ').toLowerCase();
      var ok = true;
      for (var j = 0; j < tokens.length; j++) {
        if (hay.indexOf(tokens[j]) < 0) { ok = false; break; }
      }
      if (ok) { out.push(p); if (out.length >= 30) break; }
    }
    return out;
  }

  function _homeRender(results, q) {
    var el = document.getElementById('seisbib-home-results');
    if (!el) return;
    if (!q) { el.innerHTML = ''; return; }
    if (!results.length) {
      el.innerHTML = '<p class="seisbib-home-empty">No results for <strong>' +
        _homeEsc(q) + '</strong>.</p>';
      return;
    }
    var more = (results.length === 30);
    var html = '<p class="seisbib-home-rcount">' +
      (more ? '30+' : results.length) + ' paper' + (results.length !== 1 ? 's' : '') +
      ' — <a href="filter/?q=' + encodeURIComponent(q) + '">see all →</a></p>';
    html += '<div class="seisbib-home-list">';
    results.forEach(function(p) {
      var doiA = p.doi
        ? ' <a class="seisbib-home-doi" href="https://doi.org/' +
          _homeEsc(p.doi) + '" target="_blank" rel="noopener">DOI ↗</a>'
        : '';
      var bibBtn = p.bibtex
        ? ' <button class="seisbib-copy seisbib-home-bib" type="button"' +
          ' data-bibtex="' + _homeEsc(p.bibtex) + '"' +
          ' title="Copy BibTeX">BibTeX</button>'
        : '';
      var metaParts = [];
      if (p.first_author) metaParts.push(_homeEsc(p.first_author));
      if (p.venue)        metaParts.push('<em>' + _homeEsc(p.venue) + '</em>');
      if (p.year)         metaParts.push(_homeEsc(p.year));
      var kws = (p.keywords || []).slice(0, 5)
        .map(function(k) { return '<code>' + _homeEsc(k) + '</code>'; }).join(' ');
      html += '<div class="seisbib-home-entry">';
      html += '<div class="seisbib-home-title">' + _homeEsc(p.title) + doiA + bibBtn + '</div>';
      if (metaParts.length) {
        html += '<div class="seisbib-home-meta">' + metaParts.join(', ') + '</div>';
      }
      if (kws) html += '<div class="seisbib-home-kws">' + kws + '</div>';
      html += '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
  }

  function initHomeSearch() {
    var input = document.getElementById('seisbib-home-q');
    if (!input || input._homeSearchBound) return;
    input._homeSearchBound = true;
    var _homeTimer = null;
    input.addEventListener('input', function() {
      var q = input.value.trim();
      clearTimeout(_homeTimer);
      var res = document.getElementById('seisbib-home-results');
      if (!q) { if (res) res.innerHTML = ''; return; }
      _homeTimer = setTimeout(function() {
        _homeLoad(function(data) { _homeRender(_homeSearch(q, data), q); });
      }, 120);
    });

    // Delegated BibTeX copy — handles buttons rendered inside results.
    // stopPropagation prevents the body-level bindClicks handler from also
    // firing (it looks up data-id which these buttons don't have → "not found").
    var resEl = document.getElementById('seisbib-home-results');
    if (resEl) {
      resEl.addEventListener('click', function(ev) {
        var btn = ev.target.closest && ev.target.closest('.seisbib-home-bib');
        if (!btn) return;
        ev.stopPropagation();
        var bib = btn.dataset.bibtex;
        if (!bib) return;
        copyToClip(bib).then(
          function() { flash(btn, '✓ copied'); },
          function() { flash(btn, '! failed'); }
        );
      });
    }

    // Preload papers.json quietly so first search feels instant.
    setTimeout(function() { _homeLoad(function() {}); }, 500);
  }

  function onPageReady() {
    bindClicks(document.body);
    applyLangFilter();
    injectEntryFilter();
    initHomeSearch();
  }
  // Material's instant-loading nav swaps the body without firing a
  // full page load. Subscribe to its `document$` observable when it
  // exists so our handlers bind to every navigation.
  if (typeof document$ !== 'undefined' && document$.subscribe) {
    document$.subscribe(onPageReady);
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', onPageReady);
  } else {
    onPageReady();
  }
})();
"""

_COPY_CSS = """\
/* seisbib-copy.css — small inline "BibTeX" copy button. */
.seisbib-copy {
  display: inline-block;
  margin-left: 0.25em;
  padding: 0 0.45em;
  font: inherit;
  font-size: 0.78em;
  font-weight: 600;
  line-height: 1.5;
  color: var(--md-default-fg-color--light);
  background: transparent;
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.25em;
  cursor: pointer;
  vertical-align: baseline;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.seisbib-copy:hover {
  color: var(--md-accent-fg-color);
  border-color: var(--md-accent-fg-color);
}
.seisbib-copy-flash {
  color: var(--md-accent-bg-color, #fff) !important;
  background: var(--md-accent-fg-color) !important;
  border-color: var(--md-accent-fg-color) !important;
}

/* English-only filter toggle */
.seisbib-lang-toggle {
  margin: 0.6em 0 1.2em;
  padding: 0.5em 0.8em;
  background: var(--md-code-bg-color, #f5f5f5);
  border-left: 3px solid var(--md-accent-fg-color, #5e72e4);
  border-radius: 0.25em;
  font-size: 0.88em;
}
.seisbib-lang-toggle label {
  cursor: pointer;
  user-select: none;
}
.seisbib-lang-toggle input[type="checkbox"] {
  margin-right: 0.4em;
  vertical-align: -1px;
}
.seisbib-lang-counts {
  color: var(--md-default-fg-color--light);
  font-size: 0.88em;
  margin-left: 0.4em;
}
body.seisbib-en-only .seisbib-entry[data-lang="other"] {
  display: none;
}
/* Compact entry style — replaces the old `---` HR between entries
   with a single border-bottom on the wrapper, so when entries get
   hidden (lang toggle, text filter) no orphan separator is left. */
.seisbib-entry {
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
  padding: 0.4em 0 0.6em;
}
.seisbib-entry > h4,
.seisbib-entry > p > h4 {
  margin-top: 0.3em !important;
  margin-bottom: 0.2em !important;
}
.seisbib-entry > p {
  margin: 0.2em 0;
}
.seisbib-entry > blockquote {
  margin: 0.3em 0 0;
  font-size: 0.92em;
}

/* Per-page entry filter */
.seisbib-entry-filter {
  display: flex;
  align-items: center;
  gap: 0.6em;
  margin: 0.6em 0 1.2em;
}
.seisbib-entry-filter input[type="search"] {
  flex: 1;
  padding: 0.5em 0.7em;
  font: inherit;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  box-sizing: border-box;
}
.seisbib-entry-count {
  white-space: nowrap;
  font-size: 0.85em;
  color: var(--md-default-fg-color--light);
}
.seisbib-entry.seisbib-entry-nomatch {
  display: none;
}
mark.seisbib-highlight {
  background: rgba(255, 217, 0, 0.55);
  color: inherit;
  padding: 0 0.05em;
  border-radius: 0.15em;
}

/* Homepage inline search widget */
.seisbib-home-widget {
  max-width: 720px;
  margin: 1.5em 0 2em;
}
.seisbib-home-search {
  display: flex;
  align-items: center;
  gap: 0.75em;
}
.seisbib-home-search input[type="search"] {
  flex: 1;
  padding: 0.65em 1em;
  font-size: 1.08em;
  font-family: inherit;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 2px solid var(--md-default-fg-color--lighter);
  border-radius: 0.4em;
  box-sizing: border-box;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.seisbib-home-search input[type="search"]:focus {
  outline: none;
  border-color: var(--md-accent-fg-color);
  box-shadow: 0 0 0 3px rgba(var(--md-accent-fg-color--rgb, 83,116,228), 0.15);
}
.seisbib-home-hint {
  font-size: 0.82em;
  white-space: nowrap;
  color: var(--md-default-fg-color--light);
}
/* Results panel */
#seisbib-home-results {
  margin-top: 0.8em;
}
.seisbib-home-rcount {
  font-size: 0.85em;
  color: var(--md-default-fg-color--light);
  margin: 0 0 0.5em;
}
.seisbib-home-empty {
  font-size: 0.9em;
  color: var(--md-default-fg-color--light);
  margin: 0.5em 0;
}
.seisbib-home-list {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.seisbib-home-entry {
  padding: 0.55em 0;
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
}
.seisbib-home-entry:last-child {
  border-bottom: none;
}
.seisbib-home-title {
  font-size: 0.97em;
  font-weight: 600;
  line-height: 1.35;
}
.seisbib-home-doi {
  font-size: 0.8em;
  font-weight: 400;
  margin-left: 0.4em;
  color: var(--md-accent-fg-color);
  text-decoration: none;
}
.seisbib-home-doi:hover { text-decoration: underline; }
.seisbib-home-meta {
  font-size: 0.83em;
  color: var(--md-default-fg-color--light);
  margin-top: 0.15em;
}
.seisbib-home-kws {
  margin-top: 0.2em;
  font-size: 0.78em;
}
.seisbib-home-kws code {
  background: var(--md-code-bg-color, #f5f5f5);
  padding: 0.05em 0.35em;
  border-radius: 0.2em;
  font-family: inherit;
  font-size: inherit;
  margin-right: 0.2em;
}

/* Client-side pagination controls */
.seisbib-pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.8em;
  margin: 1.5em 0 0.5em;
  padding: 0.7em 0;
  border-top: 1px solid var(--md-default-fg-color--lightest);
}
.seisbib-pagination button {
  padding: 0.3em 0.85em;
  font: inherit;
  font-size: 0.88em;
  font-weight: 600;
  color: var(--md-default-fg-color--light);
  background: var(--md-code-bg-color, #f5f5f5);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.seisbib-pagination button:hover:not(:disabled) {
  color: var(--md-accent-fg-color);
  border-color: var(--md-accent-fg-color);
}
.seisbib-pagination button:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.seisbib-page-label {
  font-size: 0.88em;
  color: var(--md-default-fg-color--light);
}
"""


def write_copy_assets(out: Path) -> None:
    (out / "seisbib-copy.js").write_text(_COPY_JS, encoding="utf-8")
    (out / "seisbib-copy.css").write_text(_COPY_CSS, encoding="utf-8")
    # Copy static theme overrides (logo, extra.css, extra.js) into docs/assets/
    static_assets = ROOT / "static" / "assets"
    if static_assets.is_dir():
        dest_assets = out / "assets"
        dest_assets.mkdir(exist_ok=True)
        for src in static_assets.iterdir():
            shutil.copy2(src, dest_assets / src.name)


def write_filter_page(
    out: Path,
    entries: list[dict[str, Any]],
    catalog_totals: dict[str, int] | None = None,
) -> None:
    ct = catalog_totals or {}
    main_n = len(entries)
    seg_n = ct.get("seg", 0)
    eage_n = ct.get("eage", 0)

    papers = [to_paper_dict(e) for e in entries]
    papers_json = json.dumps(papers, ensure_ascii=False)
    # HTML-escape for the hidden <div> container. textContent in the
    # browser auto-decodes &amp; and &lt; before JSON.parse sees them.
    papers_json_safe = papers_json.replace("&", "&amp;").replace("<", "&lt;")

    # NOTE: filter page uses a hidden <div> instead of
    # <script type="application/json"> because Material's
    # instant-loading navigation strips script tags from swapped-in
    # bodies. HTML-escape `&` and `<` so a stray char in any field
    # value doesn't truncate the div early.
    template = """---
search:
  exclude: true
---

# Filter

Combine keyword chips (AND across selected) with year chips (OR across
selected) and a free-text query. Results update live.

<style>
.fwibib-filter {
  margin: 1em 0;
}
.fwibib-searchbar {
  display: flex;
  gap: 0.5em;
  margin-bottom: 0.5em;
}
.fwibib-searchbar input[type="search"] {
  flex: 1;
  padding: 0.5em 0.7em;
  font: inherit;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  box-sizing: border-box;
}
.fwibib-searchbar select {
  padding: 0.5em 0.6em;
  font: inherit;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  cursor: pointer;
}
.fwibib-filter input[type="search"] {
  width: 100%;
  padding: 0.5em 0.7em;
  font: inherit;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  margin-bottom: 0.5em;
  box-sizing: border-box;
}
.fwibib-sources {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6em 1.5em;
  margin: 0.4em 0 0.6em;
  font-size: 0.88em;
}
.fwibib-sources label {
  cursor: pointer;
  user-select: none;
}
.fwibib-sources input[type="checkbox"] {
  margin-right: 0.3em;
  vertical-align: -1px;
}
.fwibib-src-status {
  color: var(--md-default-fg-color--light);
  font-size: 0.88em;
  margin-left: 0.3em;
}
.fwibib-filter details {
  margin: 0.4em 0;
}
.fwibib-filter summary {
  cursor: pointer;
  font-weight: 600;
  padding: 0.25em 0;
}
.fwibib-filter input.fwibib-chip-filter {
  width: 100%;
  padding: 0.35em 0.6em;
  margin: 0.3em 0 0.4em;
  font: inherit;
  font-size: 0.88em;
  background: var(--md-default-bg-color);
  color: var(--md-default-fg-color);
  border: 1px solid var(--md-default-fg-color--lighter);
  border-radius: 0.3em;
  box-sizing: border-box;
}
.fwibib-chip.fwibib-chip-hidden {
  display: none !important;
}
.fwibib-chip {
  display: inline-block;
  border: 1px solid var(--md-default-fg-color--lighter);
  background: transparent;
  padding: 0.15em 0.6em;
  margin: 0.15em 0.1em;
  border-radius: 999px;
  font: inherit;
  font-size: 0.82em;
  cursor: pointer;
  color: var(--md-default-fg-color);
  line-height: 1.4;
}
.fwibib-chip:hover {
  border-color: var(--md-accent-fg-color);
}
.fwibib-chip.active {
  background: var(--md-accent-fg-color);
  color: var(--md-accent-bg-color, #fff);
  border-color: var(--md-accent-fg-color);
}
#fwibib-stats {
  margin: 0.8em 0 0.4em;
  font-weight: 600;
  color: var(--md-default-fg-color--light);
}
.fwibib-result {
  padding: 0.8em 0;
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
}
.fwibib-result-meta {
  font-size: 0.85em;
  color: var(--md-default-fg-color--light);
  margin: 0.25em 0;
}
.fwibib-result-keywords code {
  font-size: 0.78em;
  margin: 0 0.15em 0 0;
}
.fwibib-result blockquote {
  margin: 0.4em 0 0;
  font-size: 0.92em;
}
.fwibib-truncated {
  margin: 1em 0;
  padding: 0.6em 0.8em;
  background: var(--md-code-bg-color, #f5f5f5);
  border-left: 3px solid var(--md-accent-fg-color);
  font-size: 0.88em;
  color: var(--md-default-fg-color--light);
}
</style>

<div class="fwibib-filter" markdown="0">
  <div class="fwibib-searchbar">
    <input id="fwibib-text" type="search" placeholder="Type to search...">
    <select id="fwibib-scope" title="Where to search">
      <option value="all">All fields</option>
      <option value="title">Title only</option>
      <option value="annotation">Note / annotation only</option>
      <option value="author">Author only</option>
      <option value="venue">Venue only</option>
      <option value="keywords">Keywords only</option>
    </select>
  </div>
  <div class="fwibib-sources">
    <label><input type="checkbox" id="fwibib-src-main" checked disabled> Curated main bib (__MAIN_N__)</label>
    <label><input type="checkbox" id="fwibib-src-seg"> SEG abstracts (__SEG_N__) <span class="fwibib-src-status" data-src="seg"></span></label>
    <label><input type="checkbox" id="fwibib-src-eage"> EAGE abstracts (__EAGE_N__) <span class="fwibib-src-status" data-src="eage"></span></label>
  </div>
  <details open>
    <summary>Keywords (AND)</summary>
    <input type="search" class="fwibib-chip-filter" data-target="fwibib-keywords" placeholder="Type to narrow keywords (e.g. 3d, fwi, ml)...">
    <div id="fwibib-keywords"></div>
  </details>
  <details>
    <summary>Venues / journals (OR)</summary>
    <input type="search" class="fwibib-chip-filter" data-target="fwibib-venues" placeholder="Type to narrow venues (e.g. Geophysics, GJI, EAGE)...">
    <div id="fwibib-venues"></div>
  </details>
  <details>
    <summary>Years (OR)</summary>
    <input type="search" class="fwibib-chip-filter" data-target="fwibib-years" placeholder="Type a year...">
    <div id="fwibib-years"></div>
  </details>
  <button id="fwibib-clear" type="button" class="fwibib-chip">Clear all</button>
</div>

<div id="fwibib-stats"></div>
<div id="fwibib-results"></div>

<div id="fwibib-data" hidden>__DATA__</div>
<script>
(function() {
  var FILTER_REV = "rev13";
  console.log("[filter] script loaded " + FILTER_REV);
  function init() {
    console.log("[filter] init() called " + FILTER_REV);
    var resultsEl = document.getElementById('fwibib-results');
    if (!resultsEl) { console.log("[filter] no #fwibib-results, bailing"); return; }
    if (resultsEl.dataset.initialized === '1') { console.log("[filter] already initialized, bailing"); return; }
    resultsEl.dataset.initialized = '1';

    var dataEl = document.getElementById('fwibib-data');
    var mainPapers = [];
    try { mainPapers = JSON.parse(dataEl.textContent); }
    catch (err) {
      console.log("[filter] JSON parse failed: " + err);
      resultsEl.innerHTML = '<p>Failed to parse paper data.</p>';
      return;
    }
    // Total searchable corpus: main + any loaded catalogs.
    // SEG/EAGE entries are loaded on demand (~30+60MB JSON each)
    // when the user ticks the corresponding source checkbox.
    var papers = mainPapers.slice();
    var loadedCatalogs = {};     // src -> array of papers
    var catalogFetchInFlight = {}; // src -> Promise
    console.log("[filter] parsed " + mainPapers.length + " main papers");

    function buildChips(containerId, items) {
      var c = document.getElementById(containerId);
      if (!c) return;
      // Preserve currently-active values so a rebuild (after loading
      // SEG/EAGE catalog) doesn't drop the user's selections.
      var keep = new Set(
        Array.from(c.querySelectorAll('.fwibib-chip.active'))
             .map(function(el) { return el.dataset.value; })
      );
      c.innerHTML = '';
      items.forEach(function(item) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'fwibib-chip';
        if (keep.has(item)) b.classList.add('active');
        b.textContent = item;
        b.dataset.value = item;
        b.addEventListener('click', function() {
          b.classList.toggle('active');
          update();
        });
        c.appendChild(b);
      });
    }
    function rebuildAllChips() {
      // Defensive: filter undefined / non-object entries before reading
      // .keywords / .venue / .year. Caught a crash when a fetched
      // catalog JSON happened to have an entry without expected fields.
      var safePapers = papers.filter(function(p) { return p && typeof p === 'object'; });
      var allKeywords = Array.from(new Set(safePapers.flatMap(function(p) {
        return Array.isArray(p.keywords) ? p.keywords : [];
      }))).sort();
      var allYears = Array.from(new Set(safePapers.map(function(p) {
        return p.year ? String(p.year) : '';
      }).filter(Boolean))).sort().reverse();
      var allVenues = Array.from(new Set(safePapers.map(function(p) {
        return p.venue ? String(p.venue).trim() : '';
      }).filter(Boolean))).sort();
      buildChips('fwibib-keywords', allKeywords);
      buildChips('fwibib-venues', allVenues);
      buildChips('fwibib-years', allYears);
      // Re-apply the chip-narrowing search filter so newly-added chips
      // respect the user's typed query.
      document.querySelectorAll('.fwibib-chip-filter').forEach(function(inp) {
        if (typeof applyChipFilter === 'function') applyChipFilter(inp);
      });
    }
    rebuildAllChips();

    // Type-to-narrow inputs above each chip group:
    //   * empty input → only active chips visible (clean default)
    //   * typed query → show chips whose label substring-matches, plus
    //                   active chips (so they can be deactivated)
    // Hides the long default list of 100+ keywords until the user
    // actually starts narrowing.
    function applyChipFilter(input) {
      var targetId = input.dataset.target;
      var q = input.value.trim().toLowerCase();
      var container = document.getElementById(targetId);
      if (!container) return;
      container.querySelectorAll('.fwibib-chip').forEach(function(chip) {
        var label = (chip.dataset.value || '').toLowerCase();
        var isActive = chip.classList.contains('active');
        var show = isActive || (q && label.indexOf(q) >= 0);
        chip.classList.toggle('fwibib-chip-hidden', !show);
      });
    }
    document.querySelectorAll('.fwibib-chip-filter').forEach(function(input) {
      input.addEventListener('input', function() { applyChipFilter(input); });
      // initial pass: hide all inactive chips
      applyChipFilter(input);
    });

    // Debounced wrapper for the text-input listener. Filtering 100K+
    // papers + building HTML on every keystroke caused noticeable lag
    // when SEG/EAGE catalogs are loaded; 120 ms after the last keystroke
    // is well below perception threshold for "typed too fast" cases.
    var _updateTimer = null;
    function debouncedUpdate() {
      if (_updateTimer) clearTimeout(_updateTimer);
      _updateTimer = setTimeout(function() {
        _updateTimer = null;
        update();
      }, 120);
    }
    document.getElementById('fwibib-text').addEventListener('input', debouncedUpdate);
    document.getElementById('fwibib-scope').addEventListener('change', update);
    document.getElementById('fwibib-clear').addEventListener('click', function() {
      document.getElementById('fwibib-text').value = '';
      document.querySelectorAll('.fwibib-chip.active').forEach(function(el) {
        el.classList.remove('active');
      });
      // also clear chip-narrowing inputs and re-collapse all chips so
      // we go back to the clean default (no chips shown until typed).
      document.querySelectorAll('.fwibib-chip-filter').forEach(function(inp) {
        inp.value = '';
        applyChipFilter(inp);
      });
      update();
    });

    function getActive(containerId) {
      return Array.from(
        document.querySelectorAll('#' + containerId + ' .fwibib-chip.active')
      ).map(function(el) { return el.dataset.value; });
    }

    function escapeHtml(s) {
      return String(s).replace(/&/g, '&amp;')
        .replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
    function escRegex(s) {
      return s.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
    }
    function highlight(text, tokens) {
      // Returns HTML-escaped `text` with each token occurrence wrapped
      // in <mark>. tokens=[] returns plain escaped text. Case-insensitive.
      if (!text) return '';
      if (!tokens || !tokens.length) return escapeHtml(text);
      var pat = tokens.map(escRegex).filter(Boolean).join('|');
      if (!pat) return escapeHtml(text);
      var re = new RegExp('(' + pat + ')', 'gi');
      var parts = [];
      var last = 0, m;
      while ((m = re.exec(text))) {
        if (m.index > last) parts.push(escapeHtml(text.slice(last, m.index)));
        parts.push('<mark class="seisbib-highlight">' + escapeHtml(m[0]) + '</mark>');
        last = m.index + m[0].length;
        if (m[0].length === 0) re.lastIndex++;
      }
      if (last < text.length) parts.push(escapeHtml(text.slice(last)));
      return parts.join('');
    }

    var FILTER_RENDER_CAP = 500;  // cap rendered DOM size; "X more not shown" hint added
    function update() {
      var text = document.getElementById('fwibib-text').value.trim().toLowerCase();
      var scope = (document.getElementById('fwibib-scope') || {}).value || 'all';
      var activeKws = getActive('fwibib-keywords');
      var activeYrs = getActive('fwibib-years');

      // Filter is empty by default: skip rendering all 6700+ papers
      // (would freeze the browser). Show a hint until the user types
      // text or selects at least one keyword / venue / year chip.
      if (!text && !activeKws.length && !activeYrs.length && !activeVenues.length) {
        document.getElementById('fwibib-stats').textContent =
          'Type in the search box, pick a keyword chip, or pick a year ' +
          'to filter the ' + papers.length + ' papers in the library.';
        resultsEl.innerHTML = '';
        return;
      }

      var activeVenues = getActive('fwibib-venues');
      var matched = papers.filter(function(p) {
        if (!p || typeof p !== 'object') return false;
        if (activeKws.length) {
          var pk = Array.isArray(p.keywords) ? p.keywords : [];
          if (!activeKws.every(function(k) { return pk.indexOf(k) >= 0; })) return false;
        }
        if (activeYrs.length && activeYrs.indexOf(String(p.year || '')) < 0) return false;
        if (activeVenues.length &&
            activeVenues.indexOf(String(p.venue || '').trim()) < 0) return false;
        if (text) {
          var hay;
          switch (scope) {
            case 'title':      hay = p.title || ''; break;
            case 'annotation': hay = p.annotation || ''; break;
            case 'author':     hay = p.authors || ''; break;
            case 'venue':      hay = p.venue || ''; break;
            case 'keywords':   hay = (p.keywords || []).join(' '); break;
            default: hay = [
              p.title, p.authors, p.venue, p.annotation || '',
              (p.keywords || []).join(' '), String(p.year || ''), p.id
            ].join(' ');
          }
          hay = hay.toLowerCase();
          var tokens = text.split(/\\s+/);
          for (var i = 0; i < tokens.length; i++) {
            if (hay.indexOf(tokens[i]) < 0) return false;
          }
        }
        return true;
      });

      matched.sort(function(a, b) {
        var y = (Number(b.year) || 0) - (Number(a.year) || 0);
        if (y !== 0) return y;
        return (a.id < b.id) ? -1 : 1;
      });

      var total = matched.length;
      var truncated = total > FILTER_RENDER_CAP;
      if (truncated) matched = matched.slice(0, FILTER_RENDER_CAP);
      // Tokens for highlighting (only when there's text search input).
      var tokens = text ? text.split(/\\s+/).filter(Boolean) : [];
      document.getElementById('fwibib-stats').textContent =
        (truncated
          ? ('showing first ' + FILTER_RENDER_CAP + ' of ' + total)
          : (total + ' of ' + papers.length + ' papers'));

      // Scope-aware highlighting: only mark fields the text search is
      // actually scanning. (Active chip selections aren't highlighted
      // — they're already exact-match.)
      var tokTitle = (scope === 'all' || scope === 'title') ? tokens : [];
      var tokAuth  = (scope === 'all' || scope === 'author') ? tokens : [];
      var tokVen   = (scope === 'all' || scope === 'venue') ? tokens : [];
      var tokAnn   = (scope === 'all' || scope === 'annotation') ? tokens : [];
      var tokKw    = (scope === 'all' || scope === 'keywords') ? tokens : [];
      var html = matched.map(function(p) {
        var t = highlight(p.title || '', tokTitle);
        var a = highlight(p.first_author || p.authors || '', tokAuth);
        var v = highlight(p.venue || '', tokVen);
        var y = escapeHtml(p.year || '');
        var id = escapeHtml(p.id || '');
        var link = '';
        if (p.doi) link = ' <a href="https://doi.org/' + escapeHtml(p.doi) + '">[DOI]</a>';
        else if (p.url) link = ' <a href="' + escapeHtml(p.url) + '">[Link]</a>';
        var kws = (p.keywords || []).map(function(k) {
          return '<code>' + highlight(k, tokKw) + '</code>';
        }).join(' ');
        var ann = p.annotation
          ? '<blockquote>' + highlight(p.annotation, tokAnn) + '</blockquote>' : '';
        // Per-result lazy-copy button: handler in seisbib-copy.js looks
        // up the bibtex via the global filter map (main bib) OR
        // window.__seisbibCatalogBuilder (SEG/EAGE slim entries). The
        // button is always shown; SEG/EAGE entries get their bibtex
        // rebuilt on the fly from the in-memory paper record.
        var copyBtn = ' <button type="button" class="seisbib-copy" data-id="' +
          id + '" data-source="filter" title="Copy BibTeX entry">BibTeX</button>';
        return '<div class="fwibib-result">' +
          '<strong>' + t + '</strong> ' +
          '<span class="fwibib-result-meta">(' + a + ', <em>' + v + '</em>, ' + y + ')' +
          link + ' <code>@' + id + '</code>' + copyBtn + '</span>' +
          '<div class="fwibib-result-keywords">' + kws + '</div>' +
          ann +
          '</div>';
      }).join('');
      resultsEl.innerHTML = html;
    }

    function rebuildBibtexMap() {
      // Main bib papers carry an inline `bibtex` field. SEG/EAGE
      // slim catalogs don't, so seisbib-copy.js will fall back to
      // window.__seisbibCatalogBuilder() for any id not in the map.
      window.__seisbibFilterBibtexMap = papers.reduce(function(acc, p) {
        if (p && p.bibtex) acc[p.id] = p.bibtex;
        return acc;
      }, {});
    }
    // Builder used by seisbib-copy.js when a filter result's id is not
    // in the eager map (i.e. it came from a slim SEG/EAGE catalog).
    // Reconstructs a BibTeX block from the in-memory paper record.
    window.__seisbibCatalogBuilder = function(id) {
      var p = papers.find(function(x) { return x && x.id === id; });
      if (!p) return null;
      function clean(s) {
        return String(s || '').replace(/[{}]/g, function(c) {
          return c === '{' ? '(' : ')';
        });
      }
      var et = p.type === 'article' ? 'article' : 'inproceedings';
      var venueField = et === 'inproceedings' ? 'booktitle' : 'journal';
      var lines = ['@' + et + '{' + p.id + ','];
      if (p.authors) lines.push('  author     = {' + clean(p.authors) + '},');
      if (p.title) lines.push('  title      = {' + clean(p.title) + '},');
      if (p.venue) lines.push('  ' + (venueField + '   ').slice(0, 10) +
                              ' = {' + clean(p.venue) + '},');
      if (p.year) lines.push('  year       = {' + p.year + '},');
      if (p.doi) lines.push('  doi        = {' + p.doi + '},');
      if (p.keywords && p.keywords.length) {
        lines.push('  keywords   = {' + p.keywords.join(', ') + '},');
      }
      if (p.annotation) lines.push('  annotation = {' + clean(p.annotation) + '},');
      lines.push('}');
      return lines.join('\\n');
    };

    rebuildBibtexMap();
    console.log("[filter] map registered with " +
      Object.keys(window.__seisbibFilterBibtexMap).length + " entries");

    function toggleCatalog(src, enable, statusEl) {
      if (enable) {
        if (loadedCatalogs[src]) {
          papers = papers.concat(loadedCatalogs[src]);
          rebuildBibtexMap();
          rebuildAllChips();
          update();
          if (statusEl) statusEl.textContent = '';
          return;
        }
        if (catalogFetchInFlight[src]) return;
        if (statusEl) statusEl.textContent = '... loading';
        var base = location.pathname.replace(/[^\\/]*$/, '');
        var url = base.replace(/filter\\/?$/, '') + src + '-papers.json';
        console.log('[filter] fetching', url);
        catalogFetchInFlight[src] = fetch(url).then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        }).then(function(arr) {
          if (!Array.isArray(arr)) {
            throw new Error('expected JSON array, got ' + typeof arr);
          }
          loadedCatalogs[src] = arr;
          papers = papers.concat(arr);
          console.log('[filter] loaded', src, arr.length, 'entries, total', papers.length);
          try {
            rebuildBibtexMap();
            rebuildAllChips();
            update();
          } catch (uiErr) {
            console.error('[filter] post-load UI error:', uiErr);
            throw uiErr;
          }
          if (statusEl) statusEl.textContent = ' (' + arr.length + ' loaded)';
        }).catch(function(e) {
          console.error('[filter] toggle failed for', src, e);
          if (statusEl) statusEl.textContent = ' (load failed: ' + e + ')';
        }).finally(function() {
          delete catalogFetchInFlight[src];
        });
      } else {
        if (loadedCatalogs[src]) {
          var loadedIds = new Set(loadedCatalogs[src].map(function(p) { return p && p.id; }));
          papers = papers.filter(function(p) { return p && !loadedIds.has(p.id); });
          rebuildBibtexMap();
          rebuildAllChips();
          update();
          if (statusEl) statusEl.textContent = ' (unloaded)';
        }
      }
    }
    ['seg', 'eage'].forEach(function(src) {
      var cb = document.getElementById('fwibib-src-' + src);
      var stat = document.querySelector('.fwibib-src-status[data-src="' + src + '"]');
      if (!cb) return;
      cb.addEventListener('change', function() {
        toggleCatalog(src, cb.checked, stat);
      });
    });

    update();
    console.log("[filter] first update() done");

    // Pre-fill search from ?q= URL parameter (set by the homepage search box).
    try {
      var urlQ = new URLSearchParams(location.search).get('q');
      if (urlQ) {
        var textInput = document.getElementById('fwibib-text');
        if (textInput && !textInput.value) {
          textInput.value = urlQ;
          textInput.dispatchEvent(new Event('input'));
        }
      }
    } catch (e) {}
  }

  if (typeof document$ !== 'undefined' && document$.subscribe) {
    document$.subscribe(function() {
      var el = document.getElementById('fwibib-results');
      if (el) { el.dataset.initialized = ''; init(); }
    });
  } else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
</script>
"""
    content = (
        template
        .replace("__DATA__", papers_json_safe)
        .replace("__MAIN_N__", f"{main_n:,}")
        .replace("__SEG_N__", f"{seg_n:,}")
        .replace("__EAGE_N__", f"{eage_n:,}")
    )
    (out / "filter.md").write_text(content, encoding="utf-8")


def copy_with_header(
    src: Path,
    dst: Path,
    title: str,
    link_rewrites: dict[str, str] | None = None,
    frontmatter: str | None = None,
) -> None:
    text = src.read_text(encoding="utf-8")
    if link_rewrites:
        for old, new in link_rewrites.items():
            text = text.replace(old, new)
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        text = "\n".join(lines[1:]).lstrip()

    src_rel = src.relative_to(ROOT).as_posix()
    fm = "---\n" + frontmatter + "---\n\n" if frontmatter else ""
    header = (
        f"# {title}\n\n"
        f'!!! note "Auto-synced from [`{src_rel}`]'
        f"({REPO_URL}/blob/main/{src_rel})\"\n"
        f"    Edit the source file in the repo root; this page is "
        f"regenerated on every build.\n\n"
    )
    dst.write_text(fm + header + text, encoding="utf-8")


def write_landing(
    out: Path,
    n_entries: int,
    n_topics: int,
    n_years: int,
    n_journals: int,
    catalog_totals: dict[str, int],
) -> None:
    seg_n = catalog_totals.get("seg", 0)
    eage_n = catalog_totals.get("eage", 0)
    grand_total = n_entries + seg_n + eage_n
    content = f"""<div class="sb-hero">
<div class="sb-hero__eyebrow">A community bibliography for seismic research</div>
<h1 class="sb-hero__title">Every <em>seismic</em> paper, one place.</h1>
<p class="sb-hero__sub">
seisbib collects {grand_total:,} papers spanning acquisition, processing,
imaging, FWI, microseismic, and earthquake studies — curated, machine-checked,
and Zotero-ready.
</p>
</div>

<div class="sb-stats">
<div class="sb-stat">
  <div class="sb-stat__num">{grand_total:,}</div>
  <div class="sb-stat__label">total entries</div>
  <div class="sb-stat__sub">{n_entries:,} curated · {seg_n:,} SEG · {eage_n:,} EAGE</div>
</div>
<div class="sb-stat">
  <div class="sb-stat__num">{n_topics}</div>
  <div class="sb-stat__label">topics</div>
  <div class="sb-stat__sub">controlled taxonomy</div>
</div>
<div class="sb-stat">
  <div class="sb-stat__num">{n_journals}</div>
  <div class="sb-stat__label">venues</div>
  <div class="sb-stat__sub">journals &amp; conferences</div>
</div>
<div class="sb-stat">
  <div class="sb-stat__num">{n_years}</div>
  <div class="sb-stat__label">years</div>
  <div class="sb-stat__sub">1960s to present</div>
</div>
</div>

<div class="seisbib-home-widget">
<div class="seisbib-home-search">
<input type="search" id="seisbib-home-q"
       placeholder="Search {grand_total:,}+ papers — title, author, keyword, venue…"
       autocomplete="off" spellcheck="false">
<span class="seisbib-home-hint">↵ or <a href="filter/">advanced filter</a></span>
</div>
<div id="seisbib-home-results"></div>
</div>

## Browse

<div class="sb-topics">
<a class="sb-topic" href="by-topic/">
  <div class="sb-topic__num">01</div>
  <div class="sb-topic__label">By topic</div>
  <div class="sb-topic__count">{n_topics} topic groups</div>
</a>
<a class="sb-topic" href="by-year/">
  <div class="sb-topic__num">02</div>
  <div class="sb-topic__label">By year</div>
  <div class="sb-topic__count">{n_years} years</div>
</a>
<a class="sb-topic" href="by-journal/">
  <div class="sb-topic__num">03</div>
  <div class="sb-topic__label">By journal</div>
  <div class="sb-topic__count">{n_journals} venues</div>
</a>
<a class="sb-topic" href="filter/">
  <div class="sb-topic__num">04</div>
  <div class="sb-topic__label">Filter</div>
  <div class="sb-topic__count">live keyword &amp; year filter</div>
</a>
<a class="sb-topic" href="seg/">
  <div class="sb-topic__num">05</div>
  <div class="sb-topic__label">SEG abstracts</div>
  <div class="sb-topic__count">{seg_n:,} expanded abstracts</div>
</a>
<a class="sb-topic" href="eage/">
  <div class="sb-topic__num">06</div>
  <div class="sb-topic__label">EAGE abstracts</div>
  <div class="sb-topic__count">{eage_n:,} conference papers</div>
</a>
</div>

## Cite

```bash
curl -O https://raw.githubusercontent.com/GeophyAI/seisbib/main/bib/seismic.bib
```

Cite-keys follow `firstauthor_keyword_year`, e.g.
`\\cite{{he_reparameterized_2021}}`.

## Contribute

See the [Contributing guide](contributing.md) and the
[Taxonomy](taxonomy.md) of allowed keywords.
"""
    (out / "index.md").write_text(content, encoding="utf-8")


def write_topic_index(
    out_topic: Path,
    by_topic: dict[str, list],
    frontmatter: str | None = None,
) -> None:
    with (out_topic / "index.md").open("w", encoding="utf-8") as fp:
        if frontmatter:
            fp.write("---\n" + frontmatter + "---\n\n")
        fp.write("# By topic\n\n")
        fp.write(
            "Each entry can carry multiple keywords, so a paper appears on "
            "every relevant topic page. See [Taxonomy](../taxonomy.md) for "
            "the controlled vocabulary.\n\n"
        )
        fp.write(f"**{len(by_topic)}** topics:\n\n")
        for kw in sorted(by_topic.keys()):
            fp.write(f"- [{kw}]({kw}.md) — {len(by_topic[kw])} entries\n")


def write_year_index(
    out_year: Path,
    by_year: dict[str, list],
    frontmatter: str | None = None,
) -> None:
    with (out_year / "index.md").open("w", encoding="utf-8") as fp:
        if frontmatter:
            fp.write("---\n" + frontmatter + "---\n\n")
        fp.write("# By year\n\n")
        fp.write(f"**{len(by_year)}** years covered.\n\n")
        for y in sorted(by_year.keys(), reverse=True):
            fp.write(f"- [{y}]({y}.md) — {len(by_year[y])} entries\n")


_JOURNAL_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def journal_slug(name: str) -> str:
    """URL/filename-safe slug from a venue title."""
    s = name.strip().lower()
    s = _JOURNAL_SLUG_BAD.sub("-", s).strip("-")
    return s[:80] or "untitled"


# ---------------------------------------------------------------------------
# Conference-series normalisation for SEG / EAGE catalogs
# ---------------------------------------------------------------------------

# Each entry: (display_name, slug, regex_pattern)
_SEG_SERIES: list[tuple[str, str, str]] = [
    ("SEG Annual Meeting",
     "seg-annual-meeting",
     r"SEG Technical Program|SEG Expanded Abstracts|\bSEG\b.*Abstract|\d{4} SEG"),
    ("IMAGE – International Meeting for Applied Geoscience & Energy",
     "image",
     r"International Meeting for Applied Geoscience.*Energy|Applied Geoscience.*Energy"),
    ("Brazilian Geophysical Society (SBGf / EXPOGEF)",
     "sbgf-expogef",
     r"Brazilian Geophysical|EXPOGEF|SBGf|Simp.*Bras"),
    ("International Geophysical Conference (China)",
     "igc-china",
     r"International Geophysical Conference|Beijing.*Geophysical|Qingdao.*Geophysical"),
    ("URTeC – Unconventional Resources Technology Conference",
     "urtech",
     r"Unconventional Resources Technology|URTeC"),
    ("SEG International Conference and Exhibition (ICE)",
     "seg-ice",
     r"International Conference and Exhibition"),
    ("IPTC – International Petroleum Technology Conference",
     "iptc-seg",
     r"IPTC|International Petroleum Technology"),
    ("Near Surface Geoscience (SEG)",
     "seg-near-surface",
     r"Near Surface|NSG\b"),
]

_EAGE_SERIES: list[tuple[str, str, str]] = [
    ("EAGE Annual Conference & Exhibition",
     "eage-annual",
     r"\bEAEG?\b.*(?:Meeting|Conference)|\bEAGE Annual\b"
     r"|\bEAGE Conference\b.*Exhibition|\bEAGE.*Annual Conference\b"),
    ("Near Surface Geoscience",
     "near-surface-geoscience",
     r"Near Surface|NSG\b"),
    ("GeoSiberia",
     "geosiberia",
     r"GeoSiberia"),
    ("Petroleum Geostatistics",
     "petroleum-geostatistics",
     r"Petroleum Geostatistics"),
    ("EAGE Digitalization Conference & Exhibition",
     "eage-digitalization",
     r"Digitali[sz]ation"),
    ("EAGE Workshops",
     "eage-workshops",
     r"EAGE.*Workshop|Workshop.*EAGE"),
    ("Brazilian Geophysical Society (SBGf / EXPOGEF)",
     "sbgf-eage",
     r"Brazilian Geophysical|EXPOGEF|SBGf|Simp.*Bras"),
    ("IPTC – International Petroleum Technology Conference",
     "iptc-eage",
     r"IPTC|International Petroleum Technology Conference"),
    ("GEO (Bahrain)",
     "geo-bahrain",
     r"GEO 20\d\d\b"),
    ("EAGE Global Energy Transition (GET)",
     "eage-get",
     r"Global Energy Transition|GET 20"),
    ("EAGE St. Petersburg",
     "eage-st-petersburg",
     r"Saint Petersburg|St Petersburg|St\. Petersburg"),
    ("International Meeting on Organic Geochemistry (IMOG)",
     "imog",
     r"Organic Geochemistry|IMOG"),
    ("Geomodel (Russia)",
     "geomodel",
     r"Geomodel"),
    ("EAGE Engineering & Mining Geophysics",
     "eage-engineering-mining",
     r"Engineering.*Mining Geophysics|Mining.*Engineering Geophysics"),
    ("European Environmental & Engineering Geophysics (EAGE/EEGS)",
     "eegs",
     r"Balkan Geophysical|EEGS|European.*Environmental.*Engineering"),
    ("Monitoring Geological Processes (Ukraine)",
     "monitoring-geological",
     r"Monitoring.*Geological|Ecological Condition"),
]

# Compile all patterns once.
_SEG_SERIES_COMPILED = [
    (display, slug, re.compile(pat, re.I))
    for display, slug, pat in _SEG_SERIES
]
_EAGE_SERIES_COMPILED = [
    (display, slug, re.compile(pat, re.I))
    for display, slug, pat in _EAGE_SERIES
]


def normalize_conference_series(booktitle: str, catalog: str) -> tuple[str, str]:
    """Return (display_name, slug) for a conference booktitle.

    Matches against a priority-ordered list of patterns for the given
    catalog ('seg' or 'eage').  Falls through to a catch-all on no match.
    """
    bt = booktitle or ""
    table = _SEG_SERIES_COMPILED if catalog == "seg" else _EAGE_SERIES_COMPILED
    for display, slug, pat in table:
        if pat.search(bt):
            return display, slug
    if catalog == "seg":
        return "Other SEG Events", "other-seg-events"
    return "Other EAGE Events", "other-eage-events"


def get_venue(entry: dict[str, Any]) -> str:
    f = entry["fields"]
    return (f.get("journal") or f.get("booktitle") or "").strip()


def build_by_journal(
    entries: list[dict[str, Any]],
) -> dict[str, tuple[str, list[dict[str, Any]]]]:
    """Group `@article` entries by their `journal` field.

    Returns slug → (display_name, [entries]). Conference papers
    (`@inproceedings` with `booktitle`) are intentionally NOT
    included here — they're already covered by the SEG / EAGE
    catalogs and per-year views, and lumping them in produced 600+
    one-off venue pages (one per `85th EAGE …`, etc.).
    """
    by_journal: dict[str, tuple[str, list[dict[str, Any]]]] = {}
    for e in entries:
        if e.get("type") != "article":
            continue
        venue = e["fields"].get("journal", "").strip()
        if not venue:
            continue
        slug = journal_slug(venue)
        if slug in by_journal:
            by_journal[slug][1].append(e)
        else:
            by_journal[slug] = (venue, [e])
    return by_journal


def write_journal_index(
    out_journal: Path,
    by_journal: dict[str, tuple[str, list[dict[str, Any]]]],
    frontmatter: str | None = None,
) -> None:
    with (out_journal / "index.md").open("w", encoding="utf-8") as fp:
        if frontmatter:
            fp.write("---\n" + frontmatter + "---\n\n")
        fp.write("# By journal\n\n")
        fp.write(
            "`@article` entries from the curated main bib, grouped by "
            "their `journal` field. Sorted by entry count, descending. "
            "Conference proceedings (SEG IMAGE, EAGE Annual, …) are "
            "tracked separately in the [SEG abstracts](../seg/index.md) "
            "and [EAGE abstracts](../eage/index.md) catalogs.\n\n"
        )
        fp.write(f"**{len(by_journal)}** journals covered.\n\n")
        sorted_venues = sorted(
            by_journal.items(),
            key=lambda kv: (-len(kv[1][1]), kv[1][0].lower()),
        )
        for slug, (display, items) in sorted_venues:
            fp.write(f"- [{display}]({slug}.md) — {len(items)} entries\n")


# Independent conference catalogs (see proc_jsonl_to_bib.py).
# Each is a standalone .bib file produced from a Crossref dump,
# rendered as a year-grouped catalog separate from the curated bib.
CATALOG_SOURCES: list[dict[str, str]] = [
    {
        "bib": "bib/seg_abstracts.bib",
        "dir": "seg",
        "title": "SEG Technical Program Expanded Abstracts",
        "blurb": (
            "Conference papers published under DOI prefix `10.1190/` "
            "(Society of Exploration Geophysicists), covering the SEG Annual "
            "Meeting (1982–2020), IMAGE (2021–), and affiliated events "
            "(EXPOGEF, ICE, URTeC, …). Auto-harvested from Crossref — "
            "not hand-curated."
        ),
        "page_intro": (
            "SEG abstracts for this edition. "
            "Auto-harvested from Crossref; full journal versions (if any) "
            "are tracked separately in the main bibliography "
            "(use [Filter](../../filter.md))."
        ),
    },
    {
        "bib": "bib/eage_abstracts.bib",
        "dir": "eage",
        "title": "EAGE Conference Abstracts",
        "blurb": (
            "Conference papers published under DOI prefix `10.3997/` "
            "(European Association of Geoscientists & Engineers), covering the "
            "EAGE Annual Conference & Exhibition, Near Surface Geoscience, "
            "GeoSiberia, Petroleum Geostatistics, IPTC, SBGf, and many other "
            "EAGE-organized or co-sponsored meetings. Auto-harvested from "
            "Crossref — not hand-curated."
        ),
        "page_intro": (
            "EAGE conference abstracts for this edition. "
            "Auto-harvested from Crossref; full journal versions (if any) "
            "are tracked separately in the main bibliography "
            "(use [Filter](../../filter.md))."
        ),
    },
]


def prepare_catalog_section(
    out: Path,
    bib_path: Path,
    dir_name: str,
    title: str,
    blurb: str,
    page_intro: str,
    entries: list[dict[str, Any]] | None = None,
) -> tuple[int, list[tuple]]:
    """Set up a catalog directory & index page, return (count, page_tasks).

    Organises entries by *conference series* (normalised from the booktitle)
    then by year within each series.  Directory layout:

        docs/<dir_name>/
          index.md                    ← lists series with counts
          <series-slug>/
            index.md                  ← lists years for this series
            <year>.md                 ← entries for this series + year

    Also writes ``<dir_name>-papers.json`` (slim, no inline bibtex) at
    the docs root so the filter page can lazy-fetch this catalog.

    The page tasks are returned for batch dispatch via `parallel_write_pages`.
    If the bib doesn't exist, a stub index is written and an empty list
    returned.
    """
    cat_dir = out / dir_name
    cat_dir.mkdir(exist_ok=True)
    if not bib_path.exists():
        with (cat_dir / "index.md").open("w", encoding="utf-8") as fp:
            fp.write("---\n" + SEARCH_EXCLUDE_FM + "---\n\n")
            fp.write(f"# {title}\n\n")
            fp.write(
                f"_Catalog source `{bib_path.as_posix()}` not yet built — "
                "run the corresponding `scripts/harvest_*.py` and "
                "`scripts/proc_jsonl_to_bib.py` to populate it._\n"
            )
        return 0, []

    if entries is None:
        entries = parse_bib(bib_path)

    # Group: series_slug → year → [entries]
    # Also track display_name for each slug (first-seen wins).
    series_display: dict[str, str] = {}
    by_series_year: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for e in entries:
        bt = e["fields"].get("booktitle", "")
        display, slug = normalize_conference_series(bt, dir_name)
        y = e["fields"].get("year", "unknown")
        by_series_year[slug][y].append(e)
        if slug not in series_display:
            series_display[slug] = display

    # Stable order: largest series first (matches user expectation).
    series_order = sorted(
        by_series_year.keys(),
        key=lambda s: -sum(len(v) for v in by_series_year[s].values()),
    )

    tasks: list[tuple] = []
    for slug in series_order:
        by_year = by_series_year[slug]
        display = series_display[slug]
        series_dir = cat_dir / slug
        series_dir.mkdir(exist_ok=True)

        # Series index: lists years newest-first.
        series_total = sum(len(v) for v in by_year.values())
        yrange = (
            f"{min(by_year.keys())}–{max(by_year.keys())}"
            if len(by_year) > 1 else list(by_year.keys())[0]
        )
        with (series_dir / "index.md").open("w", encoding="utf-8") as fp:
            fp.write("---\n" + SEARCH_EXCLUDE_FM + "---\n\n")
            fp.write(f"# {display}\n\n")
            fp.write(
                f"**{series_total:,}** abstracts across "
                f"**{len(by_year)}** edition(s) ({yrange}). "
                "Auto-harvested from Crossref; not hand-curated.\n\n"
            )
            for y in sorted(by_year.keys(), reverse=True):
                fp.write(f"- [{y}]({y}.md) — {len(by_year[y]):,} abstracts\n")

        # Year pages within this series.
        for y, items in by_year.items():
            tasks.append((
                series_dir / f"{y}.md",
                f"{display} — {y}",
                items,
                page_intro,
                SEARCH_EXCLUDE_FM,
            ))

    # Slim per-catalog JSON for the filter page's optional source load.
    write_catalog_papers_json(out, dir_name, entries)

    total = len(entries)
    # Main catalog index: one entry per series, sorted largest first.
    with (cat_dir / "index.md").open("w", encoding="utf-8") as fp:
        fp.write("---\n" + SEARCH_EXCLUDE_FM + "---\n\n")
        fp.write(f"# {title}\n\n")
        fp.write(blurb + "\n\n")
        fp.write(f"**{total:,}** abstracts across **{len(series_order)}** conference series.\n\n")
        for slug in series_order:
            display = series_display[slug]
            by_year = by_series_year[slug]
            series_total = sum(len(v) for v in by_year.values())
            yrange = (
                f"{min(by_year.keys())}–{max(by_year.keys())}"
                if len(by_year) > 1 else list(by_year.keys())[0]
            )
            fp.write(
                f"- [{display}]({slug}/index.md) "
                f"— {series_total:,} abstracts ({yrange})\n"
            )
    return total, tasks


def main() -> int:
    import time
    _t0 = time.time()
    def _tick(msg: str):
        print(f"[+{time.time()-_t0:6.2f}s] {msg}", flush=True)

    if not BIB_FILE.exists():
        print(f"error: {BIB_FILE} not found", file=sys.stderr)
        return 1
    _tick("parse_bib(seismic.bib) start")
    entries = parse_bib(BIB_FILE)
    _tick(f"parse_bib(seismic.bib) done — {len(entries)} entries")
    # Merge any EXTRA_MAIN_BIBS (e.g. openalex_geophysics.bib) into the
    # main entry list. They are treated identically to seismic.bib by
    # downstream code (by-topic / by-year / by-journal / filter).
    for extra_path in EXTRA_MAIN_BIBS:
        if not extra_path.exists():
            continue
        _tick(f"parse_bib({extra_path.name}) start")
        extra = parse_bib(extra_path)
        _tick(f"parse_bib({extra_path.name}) done — {len(extra)} entries")
        entries.extend(extra)
    _tick(f"main bib total: {len(entries)} entries")
    if not entries:
        print("error: no entries", file=sys.stderr)
        return 1

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    out_topic = OUT_DIR / "by-topic"
    out_year = OUT_DIR / "by-year"
    out_journal = OUT_DIR / "by-journal"
    out_topic.mkdir()
    out_year.mkdir()
    out_journal.mkdir()

    # Pre-parse catalog bibs so the same entries can feed BOTH the
    # catalog year pages AND the cross-source by_year / by_journal
    # views (SEG/EAGE conference papers also have year + venue).
    catalog_entries: dict[str, list[dict[str, Any]]] = {}
    for cfg in CATALOG_SOURCES:
        bp = ROOT / cfg["bib"]
        if bp.exists():
            _tick(f"parse_bib({cfg['bib']}) start")
            catalog_entries[cfg["dir"]] = parse_bib(bp)
            _tick(f"parse_bib({cfg['bib']}) done — "
                  f"{len(catalog_entries[cfg['dir']])} entries")
        else:
            catalog_entries[cfg["dir"]] = []

    # `entries` (main bib) is the only source for by_topic — SEG/EAGE
    # entries carry only the generic `seg, expanded-abstract` keyword
    # and would crowd out the curated topic taxonomy.
    # by_year aggregates ALL three sources (SEG/EAGE papers have years).
    # by_journal uses ONLY @article entries from the main bib — the
    # SEG/EAGE conference proceedings get their own catalog views.
    all_entries = entries + sum(catalog_entries.values(), [])

    by_topic: dict[str, list] = defaultdict(list)
    for e in entries:
        raw_kws = split_keywords(e["fields"].get("keywords", ""))
        for kw in normalize_topics(raw_kws):
            by_topic[kw].append(e)
    by_year: dict[str, list] = defaultdict(list)
    for e in all_entries:
        y = e["fields"].get("year", "")
        if y:
            by_year[y].append(e)
    by_journal = build_by_journal(entries)

    # Collect ALL per-entry page write tasks (by-topic, by-year,
    # by-journal, and SEG/EAGE year catalogs) into one batch; dispatch
    # via process pool so 400+ pages don't serialize. Aggregator pages
    # are excluded from full-text search via SEARCH_EXCLUDE_FM so the
    # top-bar search returns at most one hit per paper. They stay
    # reachable via the left nav and section indexes.
    page_tasks: list[tuple] = []
    for kw, items in by_topic.items():
        page_tasks.append((
            out_topic / f"{kw}.md", kw, items, "", SEARCH_EXCLUDE_FM,
        ))
    for y, items in by_year.items():
        page_tasks.append((
            out_year / f"{y}.md", y, items, "", SEARCH_EXCLUDE_FM,
        ))
    for slug, (display, items) in by_journal.items():
        page_tasks.append((
            out_journal / f"{slug}.md", display, items, "", SEARCH_EXCLUDE_FM,
        ))
    write_topic_index(out_topic, by_topic, frontmatter=SEARCH_EXCLUDE_FM)
    write_year_index(out_year, by_year, frontmatter=SEARCH_EXCLUDE_FM)
    write_journal_index(out_journal, by_journal, frontmatter=SEARCH_EXCLUDE_FM)

    page_tasks.append((
        OUT_DIR / "recent.md",
        "Recent additions",
        sort_entries(entries)[:50],
        "Newest 50 entries, sorted by year then cite-key.",
        SEARCH_EXCLUDE_FM,
    ))
    # NOTE: there is intentionally no `all.md` — at 6700+ entries it
    # rendered to ~30 MB of HTML and froze browsers. Use `filter.md`
    # (live keyword/year/text filter) or per-topic / per-year pages.

    write_papers_json(OUT_DIR, entries)
    _tick("write_papers_json done")
    write_copy_assets(OUT_DIR)
    _tick("write_copy_assets done")

    catalog_totals: dict[str, int] = {}
    for cfg in CATALOG_SOURCES:
        _tick(f"prepare_catalog_section({cfg['dir']}) start")
        total, cat_tasks = prepare_catalog_section(
            OUT_DIR,
            ROOT / cfg["bib"],
            cfg["dir"],
            cfg["title"],
            cfg["blurb"],
            cfg["page_intro"],
            entries=catalog_entries.get(cfg["dir"]),
        )
        _tick(f"prepare_catalog_section({cfg['dir']}) done — "
              f"{total} entries, {len(cat_tasks)} tasks")
        catalog_totals[cfg["dir"]] = total
        page_tasks.extend(cat_tasks)

    # Filter page and landing both need catalog_totals — write them now.
    _tick("write_filter_page start")
    write_filter_page(OUT_DIR, entries, catalog_totals)
    _tick("write_filter_page done")

    # Final landing depends only on counts — write before pool dispatch
    # so it's in place even if a worker errors out.
    write_landing(
        OUT_DIR, len(entries), len(by_topic), len(by_year),
        len(by_journal), catalog_totals,
    )

    _tick(f"dispatching {len(page_tasks)} entry-page writes to "
          f"{min(PAGE_WORKERS, len(page_tasks))} workers")
    parallel_write_pages(page_tasks)
    _tick("parallel_write_pages done")

    copy_with_header(
        ROOT / "CONTRIBUTING.md",
        OUT_DIR / "contributing.md",
        "Contributing",
        link_rewrites={"TAXONOMY.md": "taxonomy.md"},
        frontmatter=SEARCH_EXCLUDE_FM,
    )
    copy_with_header(
        ROOT / "TAXONOMY.md",
        OUT_DIR / "taxonomy.md",
        "Taxonomy",
    )

    cat_summary = ", ".join(
        f"{d}={n}" for d, n in catalog_totals.items()
    )
    print(
        f"generated docs/: {len(entries)} entries, {len(by_topic)} topic pages, "
        f"{len(by_year)} year pages, {len(by_journal)} journal pages, "
        f"plus index/recent/filter/contributing/taxonomy + papers.json; "
        f"catalogs: {cat_summary}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
