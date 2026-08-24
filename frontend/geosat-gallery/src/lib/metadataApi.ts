// Small FastAPI service (geosat-mosaic-api/, sibling to both frontend
// projects) backing the info button's per-satellite metadata table -- same
// service GeoSat-MOSAIC's own info button uses (see that project's
// metadataApi.ts for the full THREDDS-can't-serve-this rationale). Only
// used here for the on-demand info panel, never per animation frame --
// see lib/gallery.ts's own note on why per-frame thumbnail URL resolution
// deliberately does NOT go through this backend.
import { getMetadataApiUrl } from './runtimeBackendConfig'
const METADATA_API_URL: string = getMetadataApiUrl()

export interface SatelliteMetadataEntry {
  key: string
  longitude: number
  scan_start: string
  scan_mid: string
  scan_end: string
  // Band center wavelength, per-satellite -- null when this slot's file
  // genuinely doesn't have it set (a masked/fill value in the raw .nc).
  band_wavelength_um: number | null
  // Nominal nadir spatial resolution, per-satellite (fixed by that
  // satellite's sensor design) -- distinct from the per-pixel
  // pixel_resolution_km GeoSat-MOSAIC's point-probe uses, which degrades
  // away from nadir. Real .nc var (satellite_spatial_resolution_km_
  // <band>), added to the backend 2026-08-14 for this info panel.
  spatial_resolution_km: number | null
}

export interface SatelliteMetadata {
  filename: string
  band: string
  actual_mid_time: string
  satellites: SatelliteMetadataEntry[]
}

export async function fetchSatelliteMetadata(
  band: string,
  time: string,
): Promise<SatelliteMetadata> {
  const url = new URL(`${METADATA_API_URL}/satellite-metadata`)
  url.searchParams.set('band', band)
  url.searchParams.set('time', time)
  const response = await fetch(url)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json()
}
