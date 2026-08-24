"""Per-band gallery thumbnail rendering for Tier A output.

Built from the FULL-RESOLUTION in-memory tile data as it's produced by
build_band_mosaic_tiled (see run_phase2_tier.py) -- NOT by re-opening the
already-written .nc file afterward. The expensive part of this pipeline
is satellite acquisition + reprojection + selection (minutes per band);
re-reading a small already-packed .nc file to render a thumbnail would
be cheap by comparison, but accumulating the tile data directly avoids
that extra read entirely -- the data gets processed exactly once, not
twice.

Memory cost of the accumulator itself: one float32 full-grid array
per band, ~594MB at Tier A's ~7403x20038 grid -- small next to the
multi-GB per-band peaks already measured for the tile-major pipeline,
discarded immediately after rendering.

Fixed, per-band brightness-temperature ranges (not auto-scaled per
image) are the whole point -- the same band's images stay visually
comparable to each other across different timestamps, which an
auto-ranged colormap would NOT give you (a genuinely cold day would look
identical to a genuinely warm day if both got stretched to their own
min/max).
"""

from __future__ import annotations

import datetime as dt
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib
import numpy as np

matplotlib.use("Agg")  # headless -- this runs inside the acquisition/processing pipeline, no display
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402

from global_mosaic.processing.mosaic_io import _median_datetime  # noqa: E402

@dataclass(frozen=True)
class BandPreviewSpec:
    """Fixed display range and colormap convention for one band's thumbnail.

    Attributes:
        vmin: Fixed lower bound of the color scale, in the band's native
            units.
        vmax: Fixed upper bound of the color scale, in the band's native
            units.
        reverse_cmap: True: cold/low renders bright (brightness-
            temperature convention). False: high value renders bright
            (reflectance convention -- clouds/snow ARE bright, not
            inverted).
        units_label: Colorbar + title label, e.g. "Brightness
            Temperature (K)" or "Reflectance (%)".
    """

    vmin: float
    vmax: float
    reverse_cmap: bool  # True: cold/low renders bright (BT convention). False: high value renders bright (reflectance convention -- clouds/snow ARE bright, not inverted).
    units_label: str  # colorbar + title label, e.g. "Brightness Temperature (K)" or "Reflectance (%)"


