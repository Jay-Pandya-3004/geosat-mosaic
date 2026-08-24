# Contributing to Global Mosaic

Thanks for considering a contribution. This document covers how the project is organized, the conventions the existing code follows, and the practical steps for submitting a change.

## Before you start

- For anything beyond a small fix (a new satellite/reader, a new frontend feature, a change to how the pipeline or a service behaves), please open an issue first to discuss the approach. It's much easier to agree on a design before code is written than to rework a finished pull request.
- For a small, obvious fix (a typo, a broken link, a null-check), a pull request without a preceding issue is fine.
- Check open issues and pull requests first so you're not duplicating in-progress work.

## Development setup

This repo has three independent parts — a Python backend, a services layer, and three React frontend apps — that can be worked on separately.

**Backend:**

1. `cd backend`, create a virtualenv, and `pip install -r requirements.txt` (Python ≥3.10 required).
2. Copy `.env.example` to `.env` and fill in credentials for any satellite source you need (EUMETSAT `eumdac`, MOSDAC for INSAT — GOES and Himawari are public S3 buckets and need no credentials).
3. See [docs/backend/](docs/backend/) for what each module does; [docs/backend/05-automation-and-scripts.md](docs/backend/05-automation-and-scripts.md) covers the CLI drivers and scheduler.

**Services:**

- See [docs/services/](docs/services/) for THREDDS, xcube, and the FastAPI metadata service — each has its own setup notes.

**Frontend:**

1. `cd frontend/<app>` (one of `geosat-mosaic-cesium`, `geosat-mosaic-openlayers`, `geosat-gallery`), then `npm install`.
2. Backend base URLs are **not** read from `.env` — `src/lib/runtimeBackendConfig.ts` in each app returns fixed base URLs for THREDDS/xcube/`geosat-mosaic-api`. Edit that file directly to point the app at your own running services.
3. `npm run dev` for a hot-reloading dev server, `npm run build` to produce the static `dist/` build the production apps actually serve (source edits alone don't go live without a build).

## Project structure

- `backend/src/global_mosaic/acquisition/` — per-satellite-family download logic (GOES, Himawari, Meteosat, INSAT).
- `backend/src/global_mosaic/satellites/` — satellite metadata, band/reader registries, cross-satellite radiometric (SRF) correction.
- `backend/src/global_mosaic/processing/` — the mosaic algorithm itself: timing, geometry, reprojection, source selection, blending, tiling, mosaic I/O.
- `backend/scripts/` — CLI drivers (`run_phase0.py`, `run_phase2_tier.py`), the production scheduler (`schedule_pipeline.py`), retention, and one-off calibration tools.
- `backend/tests/` — pytest tests, mostly pure-logic/regression tests against synthetic or cached real data; a few `integration`-marked tests need already-downloaded raw satellite files and are skipped automatically otherwise.
- `services/` — THREDDS, xcube, the FastAPI metadata service, nginx tile-cache config, and Windows deployment scripts.
- `frontend/geosat-mosaic-cesium/`, `frontend/geosat-mosaic-openlayers/` — the two live-map viewer apps (3D globe and 2D map respectively), same backend, different mapping library.
- `frontend/geosat-gallery/` — the pre-rendered-thumbnail slideshow viewer.
- `docs/` — the documentation index ([docs/README.md](docs/README.md)) and per-module deep-dive pages.
- `wiki/` — longer background pages (e.g. the data-processing pipeline and SRF-correction derivation) that don't fit the per-module docs.

## Coding conventions

The existing code is fairly consistent about a few things — please match them rather than introducing a new style:

- **Comment the why, not the what.** Identifiers are named to make the "what" self-evident; comments are reserved for non-obvious reasoning — a hidden constraint, a workaround, why a fallback is safe, why an ordering matters. If a comment just restates the code in prose, it should probably be deleted instead.
- **Google-style docstrings on Python functions/classes** (`Args:`/`Returns:`/`Raises:`), and **JSDoc (`@param`/`@returns`) on exported TypeScript functions** whose behavior isn't obvious from their signature.
- **One responsibility per module.** Acquisition, satellite metadata, and processing are kept in separate packages under `backend/src/global_mosaic/` on purpose — prefer adding a new small module over growing an existing one to cover an unrelated concern.
- **Radiometric correctness is load-bearing.** Anything touching `satellites/srf_correction.py`, `srf_convolution.py`, or `srf_reflectance.py` affects cross-satellite calibration — see [wiki/Data-Processing-Pipeline.md](wiki/Data-Processing-Pipeline.md) §12 for how the correction coefficients were derived before changing how they're applied.
- **Frontend apps share a pattern, not code.** `geosat-mosaic-cesium` and `geosat-mosaic-openlayers` intentionally duplicate equivalent components (`AppInfoPanel.tsx`, `LayerSwitcher.tsx`, etc.) rather than sharing a package — if you fix a bug or add a feature in one, check whether the sibling app needs the same fix.

## Tests

- Backend: `cd backend && pytest`. A bug fix should come with a regression test where practical, especially for pure-logic code (timing, geometry, reprojection, selection) that doesn't need real satellite data to test — see `backend/tests/test_source_side_cropping.py` for the style (synthetic fixtures, exact-equivalence assertions against a known-correct reference).
- Frontend: there's no automated test suite yet. Verify a change with `npx tsc -b --noEmit` (type-check) and by actually running the app (`npm run dev`) against a live or locally-running backend.

## Submitting a change

1. Fork the repository and create a branch off `main` named for what it does, e.g. `fix/insat-midtime-selection` or `feature/gallery-fullscreen`.
2. Keep commits focused — one logical change per commit, with a message explaining *why* the change was made, not just what changed.
3. Update the relevant page(s) under `docs/` (and `wiki/` if it's a deeper design change) if your change adds, removes, or changes user-facing or pipeline behavior.
4. Open a pull request against `main`, filling in the pull request template. Link the issue it resolves, if any.
5. Be responsive to review feedback — a PR that goes quiet for a long time may be closed and can always be reopened once it's picked back up.

## Reporting bugs and requesting features

Please use the issue templates rather than a blank issue — they ask for exactly the information needed to act on a report quickly (which app/component, repro steps, environment, expected vs. actual behavior for bugs; the problem being solved and why for feature requests).

## Code of conduct

Be respectful and assume good faith. Disagreements about approach are normal and welcome — personal attacks, harassment, or bad-faith arguing are not, and will get a contributor removed from the discussion.
