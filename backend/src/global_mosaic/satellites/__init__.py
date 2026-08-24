"""Satellite metadata, coverage, and radiometric-correction subpackage.

Resolves which satellites exist and their static metadata
(``sat_metadata``), which satellites can see a given mosaic slot
(``coverage``), which satpy reader to use for a satellite/band
(``reader_registry``), and the spectral response function (SRF) based
radiometric corrections applied to bring different satellites' bands
onto a common radiometric scale (``srf_correction``,
``srf_convolution``, ``srf_reflectance``).

Re-exports the most commonly used symbols so callers can do
``from global_mosaic.satellites import get_satellite`` etc. rather than
reaching into the individual submodules.
"""

from global_mosaic.satellites.coverage import candidates_for_slot, resolve_slot
from global_mosaic.satellites.reader_registry import get_reader_spec
from global_mosaic.satellites.sat_metadata import (
    Satellites,
    Satellites_Objects,
    get_satellite,
    satellites_covering,
)

__all__ = [
    "Satellites",
    "Satellites_Objects",
    "get_satellite",
    "satellites_covering",
    "get_reader_spec",
    "candidates_for_slot",
    "resolve_slot",
]