# Observed value range per band, measured across all
# tiera_*.nc/tierb_*.nc files produced (2026-08-07):
#   MIR:  -3.5 to 400.1 K   <- NOT physical at either end, see below
#   WV:  182.4 to 274.0 K
#   TIR1: 179.7 to 336.4 K
#   TIR2: 179.1 to 336.1 K
#   VIS/SWIR: see their own entries below -- different unit conventions.
# The 4 IR bands' ranges are the observed min/max with a small
# symmetric pad, so nothing-seen-yet-but-plausible data doesn't get
# silently clipped to the edge of the color scale.
#
# MIR's raw observed range is NOT used directly -- -3.5K is below
# absolute zero (impossible; a fill/noise artifact, not real data) and
# 400.1K is far past any plausible even-reflectance-contaminated
# 3.8-4um BT. Used a physically-grounded range instead (200-350K:
# covers day/night MIR variability including legitimate
# solar-reflectance-driven daytime highs over hot/bright surfaces,
# without stretching the whole color scale around two outlier pixels).
# A handful of pixels outside this range will clip to pure
# white/black rather than being mid-scale-visible -- acceptable for a
# preview thumbnail, not a concern for the underlying data itself
# (this is a display-only choice, doesn't touch the actual stored
# values).
#
# VIS is REFLECTANCE (%), not brightness temperature -- physically a
# completely different quantity, and conventionally displayed the
# OPPOSITE way: clouds/snow are genuinely bright/high-reflectance and
# should render bright, ocean/land genuinely dark/low-reflectance and
# should render dark. Using the same "reverse" convention as the IR
# bands would invert this and look wrong to anyone used to VIS
# satellite imagery -- reverse_cmap=False here, unlike every IR band.
#
# SWIR: observed range across tierb_*.nc files
# was -0.95 to 94.85 (W m-2 sr-1 um-1, but see caveat below), negative
# clipped as unphysical/noise same as the other bands' outliers -- used
# 0-100 as a round, physically-grounded range covering it.
#
# CAVEAT, not fully resolved: SWIR has a pre-existing cross-
# satellite units inconsistency (VALUE_UNITS_BY_BAND's own docstring in
# run_phase2_tier.py -- different satellites' raw SWIR radiance uses
# genuinely different physical units, e.g. INSAT's
# mW.cm-2.sr-1.micron-1 vs ABI's W.m-2.sr-1.um-1, not just a label
# difference). The units LABEL stored on the array is only ever
# whichever satellite happened to be probed first (see build_
# band_mosaic_tiled's value_units probe) -- it is NOT guaranteed
# accurate for every pixel if a different-unit satellite actually won
# that pixel. All 10 files checked happened to show
# the same ABI-style unit, but that's an observation about these
# particular runs, not a guarantee. Rendered anyway (with the label
# flagged below) rather than refused outright -- useful is better than
# nothing here, as long as the caveat is real and stated, not hidden.
BAND_PREVIEW_SPECS: dict[str, BandPreviewSpec] = {
    "MIR": BandPreviewSpec(200.0, 350.0, True, "Brightness Temperature (K)"),
    "WV": BandPreviewSpec(175.0, 280.0, True, "Brightness Temperature (K)"),
    "TIR1": BandPreviewSpec(175.0, 340.0, True, "Brightness Temperature (K)"),
    "TIR2": BandPreviewSpec(175.0, 340.0, True, "Brightness Temperature (K)"),
    "VIS": BandPreviewSpec(0.0, 100.0, False, "Reflectance (%)"),
    "SWIR": BandPreviewSpec(0.0, 100.0, False, "Radiance, units vary by satellite (see caveat)"),
}

_THUMBNAIL_WIDTH_PX = 2000
_GRATICULE_STEP_DEG = 20
_COASTLINE_COLOR = "#c8e6a0"  # light yellow-green, matches the MOSDAC reference sample's graticule/coastline style
_MIN_LABEL_POPULATION = 20_000_000  # only label reasonably large countries -- avoids the clutter a full ~180-country label set would cause on a small global thumbnail

# Matches reproject.py's _MAX_ABS_LATITUDE_DEG (GEO-visible range
# only, see that module's own docstring/README) -- the actual project
# grid only ever covers +-66.5 deg, NOT +-90. Duplicated here (not
# imported) since preview.py has no other reason to depend on
# reproject.py; keep in sync if that constant ever changes. The bug
# this fixes: an earlier version declared the image's extent as +-90 to
# imshow/set_extent, which STRETCHES the actual +-66.5-covering data to
# fill a taller range than it really represents -- every latitude
# gridline and every coastline (drawn from real vector geography, not
# stretched) would then land in the wrong row relative to the
# (silently distorted) data raster underneath it.
_MAX_ABS_LATITUDE_DEG = 66.5

# Layout constants for render_band_preview's map/title/legend positioning,
# added 2026-08-17 to remove the large left/right padding the original
# fixed-fraction axes position (left=0.035, width=0.955) left around the
# map. Root cause, found via direct pixel measurement (not assumed):
# a GeoAxes auto-centers its actual rendered content whenever the axes
# box's own width:height ratio doesn't exactly match the true geographic
# aspect ratio (360deg : 2*_MAX_ABS_LATITUDE_DEG) -- the old box
# (0.955 : 0.90) didn't match, so cartopy was silently eating extra
# margin beyond anything the declared axes fraction alone would suggest.
# Fixing the axes box to exactly match that aspect ratio makes the label/
# title/legend positions below behave predictably: the declared
# axes-left position then maps 1:1 to rendered pixels.
_LABEL_MARGIN_PX = 34
"""Reserved left margin, in pixels -- just enough for the widest latitude
tick label ("60°N"-style, at the 7pt font this function uses) to render
fully flush against the image's left edge with no gap before it (measured
constant offset ~31.2px between the axis spine and where the label
starts, plus a few px of safety headroom). Mirrored as-is on the right
side too, as pure blank space (no content there) -- this is what
keeps the map visually centered in the frame rather than shifted toward
whichever side had a label. Font size and DPI are both fixed in this
function, so this pixel value doesn't need to scale with thumbnail width."""

