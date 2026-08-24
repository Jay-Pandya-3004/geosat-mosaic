// THREDDS base URL -- same env-driven pattern as geosat-mosaic's thredds.ts
// (dev uses localhost, the tunnel URL is unstable across restarts). Set
// VITE_THREDDS_URL to override.
import { getThreddsBaseUrl } from './runtimeBackendConfig'
export const THREDDS_BASE_URL: string = getThreddsBaseUrl()

// Same rolling-aggregation WMS endpoint geosat-mosaic uses, but this app
// never renders WMS tiles -- it's only queried for GetCapabilities, as the
// authoritative source of which timestamps exist (GeoSat-Gallery reuses
// the same underlying authoritative-timestamps source as GeoSat-MOSAIC).
// As of 2026-08-13 the WMS time dimension count (213) exactly matches the
// gallery datasetScan's per-band file count -- the gallery pipeline
// writes one PNG per band per tiera timestamp, so this list is safe to
// reuse as-is.
export const MOSAIC_WMS_URL = `${THREDDS_BASE_URL}/thredds/wms/global-mosaic/tierA_rolling.nc`

// Info panel "download this file" link -- same datasetScan (catalog.xml's
// global-mosaic-tiera-raw) GeoSat-MOSAIC's own info button uses. Files are
// large (500-700MB each), a direct download, not a quick fetch.
const RAW_FILE_BASE_URL = `${THREDDS_BASE_URL}/thredds/fileServer/global-mosaic-tiera-raw`

export function rawFileDownloadUrl(filename: string): string {
  return `${RAW_FILE_BASE_URL}/${filename}`
}
