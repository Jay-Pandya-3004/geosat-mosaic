## Summary

What does this change do, and why? Link the issue it resolves, if any (e.g. `Fixes #123`).

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] New satellite / reader support
- [ ] Documentation only
- [ ] Refactor / internal cleanup (no behavior change)
- [ ] Other:

## Area(s) affected

- [ ] `backend/` (acquisition, processing, scripts)
- [ ] `services/` (THREDDS, xcube, FastAPI, nginx)
- [ ] `frontend/geosat-mosaic-cesium`
- [ ] `frontend/geosat-mosaic-openlayers`
- [ ] `frontend/geosat-gallery`
- [ ] `docs/` / `wiki/`

## How was this tested?

Describe what you actually did to verify the change — which pytest tests (new or existing), which satellite/timestamp you ran the pipeline against, `npx tsc -b --noEmit`, and/or manual testing in a running app. "It compiles" is not a test plan.

## Checklist

- [ ] I've read [CONTRIBUTING.md](../CONTRIBUTING.md)
- [ ] New/changed public functions have Google-style docstrings (Python) or JSDoc (TypeScript) where their behavior isn't obvious from the signature
- [ ] Relevant docs under `docs/` (and `wiki/` for deeper design changes) updated for any user-facing or pipeline behavior change
- [ ] Existing tests pass (`pytest` for backend, `npx tsc -b --noEmit` for frontend), and a new test was added for a bug fix or new pure-logic behavior where practical
- [ ] If this touches a satellite's radiometric correction, I've read the reasoning in [wiki/Data-Processing-Pipeline.md](../wiki/Data-Processing-Pipeline.md) §12 first

## Screenshots / video (if applicable)

For any frontend change, a before/after screenshot or short recording makes review much faster.
