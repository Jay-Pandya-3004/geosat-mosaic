import { useMemo } from 'react'
import { ImageryLayer } from 'resium'
import { GeographicTilingScheme, Rectangle, UrlTemplateImageryProvider } from 'cesium'
import { XCUBE_MAX_TILE_LEVEL, xcubeDatasetId, xcubeLegendUrl, xcubeTileUrlTemplate } from '../lib/xcube'

export type MosaicBand = 'mir' | 'wv' | 'tir1' | 'tir2'

// Northern/southern edge of every mosaic file. Nothing exists past this, so don't
// let the imagery layer request tiles there.
const COVERAGE_RECTANGLE = Rectangle.fromDegrees(-180, -66.49102, 180, 66.49102)

interface MosaicLayerProps {
  band?: MosaicBand
  // ISO 8601 timestamp. Required in practice, not just optional -- unlike
  // THREDDS's aggregated dataset (which could fall back to its own
  // "latest" default when this was omitted), xcube has no aggregation:
  // each timestamp is its own separate dataset (see lib/xcube.ts), so
  // there is no file to render without a real value here. Stays optional
  // in the type only to match Globe.tsx's brief startup window before the
  // timestamp list has loaded (`timestamps[timeIndex]` is `undefined`
  // then) -- the component just renders nothing until a real value shows
  // up, same as it already does for point-probe/timeseries elsewhere in
  // Globe.tsx.
  time?: string
  alpha?: number
  // xcube tile colormap/range selection -- one
  // shared palette/range across all 4 bands, same as `alpha`/opacity
  // already being shared rather than per-band. `palette` is a plain
  // matplotlib colormap name now (`viridis`, `Greys`, ...), not one of
  // THREDDS/EDAL's `div-`/`psu-`/`seq-`/`x-`-prefixed names -- see
  // lib/xcube.ts's fetchColorbars.
  palette?: string
  colorMin?: number
  colorMax?: number
  // Live sync / "show always recent" -- cache-
  // busting resolution: an extra tile-URL param whenever either toggle is
  // on (`undefined` -- add nothing -- otherwise), so tile requests for
  // `partial` timestamps that get revised later can't get stuck showing a
  // stale cached tile while the user has explicitly asked for freshness.
  // See Globe.tsx's `activeCacheBust`.
  cacheBust?: number
}

