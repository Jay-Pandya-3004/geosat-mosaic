// GMT/UTC vs. local time toggle -- applies everywhere at once: one shared toggle
// state, not per-widget ones that could drift out of sync. The `.nc`
// files' own timestamps are already real UTC -- UTC display is just
// showing the raw value, local display is a client-side conversion using
// the browser's own timezone, not a second value fetched from the
// backend.
export type TimeDisplayMode = 'utc' | 'local'

// This project's ISO timestamps (e.g. "2026-08-11T04:25:00") don't carry
// an explicit 'Z' suffix -- appending one before handing to `Date` is
// required so it parses as UTC, not the browser's local zone (which is
// `Date`'s default for a timezone-less ISO string).
function toDate(iso: string): Date {
  return new Date(iso.endsWith('Z') ? iso : `${iso}Z`)
}

// Full date + time, minute precision -- the animation's current-frame
// display (TimeControls.tsx).
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

// Time-of-day only, minute precision -- the calendar/time-picker dropdown
// (CalendarControl.tsx), where the date is already selected separately.
export function formatTimeOfDay(iso: string, mode: TimeDisplayMode): string {
  if (mode === 'utc') return `${iso.slice(11, 16)} UTC`
  return toDate(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    timeZoneName: 'short',
  })
}

// Time-of-day with seconds -- satellite scan-time displays (point-probe,
// timeseries hover, info panel).
export function formatScanTime(iso: string, mode: TimeDisplayMode): string {
  if (mode === 'utc') return `${iso.slice(11, 19)} UTC`
  return toDate(iso).toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    timeZoneName: 'short',
  })
}
