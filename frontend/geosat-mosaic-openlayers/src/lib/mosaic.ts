import { xcubeDatasetId, xcubeLegendUrl } from './xcube'

// Same 4 bands as geosat-mosaic-cesium's MosaicLayer.tsx.
export type MosaicBand = 'mir' | 'wv' | 'tir1' | 'tir2'

export const BAND_LABELS: Record<MosaicBand, string> = {
  mir: 'MIR',
  wv: 'WV',
  tir1: 'TIR1',
  tir2: 'TIR2',
}

// All 4 bands are brightness temperatures in Kelvin -- same as
// geosat-mosaic-cesium's MOSAIC_UNITS.
export const MOSAIC_UNITS = 'K'

// Colorbar for the current palette/range -- same xcube `/legend` endpoint
// and same portrait-request-then-rotate-90-in-CSS technique
// geosat-mosaic-cesium's MosaicLayer.tsx uses (xcube's gradient always
// varies along the requested `height` axis regardless of aspect ratio,
// confirmed there by decoding actual PNG pixels -- not re-verified
// independently here, this endpoint is identical for both apps).
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
