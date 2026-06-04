# seisbib

A curated, machine-checked bibliography spanning the **seismic / seismology
research workflow** — acquisition, processing, modeling, imaging
(RTM / LSRTM / Kirchhoff / beam), inversion (FWI / tomography / AVO),
interpretation, rock physics, 4D monitoring, microseismic, ambient noise,
near-surface, and earthquake studies. Published as a searchable MkDocs
site so you can browse, filter, and copy cite-keys from the browser.

**:globe_with_meridians: Live site — <https://geophyai.github.io/seisbib/>**

[![deploy](https://github.com/GeophyAI/seisbib/actions/workflows/deploy.yml/badge.svg)](https://github.com/GeophyAI/seisbib/actions/workflows/deploy.yml)
[![validate](https://github.com/GeophyAI/seisbib/actions/workflows/validate.yml/badge.svg)](https://github.com/GeophyAI/seisbib/actions/workflows/validate.yml)
[![License: CC BY 4.0](https://img.shields.io/badge/License-CC%20BY%204.0-lightgrey.svg)](LICENSE)

## What's in the repo

| Path | Purpose |
|---|---|
| `bib/seismic.bib` | The single source of truth — every entry, once |
| `TAXONOMY.md` | Controlled vocabulary (Topic / Method / Physics / ML / …) |
| `scripts/generate.py` | Builds `docs/` from the bib |
| `scripts/validate.py` | Checks DOI uniqueness, required fields, keyword membership, ≥1 Topic per entry |
| `mkdocs.yml` | MkDocs (Material theme) configuration |
| `docs/` | **Generated** (gitignored); rebuilt by CI on every push |

## Scope

Topics covered (see [`TAXONOMY.md`](TAXONOMY.md) for the full controlled
vocabulary):

`acquisition` · `processing` · `modeling` · `imaging` · `inversion` ·
`interpretation` · `rock-physics` · `monitoring` (4D) · `microseismic` ·
`ambient-noise` · `near-surface` · `earthquake` · `computing` · `ml`

Each entry is tagged with a **Topic** (workflow stage), a **Method**
(specific technique like `fwi`, `rtm`, `lsrtm`, `denoising`, …), plus
optional axes for physics, ML architecture, dataset, etc.

## Use the bib in your manuscript

```bash
curl -O https://raw.githubusercontent.com/GeophyAI/seisbib/main/bib/seismic.bib
```

Cite-keys follow `firstauthor_keyword_year`, e.g.
`\cite{he_reparameterized_2021}`.

## Contribute

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the entry format, cite-key
rules, and Zotero / BetterBibTeX workflow.

In short:

1. Add an `@article{...}` block to `bib/seismic.bib`.
2. Tag it with ≥1 Topic + ≥1 Method (when applicable) from `TAXONOMY.md`.
3. Run `python scripts/validate.py` — must print `ok`.
4. Open a PR. CI re-runs the validator.

## Build the site locally

```bash
pip install -r requirements-docs.txt
python scripts/validate.py
python scripts/generate.py
mkdocs serve            # http://127.0.0.1:8000
```

`mkdocs serve` watches `docs/` and `mkdocs.yml`. After editing
`bib/seismic.bib`, re-run `python scripts/generate.py` to refresh `docs/`.

## Deployment

`.github/workflows/deploy.yml` runs on every push to `main`: validates,
regenerates `docs/`, builds with `mkdocs build --strict`, and publishes
to GitHub Pages.

**One-time repo setup:** Settings → Pages → Build and deployment →
Source: **GitHub Actions**.

## License

Bibliography content: [CC BY 4.0](LICENSE). Code in `scripts/`:
same license, no attribution required.
