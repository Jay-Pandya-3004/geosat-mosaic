// Small FastAPI service (geosat-mosaic-api/, sibling to this frontend
// project) backing the info button's per-satellite metadata table.
// THREDDS genuinely cannot serve this data to a browser: satellite_key_/
// satellite_longitude_/satellite_scan_start|mid|end_<band> are all
// dimensioned by satellite_<band> (6 slots), not lat/lon, so NCSS's grid
// endpoint refuses them outright ("is not contained in the requested
// dataset") -- and the scan-time fields are int64/
// datetime64, which classic OPeNDAP/DAP2 can't serialize at all (the same
// reason they're stripped from the WMS/OPeNDAP aggregation in
// catalog.xml). This service reads the raw tiera_<timestamp>.nc file
// directly with netCDF4 instead. Dev-only for now, not yet behind a
// tunnel.
import { getMetadataApiUrl } from './runtimeBackendConfig'
const METADATA_API_URL: string = getMetadataApiUrl()

export interface SatelliteMetadataEntry {
  key: string
  longitude: number
  scan_start: string
  scan_mid: string
  scan_end: string
  // Band center wavelength for this satellite's sensor -- per-satellite,
  // not per-pixel (a sensor's band has one fixed wavelength). Null when
  // this slot's file genuinely doesn't have it set (a masked/fill value
  // in the raw .nc, not a frontend omission).
  band_wavelength_um: number | null
}

export interface SatelliteMetadata {
  filename: string
  band: string
  // The band's own median scan mid-time (time_<band> in the raw
  // file) -- differs from the requested target time embedded in the
  // filename/app's own `time` state. Needed (not just informational) to
  // construct the gallery thumbnail URL -- see thredds.ts's
  // mosaicThumbnailUrl.
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

// Live min/max for a band's *current* timestamp, over this app's
// coverage extent -- backs the colorbar's "reset to auto range" button.
// Moved here from lib/thredds.ts 2026-08-15 after two problems turned up
// with THREDDS's WMS GetMetadata&item=minmax equivalent (found by
// decompiling the installed EDAL library):
//   1. Slow -- ~26s for this app's near-global bbox, since THREDDS
//      resamples the grid down to the request's width/height before
//      computing min/max, not a lightweight metadata call. Under load,
//      this can hang outright rather than just being slow (15-20s+
//      timeouts with zero response) -- and since
//      MosaicLayer/ContributionMaskLayer wait for this to resolve before
//      rendering any tiles (see Globe.tsx's tileBand/tileTime), a hung
//      THREDDS minmax call blocks tile loading entirely even though
//      xcube (the actual tile server) is completely unaffected.
//   2. Wrong -- that same coarse resampling can miss genuine extreme
//      pixels the full-resolution grid actually has: THREDDS
//      reported -1.921/352.6 for a file whose true full-array min/max is
//      -2.599/400.126 -- a ~0.7 miss on the low end, not noise.
// This endpoint reads the raw file's full native array directly instead
// (same DATA_DIR/netCDF4 approach as /point-probe), ~3.5-4s and
// provably correct -- no bbox/width/height/crs/styles params needed,
// this file's own lat/lon coordinates already span exactly this app's
// coverage extent. Also corrects an earlier wrong guess in this
// codebase blaming THREDDS's -1.921 on "an unmasked fill-value pixel" --
// it wasn't; value_<band>'s scale_factor/add_offset are computed from
// the file's own true min/max, so the packed int16 extremes decode to
// real data by construction, never a fill value, for this pipeline.
export async function fetchMinMax(
  band: string,
  time: string,
): Promise<{ min: number; max: number }> {
  const url = new URL(`${METADATA_API_URL}/minmax`)
  url.searchParams.set('band', band)
  url.searchParams.set('time', time)
  const response = await fetch(url)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json()
}

export interface PointProbeResult {
  filename: string
  band: string
  // The actual grid cell resolved (nearest-neighbor to the clicked lat/
  // lon), not necessarily exactly what was clicked.
  lat: number
  lon: number
  // Both null together at a genuine no-data pixel (masked value, flag 0 --
  // these coincide, e.g. mid-ocean gaps in coverage).
  value: number | null
  // Actual per-pixel ground resolution (pixel_resolution_km_<band> in the
  // raw .nc), accounting for view-zenith-angle degradation -- not the
  // fixed nominal nadir figure, genuinely varies pixel to pixel.
  pixel_resolution_km: number | null
  primary: SatelliteMetadataEntry | null
  is_blended: boolean
  // Only non-null when is_blended is true -- the point-probe blended-pixel
  // nuance: the pixel is a weighted mix of
  // primary and runner_up, split by blend_weight_winner (primary's share).
  runner_up: SatelliteMetadataEntry | null
  blend_weight_winner: number | null
}

// Deliberately does NOT go through THREDDS's NCSS grid-as-point endpoint
// -- that's unsafe against this dataset (can silently return the wrong
// pixel, or crash Tomcat with an OutOfMemoryError, on both the
// aggregated and raw-file datasets). This FastAPI endpoint does its own
// nearest-neighbor lookup directly against the raw file's lat/lon
// coordinate arrays instead.
//
// Triggered by a phone report ("sometimes point probe just gets
// stuck") -- plain `fetch()` has no timeout, so a request that never
// gets a response at all (a flaky mobile connection, backgrounding the
// tab mid-request, a dropped Wi-Fi/cellular handoff) leaves the returned
// promise pending forever: no `.then`, no `.catch`, no `.finally` --
// Globe.tsx's probe-fetch effect is written correctly (it does reset
// `probeLoading` in its `.finally()`), but that `.finally()` can only
// run once the promise actually settles, which a hung `fetch()` never
// does on its own. `AbortController` + `setTimeout` forces a
// rejection after a bound, so the effect's existing `.catch()`/
// `.finally()` get a chance to run and the UI can show a "timed
// out" error instead of spinning indefinitely.
export async function fetchPointProbe(
  band: string,
  time: string,
  lat: number,
  lon: number,
): Promise<PointProbeResult> {
  const url = new URL(`${METADATA_API_URL}/point-probe`)
  url.searchParams.set('band', band)
  url.searchParams.set('time', time)
  url.searchParams.set('lat', String(lat))
  url.searchParams.set('lon', String(lon))
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 20_000)
  let response: Response
  try {
    response = await fetch(url, { signal: controller.signal })
  } catch (err) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new Error('Point probe request timed out -- check your connection and try again')
    }
    throw err
  } finally {
    clearTimeout(timeout)
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json()
}

