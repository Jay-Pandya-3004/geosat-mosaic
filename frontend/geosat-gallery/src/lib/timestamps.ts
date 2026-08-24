import { MOSAIC_WMS_URL } from './thredds'

// Available timestamps, same authoritative source and shape as
// geosat-mosaic/src/lib/timestamps.ts's fetchAvailableTimestamps -- see
// thredds.ts's own comment on why this WMS GetCapabilities call (not the
// gallery datasetScan catalog) is the source of truth here too. Ascending
// order (oldest first), matching how THREDDS lists them.
export async function fetchAvailableTimestamps(): Promise<string[]> {
  const url = `${MOSAIC_WMS_URL}?service=WMS&version=1.3.0&request=GetCapabilities`
  const response = await fetch(url, { cache: 'no-cache' })
  const xml = await response.text()
  const doc = new DOMParser().parseFromString(xml, 'application/xml')
  const dimension = Array.from(doc.getElementsByTagName('Dimension')).find(
    (element) => element.getAttribute('name') === 'time',
  )
  const raw = dimension?.textContent ?? ''
  return raw
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
}

// "YYYY-MM-DD" portion of an ISO timestamp, UTC -- matches this app's own
// UTC-bucketed calendar grouping (see CalendarControl.tsx, same rationale
// as geosat-mosaic's own CalendarControl.tsx).
export function dateKeyOf(iso: string): string {
  return iso.slice(0, 10)
}

// Ascending, de-duped list of calendar dates present in `timestamps`.
export function availableDates(timestamps: string[]): string[] {
  const seen = new Set<string>()
  const dates: string[] = []
  for (const ts of timestamps) {
    const date = dateKeyOf(ts)
    if (!seen.has(date)) {
      seen.add(date)
      dates.push(date)
    }
  }
  return dates
}

// Nearest calendar date to `targetDate` (may itself have zero
// timestamps -- a native `<input type="date">` lets a user pick ANY date
// in its min/max range, unlike this app's earlier custom calendar-grid
// popup, which disabled unavailable days directly and so never needed
// this). Same as geosat-mosaic-cesium's/geosat-mosaic-openlayers's own
// `lib/timestamps.ts` -- ported back in now that CalendarControl.tsx
// matches their native-input shape instead.
export function nearestAvailableDate(dates: string[], targetDate: string): string {
  const targetEpoch = new Date(targetDate).getTime()
  return dates.reduce((best, candidate) =>
    Math.abs(new Date(candidate).getTime() - targetEpoch) <
    Math.abs(new Date(best).getTime() - targetEpoch)
      ? candidate
      : best,
  )
}

// Absolute index into `timestamps` of the entry on `targetDate` nearest to
// `referenceIso`'s time-of-day -- per-satellite gaps mean the exact
// time-of-day often isn't available on a new date. Returns -1 if
// `targetDate` has no entries in `timestamps`. Same as geosat-mosaic's
// CalendarControl.tsx uses for its own date-jump.
export function nearestIndexForDate(
  timestamps: string[],
  targetDate: string,
  referenceIso: string,
): number {
  const targetEpoch = new Date(`${targetDate}T${referenceIso.slice(11)}`).getTime()
  let bestIndex = -1
  let bestDiff = Infinity
  timestamps.forEach((ts, index) => {
    if (dateKeyOf(ts) !== targetDate) return
    const diff = Math.abs(new Date(ts).getTime() - targetEpoch)
    if (diff < bestDiff) {
      bestDiff = diff
      bestIndex = index
    }
  })
  return bestIndex
}
