// THREDDS base URL — env-driven, keeping backend base
// URLs in env-based config, not hardcoded (dev uses localhost, the
// tunnel URL is unstable across restarts). Set VITE_THREDDS_URL to override.
import { getThreddsBaseUrl } from './runtimeBackendConfig'
export const THREDDS_BASE_URL: string = getThreddsBaseUrl()

// The joinNew aggregation dataset covering the rolling retention window
// (urlPath per catalog.xml: global-mosaic/tierA_rolling.nc).
export const MOSAIC_WMS_URL = `${THREDDS_BASE_URL}/thredds/wms/global-mosaic/tierA_rolling.nc`

// fetchMinMax moved to lib/metadataApi.ts 2026-08-15 -- THREDDS's WMS
// GetMetadata&item=minmax equivalent turned out to be both slow (~26s,
// real per-request raster resampling, not a lightweight metadata call)
// and occasionally wrong (coarse resampling missing genuine extreme
// pixels). See metadataApi.ts's fetchMinMax for the full story.

// Info button "download this file" link -- a second datasetScan
// (catalog.xml's global-mosaic-tiera-raw, separate from the WMS
// aggregation) publishing each raw tiera_<timestamp>.nc individually via
// plain HTTPServer. Needed because the aggregation itself is a virtual
// joinNew dataset with no single physical file behind it --
// /thredds/fileServer/ 404s directly on the aggregation's own urlPath.
// Files are large (500-700MB each), a real direct download, not a quick
// fetch.
const RAW_FILE_BASE_URL = `${THREDDS_BASE_URL}/thredds/fileServer/global-mosaic-tiera-raw`

export function rawFileDownloadUrl(filename: string): string {
  return `${RAW_FILE_BASE_URL}/${filename}`
}

// Info button thumbnail preview -- the per-band gallery PNG for this
// timestamp, via the same datasetScan-based pattern (catalog.xml's
// global-mosaic-gallery, deployed 2026-08-10). Filename convention per
// the actual directory listing: "<band>_<target>_<actual-mid>.png", both
// timestamps compacted to YYYYMMDDTHHMMSS with fractional seconds
// truncated (not rounded -- an actual-mid of 03:55:03.475 produces
// ...035503, not ...035504). "target" is the app's own already-known
// timestamp (it's literally where the WMS aggregation's time dimension
// comes from); the
// actual-mid isn't available anywhere else the frontend can reach (same
// int64/DAP2 gap as the scan-time fields), so it rides along in the
// satellite-metadata API response instead -- see lib/metadataApi.ts's
// `actual_mid_time`.
const GALLERY_BASE_URL = `${THREDDS_BASE_URL}/thredds/fileServer/global-mosaic-gallery`

// Exported for lib/xcube.ts's dataset-id derivation -- xcube's per-file
// datasets use the exact same "tiera_<compact-timestamp>.nc" naming as
// this file's own filename convention (both ultimately match
// geosat-mosaic-api's `_filename_for`).
export function compactTimestamp(iso: string): string {
  return iso.replace(/[-:]/g, '').slice(0, 15)
}

export function mosaicThumbnailUrl(
  band: string,
  targetTime: string,
  actualMidTime: string,
): string {
  const filename = `${band}_${compactTimestamp(targetTime)}_${compactTimestamp(actualMidTime)}.png`
  return `${GALLERY_BASE_URL}/${band}/${filename}`
}
