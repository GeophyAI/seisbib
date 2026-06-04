/* ========================================================================
   seisbib — MkDocs Material skin · client-side enhancements
   Drop into docs/assets/extra.js (or wherever your extra_javascript points)

   What this does:
   - Wires up a `Copy BibTeX` button on any element with [data-bibtex]
   - Shows a toast on copy success
   - Adds .is-in-range class on year-histogram bars when their data-year
     falls inside a [data-year-range] container's min/max
   - Tag chips with [data-tag-link] navigate to the filter page
   ======================================================================== */

(function () {
  "use strict";

  /* ---------- toast ---------- */
  let toastEl = null;
  let toastTimer = null;
  function ensureToast() {
    if (toastEl) return toastEl;
    toastEl = document.createElement("div");
    toastEl.className = "sb-toast";
    document.body.appendChild(toastEl);
    return toastEl;
  }
  function showToast(msg) {
    const el = ensureToast();
    el.textContent = msg;
    el.classList.add("is-on");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("is-on"), 1700);
  }
  window.sbToast = showToast;

  /* ---------- copy to clipboard ---------- */
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(text); return true; } catch (_) {}
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (_) {}
    document.body.removeChild(ta);
    return ok;
  }

  /* ---------- BibTeX copy wiring ---------- */
  // Two supported markup patterns:
  //   1. <button data-bibtex="@article{key,...}">BibTeX</button>
  //   2. <button data-bibtex-target="#bibtex-key123">BibTeX</button>
  //      where the target contains a <pre> or any element with .textContent
  function bindCopyButtons(root) {
    (root || document).querySelectorAll(
      "[data-bibtex], [data-bibtex-target]"
    ).forEach((btn) => {
      if (btn.__sbBound) return;
      btn.__sbBound = true;
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        let text = btn.getAttribute("data-bibtex");
        if (!text) {
          const sel = btn.getAttribute("data-bibtex-target");
          const target = sel && document.querySelector(sel);
          text = target ? (target.innerText || target.textContent) : "";
        }
        if (!text) return;
        const ok = await copyText(text);
        const key = btn.getAttribute("data-bibkey") || "";
        showToast(ok
          ? (key ? `BibTeX copied · @${key}` : "BibTeX copied to clipboard")
          : "Copy failed — select & ⌘C the text"
        );
        // micro-visual feedback
        const orig = btn.innerHTML;
        btn.setAttribute("data-was", orig);
        btn.innerHTML = `<svg class="sb-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="5 12 10 17 20 7"></polyline></svg> Copied`;
        setTimeout(() => { btn.innerHTML = orig; }, 1400);
      });
    });
  }

  /* ---------- year histogram in-range painting ---------- */
  function paintHistogram(root) {
    (root || document).querySelectorAll("[data-year-range]").forEach((wrap) => {
      const [from, to] = (wrap.getAttribute("data-year-range") || "").split("-").map(Number);
      wrap.querySelectorAll(".sb-hist__bar").forEach((bar) => {
        const y = +bar.getAttribute("data-year");
        bar.classList.toggle("is-in-range", from && to && y >= from && y <= to);
      });
    });
  }

  /* ---------- tag → filter page navigation ---------- */
  function bindTagLinks(root) {
    (root || document).querySelectorAll("[data-tag-link]").forEach((el) => {
      if (el.__sbTagBound) return;
      el.__sbTagBound = true;
      el.addEventListener("click", (e) => {
        const tag = el.getAttribute("data-tag-link");
        if (!tag) return;
        e.preventDefault();
        // navigate to /filter/?kw=<tag>; the filter page can read the param
        const base = el.getAttribute("data-tag-base") || "/filter/";
        location.href = `${base}?kw=${encodeURIComponent(tag)}`;
      });
    });
  }

  /* ---------- init + MkDocs Material's instant nav rebind ---------- */
  function initAll() {
    bindCopyButtons();
    paintHistogram();
    bindTagLinks();
  }
  initAll();

  // Material's instant navigation re-runs the script with a fresh DOM —
  // also subscribe to its document$ stream if available (Material >= 9).
  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(initAll);
  } else {
    document.addEventListener("DOMContentLoaded", initAll);
  }
})();