export interface TimeseriesPoint {
  time: string
  value: number | null
  // Same as PointProbeResult's field -- see its own comment.
  pixel_resolution_km: number | null
  primary: SatelliteMetadataEntry | null
  is_blended: boolean
  runner_up: SatelliteMetadataEntry | null
  blend_weight_winner: number | null
  // How many of the satellite slots this specific timestamp's file
  // actually had (the reprocessing-disclaimer feature's
  // informational half only). No fixed "out of N" here -- see
  // thredds.ts/geosat-mosaic-api's own notes on why that's deliberately
  // not hardcoded; the frontend derives "out of how many" from the max
  // seen across the fetched series instead.
  satellite_count: number
  // Present instead of the above fields when this specific timestamp
  // couldn't be probed (e.g. no file for that exact time) -- surfaced
  // per-point rather than failing the whole request.
  error?: string
}

export interface TimeseriesResponse {
  band: string
  lat: number
  lon: number
  points: TimeseriesPoint[]
}

// Timeseries and point-probe -- same
// THREDDS-bypass rationale as point-probe (NCSS grid-as-point is unsafe
// against this dataset), just run across many timestamps. Deliberately
// does NOT implement a "might still change" mutability flag for the
// reprocessing disclaimer -- that would be
// driven by a schedule_pipeline_state.json `partial` dict that doesn't
// exist anywhere in the actual backend pipeline, so there's no data to
// drive it with. The informational satellite-count half of that same feature
// IS implemented (`satellite_count` above).
export async function fetchTimeseries(
  band: string,
  lat: number,
  lon: number,
  times: string[],
): Promise<TimeseriesResponse> {
  const response = await fetch(`${METADATA_API_URL}/timeseries`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ band, lat, lon, times }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail ?? `${response.status} ${response.statusText}`)
  }
  return response.json()
}
