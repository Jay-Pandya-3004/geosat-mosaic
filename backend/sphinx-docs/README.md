# Global Mosaic -- Sphinx API docs

Auto-generated HTML API reference for the `global_mosaic` package
(`backend/src/global_mosaic/`) and the standalone CLI scripts
(`backend/scripts/`), built from the Google-style docstrings already in
that code via `sphinx.ext.autodoc` + `sphinx.ext.napoleon`.

## Rebuild

From this directory (`backend/sphinx-docs/`):

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-docs.txt
.venv\Scripts\python -m sphinx.cmd.build -b html source build/html
```

(or, equivalently, `.venv\Scripts\sphinx-build -b html source build/html`)

Open `build/html/index.html` in a browser.

## Notes

- `source/conf.py` adds `../../src` and `../../scripts` to `sys.path` so
  both `global_mosaic` and the standalone scripts are importable by
  autodoc without installing the package.
- Heavy third-party dependencies (satpy, pyresample, xarray, dask, zarr,
  s3fs, boto3, eumdac, cartopy, matplotlib, netCDF4, h5py, openpyxl,
  pyorbital) are listed in `autodoc_mock_imports` in `conf.py` and are
  **not** installed -- Sphinx substitutes stand-in objects for them so it
  can still introspect signatures and docstrings without a working import
  chain. Lightweight deps (requests, numpy, pyyaml, python-dotenv,
  Pillow) are installed for real instead, see `requirements-docs.txt`.
- `source/api/*.rst` were generated with `sphinx-apidoc` for the
  `global_mosaic` package (one page per subpackage: acquisition,
  satellites, processing, readers, plus a top-level page for `config.py`)
  and hand-written for `scripts/` (not a package, so apidoc can't target
  it directly).
- A handful of `sphinx-build` warnings are expected and acceptable:
  duplicate-object-description warnings for dataclasses that document
  their fields in both an `Attributes:` docstring section and via
  autodoc's own field introspection, and a few docutils RST-parsing
  errors confined to specific docstrings with inline markup Sphinx can't
  parse (e.g. stray underscores/backticks) -- these do not fail the
  build, they just mean that one paragraph renders slightly oddly on an
  otherwise-complete page.
