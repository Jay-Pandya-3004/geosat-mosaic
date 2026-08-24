import { MOSAIC_WMS_URL } from './thredds'

// Available timestamps for the aggregated dataset, shared identically
// across every layer in it (mosaic bands, satellite_id, etc all join the
// same time dimension, per the catalog.xml aggregation
// setup), so any one layer's GetCapabilities entry is authoritative for
// all of them. Ascending order (oldest first), matching how THREDDS lists
// them.
//
// `cache: 'no-cache'` (not `no-store`) -- browser
// caching vs. sync/"show always recent": this is the one request that
// genuinely needs cache-busting (live sync/show-always-recent poll it
// specifically to discover *change*), forcing revalidation with the
// server rather than silently reusing a stale response, while still
// getting a cheap 304 when nothing's actually changed *if* THREDDS sends
// proper ETag/Last-Modified headers here -- not independently verified
// whether it does, so this may in practice behave like a
// full re-fetch every time rather than a conditional one; either way it's
// correct (never stale), just not confirmed to be the cheaper of the two.
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

// "YYYY-MM-DD" portion of an ISO timestamp, UTC (matches the "...UTC"
// formatting TimeControls already uses -- no local-time toggle yet, that
// was a future item at the time).
export function dateKeyOf(iso: string): string {
  return iso.slice(0, 10)
}

// Ascending, de-duped list of calendar dates present in `timestamps`, for
// bounding a date picker's min/max -- on page load,
// default selection, and the calendar/timestamp range is bounded by real
// data.
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

// Nearest entry of `dates` to `targetDate` -- needed because a native
// `<input type="date">`'s min/max only restricts a continuous range, it
// can't exclude individual gap days that have zero real timestamps.
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
// `referenceIso`'s time-of-day -- calendar date
// change snaps to the nearest available timestamp: real per-satellite
// gaps mean the exact time-of-day often isn't available on a new date.
// Returns -1 if `targetDate` has no entries in `timestamps`.
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