_TOP_STRIP_PX = 62
"""Reserved height, in pixels, for the title text (4 lines at 7pt) plus
the legend/colorbar, ABOVE the map -- added as genuinely extra figure
height rather than carved out of the map's own aspect-locked height.
At the tight 34px left/right margin above, an aspect-exact map would need to fill ~96.6% of whatever
total height it's given, leaving only ~25px for title+bottom margin
combined -- nowhere near enough for a 4-line title. Making the figure
taller (not shrinking the map or the title) is what resolves that."""

_BOTTOM_STRIP_PX = 26
"""Reserved height, in pixels, for the longitude tick labels below the
map -- same "genuinely extra height" reasoning as _TOP_STRIP_PX."""


@dataclass
class PreviewAccumulator:
    """Accumulates one band's tile-by-tile `value` arrays into one
    full-grid float32 array, in the exact shape/order write_band_tile
    already writes them in -- see build_band_mosaic_tiled's tile loop,
    which calls `add_tile` right after each `write_band_tile` call, using
    the SAME already-computed `value` array (no extra work to produce
    it). `satellite_keys`/`scan_mids` are set once, after the tile loop
    finishes (the caller already has the full per-band contributor set
    by then via metas_by_slot -- no need to track either tile-by-tile
    here too).

    Attributes:
        grid: Full-grid ``(height, width)`` float32 array, NaN where
            nothing has been written yet.
        satellite_keys: Satellite keys that contributed to this band.
        scan_mids: Each contributing satellite's real scan mid-time --
            see render_band_preview's "UTC Mid Scanning Time" (the
            mosaic's authoritative timestamp, not the requested target).
    """

    grid: np.ndarray  # (height, width), float32, NaN where nothing has been written yet
    satellite_keys: set[str] = field(default_factory=set)
    scan_mids: list[dt.datetime] = field(default_factory=list)  # each contributing satellite's real scan mid-time -- see render_band_preview's "UTC Mid Scanning Time" (the mosaic's authoritative timestamp, not the requested target)

    def add_tile(self, y0: int, y1: int, value: np.ndarray) -> None:
        """Write one tile's already-computed value array into this accumulator's grid.

        Args:
            y0: Tile's start row (inclusive).
            y1: Tile's end row (exclusive).
            value: Tile's value array, shape ``(y1 - y0, width)``.
        """
        self.grid[y0:y1, :] = value.astype(np.float32)


def new_accumulator(height: int, width: int) -> PreviewAccumulator:
    """Create an empty PreviewAccumulator for a full target grid.

    Args:
        height: Full grid height, in pixels.
        width: Full grid width, in pixels.

    Returns:
        A new :class:`PreviewAccumulator` with an all-NaN grid.
    """
    return PreviewAccumulator(grid=np.full((height, width), np.nan, dtype=np.float32))


