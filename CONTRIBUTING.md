# Contributing to seisbib

Thanks for helping grow this seismic bibliography! Contributions = pull
requests that add or amend entries in `bib/seismic.bib`.

## TL;DR

1. Fork the repo, create a branch.
2. Add or edit entries in `bib/seismic.bib` following the conventions below.
3. Run `python scripts/validate.py` until it prints `ok`.
4. Open a PR. CI will rerun the validator; passing is required to merge.
5. After the PR is merged, the `papers/` views are regenerated automatically.

You do **not** need to edit anything under `papers/`. It is fully derived
from `bib/seismic.bib` by `scripts/generate.py`.

## Entry format

```bibtex
@article{he_reparameterized_2021,
  author     = {He, Qinglong and Wang, Yanfei},
  title      = {Reparameterized full-waveform inversion using deep neural networks},
  journal    = {Geophysics},
  year       = {2021},
  volume     = {86},
  number     = {1},
  pages      = {V1--V13},
  doi        = {10.1190/geo2019-0382.1},
  keywords   = {nnfwi, reparameterization, dip, cnn, acoustic, time-domain},
  annotation = {Velocity model is the output of a CNN; CNN weights are the optimization variables.},
}
```

### Required fields

- `author` — `Last, First and Last, First and ...`
- `title`
- `year`
- `journal` (for `@article`) or `booktitle` (for `@inproceedings`)
- `doi` *or* `url` (DOI strongly preferred)
- `keywords` — comma-separated, values must come from
  [`TAXONOMY.md`](TAXONOMY.md). **Must include ≥1 Topic** keyword
  (workflow stage like `inversion`, `modeling`, `imaging`, …) and
  **≥1 Method** keyword when one applies (`fwi`, `rtm`, `lsrtm`,
  `denoising`, …). CI enforces this.

### Optional fields

`volume`, `number`, `pages`, `month`, `annotation`.

`annotation` is a one- to two-sentence summary in your own words. It shows up
under the entry in the generated markdown views.

### cite-key convention

`firstauthorlastname_shortkeyword_year`, all lowercase, no hyphens. Examples:

- `he_reparameterized_2021`
- `sun_ifwi_2023`
- `dhara_elastic_2023`

The cite-key year must match the `year` field. The validator enforces this.

### Picking keywords

Open [`TAXONOMY.md`](TAXONOMY.md). For each relevant dimension (physics,
data domain, optimizer, …), pick the one or more keywords that apply.
Most FWI papers end up with 4–8 keywords.

If the keyword you want is not in `TAXONOMY.md`, **add it in the same PR**
(extend the table; include a short definition). The validator rejects any
keyword that is not in the taxonomy file.

## Zotero + BetterBibTeX workflow

If you keep your library in Zotero, you can author entries there and export
to fwibib's format.

### One-time setup

1. Install [BetterBibTeX](https://retorque.re/zotero-better-bibtex/).
2. In Zotero preferences → BetterBibTeX → Citation keys, set the format to:

   ```
   [auth:lower]_[veryshorttitle:lower]_[year]
   ```

3. In Zotero preferences → BetterBibTeX → Export, enable
   *"Apply title-casing to titles"* off (keep BibTeX-natural casing).

### Adding a paper

1. Save the paper in Zotero (browser connector pulls DOI metadata).
2. Add Zotero **Tags** that exist in [`TAXONOMY.md`](TAXONOMY.md) — they
   become the `keywords` field on export.
3. In the item's **Extra** field, add one line:

   ```
   tex.annotation: One or two sentences in your own words.
   ```

   BetterBibTeX renders `tex.annotation:` as the `annotation` field in
   the exported `.bib`.
4. Right-click → *Export Items* → *Better BibTeX*. Copy the entry into
   `bib/seismic.bib`.
5. Run `python scripts/validate.py` and open a PR.

### Importing fwibib into your Zotero

File → Import → select `bib/seismic.bib`. `keywords` map to Zotero Tags.

## Style

- BibTeX-style `journal` and `year` (not BibLaTeX's `journaltitle` / `date`)
  so Zotero exports drop straight in.
- Use `--` for page ranges (`V1--V13`).
- Use `{}` to protect proper nouns and accents (e.g. `Sch{\"o}nlieb`).
- One blank line between entries.

## Code of conduct

Be kind. PRs that add good entries get merged; PRs that pile on style
bikeshedding without adding content do not.
