import { useMemo } from 'react'
import { ImageryLayer } from 'resium'
import { GeographicTilingScheme, Rectangle, UrlTemplateImageryProvider } from 'cesium'
import { XCUBE_MAX_TILE_LEVEL, xcubeDatasetId, xcubeTileUrlTemplate } from '../lib/xcube'
import type { MosaicBand } from './MosaicLayer'

// Same coverage extent as MosaicLayer.
const COVERAGE_RECTANGLE = Rectangle.fromDegrees(-180, -66.49102, 180, 66.49102)

// Fixed categorical color scheme for satellite_id_<band>'s 7 flag
// values (0 = no_data, 1-6 = one physical satellite each -- these are
// IDENTICAL across all 4 bands via THREDDS's own OPeNDAP DAS
// output for satellite_id_mir/wv/tir1/tir2, despite each band's
// flag_values/flag_meanings attribute pair listing entries in a different
// on-disk order -- see SATELLITE_LEGEND_COLORS in LayerSwitcher.tsx for
// the resolved flag-value -> name -> color table).
//
// xcube's tile API has no per-value fixed-color mechanism like THREDDS/
// EDAL's `default-categorical` WMS style (no custom
// colormap is registered server-side for this, `GET /colorbars` only
// lists stock matplotlib maps) -- `norm=cat` with a 10-entry qualitative
// map (`tab10`) and `vmin=0`/`vmax=6` is the closest available substitute.
// Sampling 3 of 7 values directly from a tile's
// pixels matched exactly: xcube maps value v to
// `tab10[floor((v-vmin)/(vmax-vmin) * 10)]` (clamped to index 9) --
// standard matplotlib "continuous normalize into a discrete ListedColormap"
// behavior, not literally a per-category lookup, so vmin/vmax must stay
// exactly 0/6 for this specific mapping to hold.
const SATELLITE_CMAP = 'tab10'
const SATELLITE_VMIN = 0
const SATELLITE_VMAX = 6

interface ContributionMaskLayerProps {
  band?: MosaicBand
  // See MosaicLayer.tsx's same prop for why this is required in practice
  // despite the optional type.
  time?: string
  alpha?: number
  show?: boolean
  // See MosaicLayer.tsx's same prop -- live sync / "show always recent"
  // cache-busting.
  cacheBust?: number
}

// Fourth layer -- satellite contribution mask, fixed per-satellite colors:
// renders satellite_id_<band>
// (which satellite contributed each pixel) as a categorical color overlay.
// Switched from THREDDS WMS to xcube's tile API 2026-08-15, same as
// MosaicLayer.tsx -- see that file's comment for the full history.
function ContributionMaskLayer({
  band = 'tir1',
  time,
  alpha = 1,
  show = true,
  cacheBust,
}: ContributionMaskLayerProps) {
  const provider = useMemo(() => {
    if (!time) return undefined

    const params: Record<string, string> = {
      cmap: SATELLITE_CMAP,
      vmin: String(SATELLITE_VMIN),
      vmax: String(SATELLITE_VMAX),
      norm: 'cat',
    }
    if (cacheBust !== undefined) params._sync = String(cacheBust)

    return new UrlTemplateImageryProvider({
      url: xcubeTileUrlTemplate(xcubeDatasetId(time), `satellite_id_${band}`, params),
      tilingScheme: new GeographicTilingScheme(),
      tileWidth: 256,
      tileHeight: 256,
      rectangle: COVERAGE_RECTANGLE,
      // See lib/xcube.ts's XCUBE_MAX_TILE_LEVEL and MosaicLayer.tsx's same
      // setting -- same source grid, same cap.
      maximumLevel: XCUBE_MAX_TILE_LEVEL,
      enablePickFeatures: false,
    })
  }, [band, time, cacheBust])

  if (!provider) return null

  // Explicit `index` (position within `viewer.imageryLayers`, distinct
  // from any per-satellite index elsewhere in this file) -- without it,
  // resium/Cesium append a *recreated* layer to the end of the stack
  // (`ImageryLayerCollection.add` with no index just pushes), not back to
  // its original JSX-order position. `imageryProvider` is a read-only
  // Cesium prop, so any change to `band`/`time` (this layer) or `band`/
  // `time`/`palette`/`colorMin`/`colorMax` (MosaicLayer.tsx) destroys and
  // recreates the whole layer, not just an in-place update -- this is an
  // easy-to-trigger case, not a hypothetical: once
  // the legend min/max started auto-refreshing after band/timestamp
  // changes (a live WMS fetch resolving shortly after mount), MosaicLayer
  // kept recreating without an index and kept landing on top of this
  // layer, even though it renders earlier in the JSX tree. Explicit
  // indices make each layer self-heal back into its correct position on
  // every recreation, regardless of which layer changed or how their
  // async `create()` calls interleave (`ImageryLayerCollection.add`
  // inserts via `splice`, i.e. "before whatever's currently at this
  // index," not an overwrite) -- this holds even for a
  // band switch, which recreates both layers together. Must stay
  // consistent with MosaicLayer.tsx's `index={1}` (this layer goes above
  // it, hence `2`) and the basemap's implicit `index={0}`.
  return <ImageryLayer imageryProvider={provider} alpha={alpha} show={show} index={2} />
}

export default ContributionMaskLayer