// Satellite mosaic ("band layer") -- the actual tile layer from
// the four-layer switcher. Switched from THREDDS WMS GetMap to xcube's
// native tile API 2026-08-15. Accepted behavior changes from that switch,
// not oversights:
//   - No contour styles any more (xcube's tile API has no contour
//     support) -- this was the explicit tradeoff accepted for the switch.
//   - Out-of-[colorMin,colorMax] values clip to the palette's extreme
//     color (opaque) instead of THREDDS/EDAL's `abovemaxcolor`/
//     `belowmincolor: transparent` -- xcube's tile endpoint has no
//     equivalent query param. Fixed client-side in
//     geosat-mosaic-openlayers (its own `lib/transparentEdgesTileLoader.ts`)
//     but explicitly NOT ported here after repeated testing: a
//     Cesium-specific `TransparentEdgesImageryProvider` (subclassing
//     `UrlTemplateImageryProvider`, overriding `requestImage` to
//     post-process the resolved tile image) was tried, and every version
//     of it broke Cesium's own tile-refresh bookkeeping -- direct A/B
//     testing (swapping back to this exact plain provider) at a
//     reproducible camera position showed a single tile would get
//     visibly "stuck" showing a stale zoom level while its neighbors
//     correctly refreshed to the current one. Two different fix attempts
//     (skip reprocessing for unchanged tiles; convert the processed
//     canvas back to a real `HTMLImageElement` before returning, matching
//     Cesium's own expected type) each fixed a separately-confirmed
//     symptom but neither fixed this one. Accepted as a genuine
//     Cesium/OpenLayers platform difference rather than continuing to
//     chase it: this layer stays on the plain, unmodified
//     `UrlTemplateImageryProvider`, opaque clipping and all.
function MosaicLayer({
  band = 'tir1',
  time,
  alpha = 1,
  palette = 'viridis',
  colorMin = 180,
  colorMax = 330,
  cacheBust,
}: MosaicLayerProps) {
  const provider = useMemo(() => {
    if (!time) return undefined

    const params: Record<string, string> = {
      cmap: palette,
      vmin: String(colorMin),
      vmax: String(colorMax),
      norm: 'lin',
    }
    if (cacheBust !== undefined) params._sync = String(cacheBust)

    return new UrlTemplateImageryProvider({
      url: xcubeTileUrlTemplate(xcubeDatasetId(time), `value_${band}`, params),
      // CRS84 (plain lat/lon), not Web Mercator -- matches the `crs=CRS84`
      // baked into xcubeTileUrlTemplate. This deliberately avoids repeating
      // an earlier, related GIBS tiling-scheme bug: checked against
      // xcube's tile output before shipping rather than assumed.
      tilingScheme: new GeographicTilingScheme(),
      tileWidth: 256,
      tileHeight: 256,
      rectangle: COVERAGE_RECTANGLE,
      // See lib/xcube.ts's XCUBE_MAX_TILE_LEVEL for why this specific
      // value -- past it, xcube is purely interpolating, not rendering
      // any real additional detail.
      maximumLevel: XCUBE_MAX_TILE_LEVEL,
      // Point-probe bypasses both THREDDS and xcube entirely (its own
      // FastAPI endpoint, reading the raw file directly) -- because
      // THREDDS's NCSS
      // grid-as-point endpoint is unsafe against this dataset. WMS/xcube
      // GetFeatureInfo-equivalents were never used for this either way.
      enablePickFeatures: false,
      // Surfaces the source satellites in Cesium's own native credit
      // display (bottom-left), same as BasemapLayer.tsx's per-style
      // `credit` -- previously this layer had no credit at all, so the
      // native display never mentioned satellite data regardless of the
      // basemap in use.
      credit: 'GOES-18/19 (NOAA), Meteosat-9/10 (EUMETSAT), INSAT-3DR/3DS (ISRO/SAC/MOSDAC), Himawari-9 (JMA)',
    })
  }, [band, time, palette, colorMin, colorMax, cacheBust])

  if (!provider) return null

  // Explicit `index` -- see ContributionMaskLayer.tsx for why. This layer
  // sits directly above the basemap (index 0), below the contribution mask
  // (index 2).
  return <ImageryLayer imageryProvider={provider} alpha={alpha} index={1} />
}

// Colorbar for the current palette/range -- xcube's own `/legend`
// endpoint. `length`/`thickness` describe the caller's desired ON-SCREEN
// (horizontal bar) dimensions, not the params sent to xcube: xcube's
// gradient always varies along its requested `height` axis regardless of
// aspect ratio (see lib/xcube.ts's xcubeLegendUrl), so
// this requests the transposed (portrait: xcube-width=thickness,
// xcube-height=length) image and the caller (LayerSwitcher.tsx) rotates
// it 90deg in CSS to display it as a horizontal bar -- same rotate-90
// technique the old THREDDS/EDAL COLORBARONLY image needed, still
// required here for the opposite reason (that mode was always-vertical
// regardless of aspect; this one turns out to be too, just discovered
// later). Returns '' when `time` isn't known yet (no dataset id can be
// built) -- callers should skip rendering the `<img>` in that case
// rather than pointing it at an empty src.
export function mosaicLegendUrl(
  band: MosaicBand,
  palette: string,
  colorMin: number,
  colorMax: number,
  time: string | undefined,
  length = 200,
  thickness = 14,
): string {
  if (!time) return ''
  return xcubeLegendUrl(
    xcubeDatasetId(time),
    `value_${band}`,
    palette,
    colorMin,
    colorMax,
    thickness,
    length,
  )
}

export default MosaicLayer
