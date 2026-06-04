# Taxonomy

Every entry in `bib/seismic.bib` **must** carry at least one keyword from
**A. Topic** and at least one from **B. Method** (when a recognisable method
applies). Other axes are optional but encouraged when relevant. CI rejects
any keyword that is not in this file — to add one, open a PR that edits
this table together with the entries that need it.

Use kebab-case, comma-separated inside the `keywords = {...}` field.

---

## A. Topic — the workflow stage (**≥ 1 required**)

| Keyword | Meaning |
|---|---|
| `acquisition` | Survey design, sensor / acquisition technology |
| `processing` | Pre-imaging signal processing (denoise, multiple removal, statics, …) |
| `modeling` | Forward simulation of the seismic wavefield |
| `imaging` | Migration to produce structural images (Kirchhoff, RTM, LSRTM, beam, …) |
| `inversion` | Quantitative model estimation (FWI, tomography, AVO inversion, …) |
| `interpretation` | Attribute analysis, geobody / fault / horizon picking |
| `rock-physics` | Rock-physics models, fluid / lithology, pore pressure |
| `monitoring` | Time-lapse / 4D / reservoir monitoring |
| `microseismic` | Passive seismic: event detection / location / mechanism |
| `ambient-noise` | Noise interferometry, surface-wave tomography from noise |
| `near-surface` | Engineering / environmental seismic; GPR; MASW; shallow refraction |
| `earthquake` | Earthquake source, regional/global tomography, receiver function |
| `computing` | HPC, GPU, frameworks, code releases (paired with another topic) |
| `ml` | ML / AI methodology paper for any seismic task (paired with another topic) |

---

## B. Method — the specific technique (**≥ 1 required when one applies**)

### B.1 Imaging / Migration
`rtm`, `lsrtm`, `kirchhoff`, `beam-migration`, `oneway-migration`,
`prestack-migration`, `poststack-migration`, `q-rtm`, `time-migration`,
`depth-migration`, `cig`, `mva`, `elastic-migration`

### B.2 Inversion
`fwi`, `nnfwi`, `efwi`, `vti-fwi`, `tti-fwi`,
`traveltime-tomography`, `slope-tomography`, `reflection-tomography`,
`wave-equation-tomography`, `adjoint-tomography`,
`surface-wave-tomography`, `receiver-function`,
`joint-inversion`, `bayesian-inversion`, `monte-carlo-inversion`,
`prestack-inversion`, `poststack-inversion`,
`avo-inversion`, `ava-inversion`, `geostatistical-inversion`

### B.3 Processing
`denoising`, `demultiple`, `srme`, `epsi`, `radon-demultiple`,
`deconvolution`, `predictive-deconvolution`,
`interpolation`, `regularization-processing`,
`statics`, `residual-statics`,
`nmo`, `dmo`, `velocity-analysis`, `semblance`,
`deghosting`, `wavefield-separation`,
`first-break-picking`

### B.4 Modeling / Numerical
`finite-difference`, `staggered-grid`, `lebedev-grid`, `rotated-grid`,
`finite-element`, `discontinuous-galerkin`, `spectral-element`,
`pseudospectral`, `boundary-element`,
`ray-tracing`, `eikonal`, `gaussian-beam`,
`pml`, `cpml`, `absorbing-bc`, `free-surface-bc`

### B.5 Interpretation / Attributes
`attribute`, `coherence`, `curvature`, `dip-attribute`,
`spectral-decomposition`, `time-frequency`,
`geobody`, `fault-picking`, `horizon-picking`,
`salt-segmentation`, `well-tie`

### B.6 Rock Physics
`gassmann`, `dem-rockphysics`, `kuster-toksoz`,
`avo`, `ava`, `avoa`,
`pore-pressure`, `lithology-fluid`, `eei`

### B.7 4D / Monitoring
`time-lapse`, `4d-inversion`, `4d-fwi`

### B.8 Microseismic / Earthquake source
`event-detection`, `event-location`, `hypocenter-location`,
`moment-tensor`, `source-mechanism`, `focal-mechanism`,
`rupture-imaging`, `back-projection`

### B.9 Ambient noise / surface waves
`noise-interferometry`, `green-function-retrieval`, `masw`

### B.10 Acquisition / Hardware
`survey-design`, `compressive-sensing`, `acquisition-geometry`

---

## C. Physics / Wave equation
`acoustic`, `elastic`, `viscoelastic`,
`anisotropic-vti`, `anisotropic-tti`, `anisotropic-ortho`,
`multiparameter`, `poroelastic`

## D. Data domain
`time-domain`, `frequency-domain`, `laplace-domain`

## E. Numerical scheme / Boundary
`pml`, `cpml`, `sbp-sat`, `absorbing-bc`, `free-surface-bc`,
`high-order-fd`, `mimetic`, `time-integration`, `runge-kutta`

## F. Misfit function (inversion)
`l2`, `wasserstein`, `envelope`, `awi`, `cross-correlation`,
`phase`, `instantaneous`, `optimal-transport`, `huber`,
`adaptive`, `mva-misfit`

## G. Optimizer
`gradient-descent`, `gauss-newton`, `lbfgs`, `truncated-newton`,
`adam`, `stochastic`, `levenberg-marquardt`, `nonlinear-cg`

## H. Regularization / Prior
`tikhonov`, `tv`, `sparse`, `structure-oriented`,
`dip`, `inr`, `pinn`,
`diffusion-prior`, `gan-prior`, `learned-prior`

## I. Acceleration / Trick
`source-encoding`, `random-shot`, `mini-batch`,
`multiscale`, `cycle-skipping`,
`mixed-precision`, `gpu`, `cuda`, `mpi`, `hpc`,
`checkpointing`, `boundary-saving`

## J. ML / AI architecture
`cnn`, `unet`, `transformer`, `mlp`, `rnn`, `gnn`,
`gan`, `diffusion-model`, `vae`, `encoder-decoder`, `autoencoder`,
`foundation-model`, `neural-operator`, `fno`, `deeponet`

## K. ML / AI paradigm
`supervised`, `self-supervised`, `unsupervised`,
`physics-informed`, `physics-guided`,
`surrogate`, `reparameterization`, `end-to-end`,
`transfer-learning`, `domain-adaptation`,
`uncertainty-quantification`, `active-learning`

## L. Acquisition geometry
`marine`, `land`, `crosswell`, `vsp`, `obn`, `obc`, `streamer`,
`das`, `borehole`, `surface-array`

## M. Application
`oil-gas`, `co2-storage`, `geothermal`, `mining`,
`hydrology`, `volcanology`, `glaciology`,
`gpr`, `civil-engineering`, `archaeology`, `nuclear-monitoring`

## N. Dataset / Benchmark
`marmousi`, `marmousi2`, `overthrust`, `bp2004`, `bp-tti`,
`seam`, `sigsbee`, `hess-vti`, `chevron`,
`volve`, `glesi`, `ekofisk`,
`prism-comet`, `synthetic`, `field-data`

## O. Type / Style
`review`, `tutorial`, `survey`, `benchmark`,
`theory`, `application`, `software`, `open-source`

## P. Misc
`1d`, `2d`, `3d`, `comparison`, `reproducible`
