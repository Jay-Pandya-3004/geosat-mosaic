// GMT/UTC vs. local time toggle -- same one-global-toggle concept as
// geosat-mosaic/src/lib/time.ts. The `.nc`-derived timestamps are already
// UTC -- UTC display just shows the raw value, local display is a
// client-side conversion using the browser's own timezone.
export type TimeDisplayMode = 'utc' | 'local'

// This project's ISO timestamps (e.g. "2026-08-11T04:25:00") don't carry
// an explicit 'Z' suffix -- appending one before handing to `Date` is
// required so it parses as UTC, not the browser's local zone.
function toDate(iso: string): Date {
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`)
}

// Full date + time, minute precision -- the animation's current-frame
// display.
export function formatDateTime(iso: string, mode: TimeDisplayMode): string {
  if (mode === 'utc') return `${iso.slice(0, 16).replace('T', ' ')} UTC`
  return toDate(iso).toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  })
}

// Time-of-day only, minute precision -- the calendar/time-picker dropdown,
// where the date is already selected separately.
export function formatTimeOfDay(iso: string, mode: TimeDisplayMode): string {
  if (mode === 'utc') return `${iso.slice(11, 16)} UTC`
  return toDate(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  })
}

// Time-of-day with seconds -- satellite scan-time displays (info panel).
export function formatScanTime(iso: string, mode: TimeDisplayMode): string {
  if (mode === 'utc') return `${iso.slice(11, 19)} UTC`
  return toDate(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  })
}
