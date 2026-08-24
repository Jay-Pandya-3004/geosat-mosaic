"""Maps each sat_metadata.py satellite key to the satpy reader name and
dataset/band key needed to actually load its IR-window channel.

This is deliberately separate from sat_metadata.py: that file is descriptive
(sourced from OSCAR -- what the satellite *is*), this file is a practical
recipe (what satpy call loads its data). Migrated/reconciled from the
original prototype's flat CHANNEL_MAP.

`reader` is a stock satpy reader name where satpy ships one; for the two
noted below satpy has no stock reader and the project's own custom reader
in ../readers/ is used instead. `None` means genuinely unimplemented --
don't guess at a reader name that doesn't exist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReaderSpec:
    """How to load a satellite's data: which satpy reader and IR-window band to use.

    Attributes:
        reader: satpy reader name (stock or this project's custom reader
            registered under the same name), or None if unimplemented.
        ir_window_band: Dataset/band key for the satellite's IR-window
            channel, or None if unimplemented/not applicable.
        custom: True if `reader` is one of this project's own custom
            readers (in ``../readers/``) rather than a stock satpy reader.
        note: Free-text caveat, e.g. verification status or format notes.
    """

    reader: str | None
    ir_window_band: str | None
    custom: bool = False
    note: str = ""


READER_REGISTRY: dict[str, ReaderSpec] = {
    # --- GOES legacy imager (GridSat reprocessed archive) ---
    # Only goes-08 has a verified custom reader so far (readers/gridsat_goes8_reader.py).
    # goes-09..15 likely share the same GridSat-GOES.<sat>.<time>.v01.nc format --
    # unverified, try the same reader before writing a new one.
    "goes-08": ReaderSpec("gridsat_goes8", "ch4", custom=True),
    "goes-09": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-10": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-11": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-12": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-13": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-14": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),
    "goes-15": ReaderSpec("gridsat_goes8", "ch4", custom=True, note="unverified against goes-08 reader"),

    # --- GOES-R series ABI (stock satpy reader) ---
    "goes-16": ReaderSpec("abi_l1b", "C13"),
    "goes-17": ReaderSpec("abi_l1b", "C13"),
    "goes-18": ReaderSpec("abi_l1b", "C13"),
    "goes-19": ReaderSpec("abi_l1b", "C13"),

    # --- Meteosat SEVIRI (stock satpy reader) ---
    "meteosat-08": ReaderSpec("seviri_l1b_native", "IR_108"),
    "meteosat-09": ReaderSpec("seviri_l1b_native", "IR_108"),
    "meteosat-10": ReaderSpec("seviri_l1b_native", "IR_108"),
    "meteosat-11": ReaderSpec("seviri_l1b_native", "IR_108"),

    # --- Meteosat MVIRI (older, pre-SEVIRI -- FIDUCEO FCDR reprocessing) ---
    "meteosat-05": ReaderSpec("mviri_l1b_fiduceo", None, note="band key unverified, not needed for current phases"),
    "meteosat-06": ReaderSpec("mviri_l1b_fiduceo", None, note="band key unverified, not needed for current phases"),
    "meteosat-07": ReaderSpec("mviri_l1b_fiduceo", None, note="band key unverified, not needed for current phases"),

    # --- Himawari AHI (stock satpy reader) ---
    "himawari-08": ReaderSpec("ahi_hsd", "B13"),
    "himawari-09": ReaderSpec("ahi_hsd", "B13"),
    "himawari-10": ReaderSpec(None, None, note="not yet launched"),

    # --- MTSAT (legacy JMA, pre-Himawari) ---
    "mtsat-01": ReaderSpec(None, None, note="no known satpy reader -- unimplemented"),
    "mtsat-02": ReaderSpec(None, None, note="no known satpy reader -- unimplemented"),

    # --- GK2A AMI (stock satpy reader) ---
    "gk2a": ReaderSpec("ami_l1b", "IR105"),
    "gk2b": ReaderSpec(None, None, note="no full-disk meteorological imager -- excluded"),

    # --- INSAT / Kalpana (custom reader) ---
    # Verified working 2026-07-30 against a real INSAT-3DS L1B file
    # (01-JAN-2026): needs SATPY_CONFIG_PATH pointed at
    # readers/insat3d_img_l1b_h5.yaml's parent dir (src/global_mosaic) so
    # satpy uses this project's patched file_reader instead of its own
    # stock one -- satpy's stock insat3d_img_l1b_h5.py reader crashes on
    # real files (Acquisition_Start_Time's fractional-seconds suffix
    # isn't handled by its strptime format string). See
    # readers/insat3d_img_l1b_h5.yaml's own header comment.
    "kalpana-1": ReaderSpec(None, None, note="VHRR format, no known reader -- unimplemented"),
    "insat-3d": ReaderSpec("insat3d_img_l1b_h5", "TIR1", custom=True, note="verified against 3DS files, not independently re-checked against a real 3D file"),
    "insat-3dr": ReaderSpec("insat3d_img_l1b_h5", "TIR1", custom=True, note="verified against 3DS files, not independently re-checked against a real 3DR file"),
    "insat-3ds": ReaderSpec("insat3d_img_l1b_h5", "TIR1", custom=True, note="verified 2026-07-30 against real 3DS files (01-JAN-2026 sample)"),
}


def get_reader_spec(satellite_key: str) -> ReaderSpec:
    """Look up the satpy reader recipe for a satellite.

    Args:
        satellite_key: Satellite key, e.g. ``"goes-18"``.

    Returns:
        The registered :class:`ReaderSpec` for `satellite_key`.

    Raises:
        KeyError: If `satellite_key` is not in ``READER_REGISTRY``.
    """
    return READER_REGISTRY[satellite_key]
