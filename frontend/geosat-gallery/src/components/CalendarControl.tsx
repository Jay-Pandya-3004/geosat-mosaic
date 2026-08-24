import type { CSSProperties } from 'react'
import { useMemo, useState } from 'react'
import Panel from './Panel'
import {
  availableDates,
  dateKeyOf,
  nearestAvailableDate,
  nearestIndexForDate,
} from '../lib/timestamps'
import { formatTimeOfDay, type TimeDisplayMode } from '../lib/time'

interface CalendarControlProps {
  allTimestamps: string[]
  currentTime: string | undefined
  onJump: (absoluteIndex: number) => void
  timeDisplay: TimeDisplayMode
  // On a narrow phone screen this is embedded directly inside App.tsx's
  // shared mobile Controls panel instead of floating as its own separate
  // "Date" panel. `bare` skips this component's own <Panel> chrome --
  // same pattern this component's own predecessor (CalendarPopup.tsx)
  // and geosat-mosaic-cesium's/geosat-mosaic-openlayers's own
  // CalendarControl.tsx already use.
  bare?: boolean
}

const fieldStyle: CSSProperties = {
  width: '100%',
  marginBottom: 6,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

// A native <input type="date">'s min/max only bounds a continuous range --
// it can't gray out individual gap days that have zero real timestamps
// (see lib/timestamps.ts's nearestAvailableDate comment), so picking one
// silently snaps to the nearest real date instead of erroring. This makes
// that snap visible instead of silent.
const snapNoticeStyle: CSSProperties = {
  fontSize: 11,
  color: '#e0c15c',
  marginBottom: 6,
}

// Native <input type="date"> + a separate always-visible time-of-day
// <select>, matching the same calendar pattern as geosat-mosaic-cesium's/
// geosat-mosaic-openlayers's own CalendarControl.tsx -- replaces this
// app's previous custom calendar-grid popup (CalendarPopup.tsx, deleted
// in the same change). Same shared current-timestamp-state role as
// before, unchanged: bounded by the real available range
// (`allTimestamps`), not just the animation's currently-loaded window.
function CalendarControl({
  allTimestamps,
  currentTime,
  onJump,
  timeDisplay,
  bare,
}: CalendarControlProps) {
  const dates = useMemo(() => availableDates(allTimestamps), [allTimestamps])
  const currentDate = currentTime ? dateKeyOf(currentTime) : undefined
  const [snapNotice, setSnapNotice] = useState<string | null>(null)

  const timeOptions = useMemo(
    () =>
      allTimestamps
        .map((ts, index) => ({ ts, index }))
        .filter((option) => dateKeyOf(option.ts) === currentDate),
    [allTimestamps, currentDate],
  )

  if (allTimestamps.length === 0 || !currentDate) {
    const loading = <>Loading available timestamps…</>
    return bare ? loading : <Panel title="Date">{loading}</Panel>
  }

  function jumpToDate(pickedDate: string) {
    if (!currentTime) return
    const resolvedDate = dates.includes(pickedDate)
      ? pickedDate
      : nearestAvailableDate(dates, pickedDate)
    setSnapNotice(
      resolvedDate === pickedDate
        ? null
        : `No data for ${pickedDate} — showing nearest available: ${resolvedDate}`,
    )
    const index = nearestIndexForDate(allTimestamps, resolvedDate, currentTime)
    if (index !== -1) onJump(index)
  }

  const fields = (
    <>
      {snapNotice && <div style={snapNoticeStyle}>{snapNotice}</div>}
      <input
        className="date-input-white-icon"
        style={fieldStyle}
        type="date"
        value={currentDate}
        min={dates[0]}
        max={dates[dates.length - 1]}
        onChange={(event) => jumpToDate(event.target.value)}
        // A native date input's picker, by default, only opens from a
        // click directly on its calendar-icon glyph -- clicking anywhere
        // else in the field just moves the text cursor between its
        // month/day/year segments. `showPicker()` (Chrome 99+) opens the
        // native picker programmatically on every click in the field, not
        // just the icon -- same fix already applied to geosat-mosaic-
        // cesium's/geosat-mosaic-openlayers's own CalendarControl.tsx.
        // Guarded in a try/catch -- `showPicker()` throws without a real,
        // recent user-activation gesture.
        onClick={(event) => {
          try {
            event.currentTarget.showPicker()
          } catch {
            // Not a real user-activation gesture (or the picker is
            // already open) -- no-op, same as a plain click landing
            // outside the calendar-icon glyph did before this change.
          }
        }}
      />
      <select
        style={fieldStyle}
        value={timeOptions.find((option) => option.ts === currentTime)?.index ?? ''}
        onChange={(event) => onJump(Number(event.target.value))}
      >
        {timeOptions.map((option) => (
          <option key={option.index} value={option.index}>
            {formatTimeOfDay(option.ts, timeDisplay)}
          </option>
        ))}
      </select>
    </>
  )

  return bare ? fields : <Panel title="Date">{fields}</Panel>
}

export default CalendarControl
