import { compactTimestamp } from './thredds'

// xcube base URL -- same env-driven pattern as thredds.ts's THREDDS_BASE_URL.
// Set VITE_XCUBE_URL to override.
import { getXcubeBaseUrl } from './runtimeBackendConfig'
export const XCUBE_BASE_URL: string = getXcubeBaseUrl()

// xcube's config (services/xcube/config.yml) registers
// the whole tiera_<timestamp>.nc directory as a single `mosaics` DataStore
// -- unlike THREDDS's joinNew aggregation, there is no virtual
// time-dimensioned dataset here, each raw file is its own separate xcube
// dataset, with id `mosaics~<filename>` (per GET /datasets).
// `_filename_for` in geosat-mosaic-api/main.py derives the
// exact same filename from an ISO timestamp already -- this mirrors it on
// the frontend side using thredds.ts's compactTimestamp (which THREDDS's
// own aggregation time-dimension values, this app's `time` state, already
// round-trip through for the gallery thumbnail URL).
export function xcubeDatasetId(time: string): string {
  return `mosaics~tiera_${compactTimestamp(time)}.nc`
}

// The source data's native grid resolution -- 7403 lat x 20038 lon
// samples globally (see geosat-mosaic-api/main.py's own comment on this
// same grid, backing `lat`/`lon` in the raw .nc files) -- reaches parity
// with a Cesium GeographicTilingScheme tile's pixel density right around
// z=6: total tile-grid width at z is `2 * 2^z * 256` px covering the full
// 360 deg of longitude, which only exceeds the native 20038-sample width
// (i.e. actually shows more detail, not just interpolation) starting at
// z=6 (32768px, ~1.6x oversampled) -- z=5 (16384px, ~0.8x) is coarser
// than the source data, still legitimately gains resolution by zooming
// further. Past z=6, xcube is purely upsampling/interpolating -- every
// deeper level looks identical to z=6, just recomputed from scratch.
// Capping here avoids wasting server compute: without this cap the app
// was observed requesting z=11-13 tiles while zooming in normally
// (30-100x finer than the data supports).
export const XCUBE_MAX_TILE_LEVEL = 6

// Builds a Cesium `UrlTemplateImageryProvider`-compatible URL template
// (literal `{z}`/`{y}`/`{x}` placeholders) for one variable of one
// dataset. `crs=CRS84` is what lets a Cesium `GeographicTilingScheme`
// (not Web Mercator) line up correctly, since the underlying data is a
// plain lat/lon grid, not projected.
export function xcubeTileUrlTemplate(
  datasetId: string,
  varName: string,
  params: Record<string, string>,
): string {
  const search = new URLSearchParams({ crs: 'CRS84', format: 'png', ...params })
  return `${XCUBE_BASE_URL}/tiles/${encodeURIComponent(datasetId)}/${varName}/{z}/{y}/{x}?${search.toString()}`
}

// Legend/colorbar PNG for one variable -- xcube's own equivalent of the
// old THREDDS/EDAL GetLegendGraphic call. `datasetId` is required by the
// endpoint's URL shape even though the legend gradient itself doesn't
// depend on which file's data it is -- any currently-valid dataset id
// works, so callers pass whatever timestamp is already selected.
//
// The gradient ALWAYS varies along the `height` axis, regardless of the
// requested width/height aspect ratio -- decoding the actual returned PNG
// pixel-by-pixel confirms it: a request for width=200/height=14
// (landscape) still produces a vertically-varying gradient (uniform color
// across every row, changing top-to-bottom down the 14-tall axis)
// squeezed into a wide/short canvas, not a horizontal one. There's no
// `orientation` param on this endpoint to change that. mosaicLegendUrl
// (MosaicLayer.tsx) is the caller that actually wants a horizontal
// on-screen bar -- it requests the opposite (portrait) aspect from here
// and rotates the result 90deg in CSS, same technique THREDDS/EDAL's own
// always-vertical COLORBARONLY mode needed before this switch. Whatever's
// requested here also gets returned at some server-side-chosen multiple
// (e.g. 200x14 in -> 20000x1400 out, same ratio) -- harmless, `<img>`'s
// own width/height attributes still control the final on-screen size
// regardless of the PNG's actual pixel dimensions.
export function xcubeLegendUrl(
  datasetId: string,
  varName: string,
  cmap: string,
  vmin: number,
  vmax: number,
  width = 200,
  height = 14,
): string {
  const params = new URLSearchParams({
    cmap,
    vmin: String(vmin),
    vmax: String(vmax),
    width: String(width),
    height: String(height),
  })
  return `${XCUBE_BASE_URL}/tiles/${encodeURIComponent(datasetId)}/${varName}/legend?${params.toString()}`
}

export interface ColorbarEntry {
  name: string
  // Base64-encoded PNG gradient swatch, straight from xcube's own
  // `/colorbars` response -- a generic preview of the palette's shape,
  // not adjusted to the current colorMin/colorMax (unlike the old
  // per-option live GetLegendGraphic fetch this replaces) -- a fetch per
  // visible dropdown option was affordable against THREDDS's cheap
  // GetLegendGraphic; this is simpler still (zero extra requests, the
  // preview rides along with the one-time colorbar list fetch) at the
  // minor cost of the preview not reflecting the live range.
  previewPng: string
}

export type ColorbarGroup = [title: string, entries: ColorbarEntry[]]

// Already-grouped (Perceptually Uniform Sequential / Sequential /
// Diverging / Qualitative / Cyclic / Ocean / Miscellaneous, per GET
// /colorbars) replacement for THREDDS/EDAL's flat 94-name palette
// list -- xcube uses plain matplotlib colormap names (`viridis`, `Greys`,
// `RdBu`, ...), not EDAL's `div-`/`psu-`/`seq-`/`x-` prefixed naming, so
// this is a genuinely different name space, not a 1:1 rename. Fetched
// once, like the old fetchPalettes.
export async function fetchColorbars(): Promise<ColorbarGroup[]> {
  const response = await fetch(`${XCUBE_BASE_URL}/colorbars`)
  const data = (await response.json()) as [string, string, [string, string][]][]
  return data.map(
    ([title, , entries]) =>
      [title, entries.map(([name, previewPng]) => ({ name, previewPng }))] as ColorbarGroup,
  )
}