def _downsample_block_mean(data: np.ndarray, target_width: int) -> np.ndarray:
    """Downsample an array to a target width via NaN-aware block averaging.

    NaN-aware block-mean downsample to `target_width` columns, height
    scaled to preserve aspect ratio. Block-mean (not simple striding)
    avoids aliasing artifacts a naive `data[::step, ::step]` would
    introduce on features smaller than the stride.

    Args:
        data: Full-resolution 2D array to downsample.
        target_width: Desired output width, in columns.

    Returns:
        The downsampled array, with height scaled proportionally to
        preserve `data`'s aspect ratio. Blocks that are entirely NaN
        produce NaN.
    """
    height, width = data.shape
    factor = max(1, round(width / target_width))
    new_h, new_w = height // factor, width // factor
    trimmed = data[: new_h * factor, : new_w * factor]
    reshaped = trimmed.reshape(new_h, factor, new_w, factor)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        # A block that's 100% NaN (e.g. a no-coverage gap) correctly
        # produces NaN via nanmean -- numpy's own "Mean of empty slice"
        # RuntimeWarning for that case is expected, not a bug, so it's
        # suppressed here rather than left to print on every run.
        warnings.filterwarnings("ignore", message="Mean of empty slice")
        return np.nanmean(reshaped, axis=(1, 3))


def _country_label_points(min_population: int) -> list[tuple[str, float, float]]:
    """Get label points for reasonably large countries, from Natural Earth data.

    (short_form, lon, lat) for reasonably large countries, from
    Natural Earth's 110m admin_0_countries (the coarsest/lightest
    Natural Earth resolution -- appropriate for a small global thumbnail,
    matches the "light coastline boundary" request). ISO_A2 is the
    "short form" -- standard, unambiguous two-letter country codes
    (US, IN, CN, ...) rather than a hand-picked abbreviation list.
    representative_point() (not centroid) guarantees a point actually
    INSIDE the country's own polygon, including oddly-shaped ones.

    Args:
        min_population: Minimum ``POP_EST`` for a country to be included.

    Returns:
        List of ``(iso2_code, lon, lat)`` tuples, one per qualifying
        country.
    """
    import cartopy.io.shapereader as shpreader

    shp_path = shpreader.natural_earth(resolution="110m", category="cultural", name="admin_0_countries")
    reader = shpreader.Reader(shp_path)
    labels = []
    for record in reader.records():
        attrs = record.attributes
        pop = attrs.get("POP_EST", 0) or 0
        if pop < min_population:
            continue
        iso2 = attrs.get("ISO_A2", "-99")
        if not iso2 or iso2 == "-99":
            iso2 = attrs.get("ADM0_A3", "")[:3]  # Natural Earth quirk: some entries (e.g. France) have ISO_A2=-99
        if not iso2:
            continue
        point = record.geometry.representative_point()
        labels.append((iso2, point.x, point.y))
    return labels


def compute_display_time(accumulator: PreviewAccumulator, target: dt.datetime) -> dt.datetime:
    """Compute the mosaic's authoritative display time for a band's preview.

    The median scan mid-time across this band's contributing
    satellites -- the mosaic's actual authoritative timestamp, not the
    requested `target` (falls back to `target` only if there's nothing
    to average, e.g. zero contributing satellites). Exposed as its own
    function (not just inlined in render_band_preview) so callers can
    compute it ONCE and use the SAME value for both the gallery
    filename and the title text -- see run_phase2_tier.py/
    generate_gallery_from_nc.py, both of which need it before the
    filename can even be constructed, which is before ``render_band_preview``
    (which needs the final out_path already decided) can run.

    Args:
        accumulator: This band's :class:`PreviewAccumulator`.
        target: The mosaic run's requested target time (fallback).

    Returns:
        The median of `accumulator.scan_mids`, or `target` if empty.
    """
    return _median_datetime(accumulator.scan_mids) if accumulator.scan_mids else target


