# Configuration file for the Sphinx documentation builder.
#
# This project documents two independent services that live side by side
# (services/):
#
#   - xcube/     : a set of monkeypatches + operational scripts layered on
#                   top of the third-party `xcube` tile-server library.
#   - fastapi/   : geosat-mosaic-api, a small standalone FastAPI service.
#
# Every documented function/class/route already has a real Google-style
# docstring in the source -- this file only wires Sphinx/autodoc/napoleon
# around them. See ../README.md for how to rebuild these docs.

from __future__ import annotations

import pathlib
import sys

# -- Path setup --------------------------------------------------------

# Both service directories are added directly to sys.path (not imported
# as sub-packages of a parent package) so their modules are importable
# by their bare names, e.g. `import async_datasets_patch`, `import main`.
# Neither directory has (or needs) an __init__.py: autodoc only needs the
# individual .py files to be importable as top-level modules, which plain
# sys.path membership already provides.
_ROOT = pathlib.Path(__file__).parent.resolve()
sys.path.insert(0, str(_ROOT / ".." / "xcube"))
sys.path.insert(0, str(_ROOT / ".." / "fastapi"))

# -- Import-time side effect workaround ---------------------------------
#
# fastapi/main.py (and fastapi/prewarm_minmax.py) each read a shared
# secret from disk at *module import time*:
#
#     _INTERNAL_SECRET = Path("/opt/geosat-mosaic-api/.internal-secret").read_text().strip()
#
# That path only exists on the real deployment box. Without it, autodoc's
# import of `main` (needed to document its FastAPI routes) would crash
# the whole build. We do not touch the source files to work around this
# (the task is to document them as-is) -- instead we monkeypatch
# `pathlib.Path.read_text` here, in this docs-only conf.py, to hand back
# a harmless dummy value for that one specific filename, and fall through
# to the real implementation for everything else. This only affects the
# Sphinx build process, never the real services.
_real_read_text = pathlib.Path.read_text


def _mock_read_text(self, *args, **kwargs):
    if self.name == ".internal-secret":
        return "sphinx-docs-mock-secret\n"
    return _real_read_text(self, *args, **kwargs)


pathlib.Path.read_text = _mock_read_text

# -- Project information -------------------------------------------------

project = "GeoSat-MOSAIC Services"
copyright = "2026, GeoSat-MOSAIC project"
author = "GeoSat-MOSAIC project"
release = "1.0"

# -- General configuration ------------------------------------------------

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", ".venv", "**/.venv/**"]

# -- Napoleon (Google-style docstrings) -----------------------------------

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True
napoleon_use_rtype = False
# Renders a docstring's "Attributes:" section as an :ivar: field list
# instead of separate `.. py:attribute::` blocks. Needed here specifically
# because main.py's pydantic request-model classes (TimeseriesRequest,
# PruneCacheRequest) both document their fields in an Attributes: section
# AND have those same fields autodoc-discoverable as real annotated class
# attributes -- without this, the two produce duplicate object-description
# warnings for the same fully-qualified name.
napoleon_use_ivar = True

# -- Autodoc ---------------------------------------------------------------

# `xcube` itself (the large third-party geospatial tile-server library)
# and `tornado` are never actually imported at *module* level by any of
# the 5 files under ../xcube -- every `from xcube...` / tornado-adjacent
# import happens lazily inside function bodies (`apply()`, `main()`),
# which autodoc never executes. So documenting those 5 modules does not
# actually require xcube/tornado to be installed at all. They're still
# listed here defensively, per the task's instruction to mock rather than
# install the heavy xcube stack -- if a future edit ever moves one of
# those imports to module level, the build keeps working instead of
# breaking.
autodoc_mock_imports = ["xcube", "tornado"]

autodoc_default_options = {
    "members": True,
    "undoc-members": True,
    "show-inheritance": True,
    "member-order": "bysource",
}
autodoc_typehints = "description"

# -- Options for HTML output -----------------------------------------------

html_theme = "furo"
html_static_path = ["_static"]
