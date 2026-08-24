# services Sphinx docs

API documentation generated from the Google-style docstrings already
present in `../xcube/*.py` and `../fastapi/*.py`. This directory only
contains the Sphinx project (config + `.rst` pages) -- no docstrings live
here, and the source files under `../xcube/` and `../fastapi/` are not
modified by this build.

## Rebuild

```bash
cd services/sphinx-docs
python3 -m venv .venv
source .venv/bin/activate       # Linux/macOS; use .venv\Scripts\activate on native Windows
pip install -r requirements-docs.txt
sphinx-build -b html . _build/html
```

Open `_build/html/index.html` in a browser.

## Notes

- `xcube` (the third-party tile-server library) and `tornado` are **not**
  installed -- they're listed in `conf.py`'s `autodoc_mock_imports`
  instead. None of the 5 files under `../xcube/` actually import them at
  module level (only lazily, inside `apply()`/`main()` function bodies at
  process-startup time), so this is a formality more than a strict
  requirement, kept in case a future edit moves one of those imports to
  module scope.
- `fastapi/main.py` (and `prewarm_minmax.py`) read a shared secret from
  `/opt/geosat-mosaic-api/.internal-secret` at import time. That path
  doesn't exist off the real deployment box, so `conf.py` monkeypatches
  `pathlib.Path.read_text` to hand back a dummy value for that one
  filename during the docs build only -- the real source files are
  untouched.
- This requires a real Python 3 installation (a Windows box with only the
  Microsoft Store app-execution alias stub will need Python installed
  properly, or the build run from a Linux/WSL2/macOS environment instead).