def render_band_preview(
    accumulator: PreviewAccumulator,
    band: str,
    target: dt.datetime,
    out_path: Path,
) -> None:
    """Render and save one band's gallery thumbnail PNG.

    Renders one band's gallery thumbnail: fixed-range grayscale (
    direction depends on the band -- see BandPreviewSpec.reverse_cmap),
    graticules, light coastlines/borders, short-form country labels, a
    legend, and a title -- matching the style of the reference sample
    image the user provided (own branding, not MOSDAC/ISRO's).

    Args:
        accumulator: This band's fully-populated :class:`PreviewAccumulator`.
        band: Logical band name, must be a key of ``BAND_PREVIEW_SPECS``.
        target: The mosaic run's requested target time, shown in the
            title.
        out_path: Destination PNG file path; parent directories are
            created if needed.

    Returns:
        None. Writes the rendered PNG to `out_path`.

    Raises:
        ValueError: If `band` has no entry in ``BAND_PREVIEW_SPECS``.
    """
    if band not in BAND_PREVIEW_SPECS:
        raise ValueError(f"No preview spec for band {band!r} -- add one to BAND_PREVIEW_SPECS first")
    spec = BAND_PREVIEW_SPECS[band]
    vmin, vmax = spec.vmin, spec.vmax
    preview = _downsample_block_mean(accumulator.grid, _THUMBNAIL_WIDTH_PX)

    map_height_px, width_px = preview.shape
    lat_extent = [-180, 180, -_MAX_ABS_LATITUDE_DEG, _MAX_ABS_LATITUDE_DEG]

    # Figure is made genuinely TALLER than the map's own aspect ratio would
    # require -- dedicated pixel strips added on top (title+legend) and
    # bottom (longitude labels), rather than carved out of the map's own
    # height. See _TOP_STRIP_PX/_BOTTOM_STRIP_PX's own docstrings for why:
    # a tight label margin and a multi-line title can't both fit within a
    # figure sized to the map's pure aspect ratio alone.
    required_aspect = 360 / (2 * _MAX_ABS_LATITUDE_DEG)
    map_width_px = width_px - 2 * _LABEL_MARGIN_PX
    map_height_px_exact = map_width_px / required_aspect
    total_height_px = round(map_height_px_exact + _TOP_STRIP_PX + _BOTTOM_STRIP_PX)

    left_margin = _LABEL_MARGIN_PX / width_px
    right_margin = left_margin  # mirrored blank space, no content -- keeps the map visually centered
    ax_width = map_width_px / width_px
    ax_height = map_height_px_exact / total_height_px
    ax_bottom = _BOTTOM_STRIP_PX / total_height_px

    fig = plt.figure(figsize=(width_px / 100, total_height_px / 100), dpi=100)
    ax = fig.add_axes([left_margin, ax_bottom, ax_width, ax_height], projection=ccrs.PlateCarree())
    ax.set_extent(lat_extent, crs=ccrs.PlateCarree())

    # gray_r ("reverse" -- cold/low renders bright) for BT bands, plain
    # gray (high renders bright) for reflectance bands like VIS -- see
    # BandPreviewSpec.reverse_cmap's own docstring for why these need
    # opposite conventions, not the same one applied everywhere.
    #
    # A bug caught by actually looking at rendered output (2026-08-07,
    # a run where Meteosat failed and left a no-coverage gap): NaN
    # pixels default to the SAME white as the coldest real data (vmin),
    # making "no data" visually indistinguishable from "extremely cold" --
    # a viewer can't tell a genuine coverage gap from real polar/storm-top
    # cold. set_bad gives NaN its own distinct mid-gray, clearly different
    # from both ends of the data's own black/white range.
    cmap = matplotlib.colormaps["gray_r" if spec.reverse_cmap else "gray"].copy()
    cmap.set_bad(color="#3a3a3a")
    im = ax.imshow(
        preview,
        extent=lat_extent,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        origin="upper",
        interpolation="nearest",
    )

    # Deliberately coastline only, no political/country borders (cfeature.
    # BORDERS) -- several real-world borders this global mosaic covers are
    # disputed, and drawing a specific line would take an implicit
    # position on exactly those disputes. Coastlines are a physical
    # feature, not a political claim.
    ax.coastlines(resolution="110m", color=_COASTLINE_COLOR, linewidth=0.6)

    # ylocs capped within +-66.5 -- a gridline at 80 (the old range)
    # would sit outside the actual data extent entirely.
    lat_ticks = [v for v in range(-60, 61, _GRATICULE_STEP_DEG)]
    gl = ax.gridlines(
        draw_labels=True, color=_COASTLINE_COLOR, linewidth=0.4, linestyle="-",
        xlocs=range(-180, 181, _GRATICULE_STEP_DEG), ylocs=lat_ticks,
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"color": _COASTLINE_COLOR, "size": 7}
    gl.ylabel_style = {"color": _COASTLINE_COLOR, "size": 7}

    for iso2, lon, lat in _country_label_points(_MIN_LABEL_POPULATION):
        ax.text(
            lon, lat, iso2, transform=ccrs.PlateCarree(),
            color="#ff6b3d", fontsize=6, fontweight="bold", ha="center", va="center",
            clip_on=True,
        )

    satellites = ", ".join(sorted(accumulator.satellite_keys)) or "no data"
    display_time = compute_display_time(accumulator, target)
    # x=0.002 (not a bigger fixed fraction) -- deliberately aligns with
    # where the latitude tick labels themselves now start (see
    # _LABEL_MARGIN_PX), not with the map/axes edge, which sits further
    # right at left_margin.
    fig.text(
        0.002, 0.995,
        f"GLOBAL MOSAIC -- {band} ({spec.units_label})\n"
        f"Satellites: {satellites}\n"
        f"UTC Target Time: {target.strftime('%d%b%Y %H%M%S')}\n"
        f"UTC Mid Scanning Time: {display_time.strftime('%d%b%Y %H%M')}   Geographic (EPSG:4326)",
        transform=fig.transFigure, color="white", fontsize=7, fontfamily="monospace",
        verticalalignment="top",
    )
    fig.patch.set_facecolor("black")

    # Right edge aligned with the map's own right edge (1 - right_margin),
    # not a fixed fraction -- mirrors how the title aligns with the
    # latitude labels' left edge above. Vertical position/size are fixed
    # pixel offsets within _TOP_STRIP_PX, not figure fractions, so they
    # don't drift if _TOP_STRIP_PX is ever tuned.
    cax_width = 0.32
    cax_left = (1 - right_margin) - cax_width
    cax = fig.add_axes([cax_left, 1 - 22 / total_height_px, cax_width, 10 / total_height_px])
    cbar = fig.colorbar(im, cax=cax, orientation="horizontal")
    cbar.set_label(spec.units_label, color="white", fontsize=7, labelpad=2)
    cbar.ax.xaxis.set_ticks_position("bottom")
    cbar.ax.xaxis.set_label_position("top")
    cbar.ax.xaxis.set_tick_params(color="white", labelsize=6)
    # vmin/vmax as explicit ticks (matplotlib's default locator picks "nice"
    # round numbers that don't always land exactly on the fixed range's own
    # endpoints -- e.g. WV's 175-280 or TIR1/TIR2's 175-340 -- so without
    # this a viewer has no way to read the actual scale bounds off the
    # legend), PLUS the same "nice" intermediate ticks the default locator
    # would have picked, for in-between reference points. Any auto tick
    # landing too close to an endpoint is dropped rather than kept -- on a
    # bar this narrow (0.32 fig width) a label crowded right next to
    # vmin/vmax's own label would just overlap it, not add information.
    auto_ticks = matplotlib.ticker.MaxNLocator(nbins=4).tick_values(vmin, vmax)
    min_gap = (vmax - vmin) * 0.12
    auto_ticks = [t for t in auto_ticks if min_gap < (t - vmin) and min_gap < (vmax - t)]
    ticks = [vmin, *auto_ticks, vmax]
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([f"{t:g}" for t in ticks])
    plt.setp(plt.getp(cbar.ax.axes, "xticklabels"), color="white")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor="black", bbox_inches=None)
    plt.close(fig)
