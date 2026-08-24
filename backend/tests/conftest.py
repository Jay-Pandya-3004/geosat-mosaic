"""Shared pytest setup.

`global_mosaic` is installed editable (`pip install -e .` from backend/)
so it imports normally in tests without sys.path hacks -- unlike the
scripts/ entry points, which still insert sys.path manually since they're
meant to be run standalone without requiring an editable install first.
"""

from __future__ import annotations

import os
from pathlib import Path

# So any test that touches satpy's Scene/reader machinery finds this
# project's patched INSAT reader override (see
# src/global_mosaic/readers/insat3d_img_l1b_h5.yaml's header comment for
# why satpy's own stock copy can't be used against real files).
os.environ.setdefault(
    "SATPY_CONFIG_PATH", str(Path(__file__).resolve().parents[1] / "src" / "global_mosaic")
)

DATA_RAW = Path(__file__).resolve().parents[1] / "data" / "raw"
