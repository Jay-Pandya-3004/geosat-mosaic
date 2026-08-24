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
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface CalendarControlProps {
  allTimestamps: string[]
  currentTime: string | undefined
  onJump: (absoluteIndex: number) => void
  disabled?: boolean
  timeDisplay: TimeDisplayMode
  // On a narrow phone screen this is embedded directly inside
  // LayerSwitcher's own "Layers" panel (below the Satellite mosaic
  // section) instead of floating as its own separate "Date" panel, to
  // declutter the mobile layout down to fewer panels. `bare` skips this
  // component's own <Panel> chrome (title bar/
  // border/collapse) so it doesn't nest a panel inside a panel -- same
  // date/time-of-day logic and state either way, just without the
  // wrapper.
  bare?: boolean
}

// No `position`/`width` -- see TimeControls.tsx's own note; this panel is
// a flow child of the same shared flex column wrapper in Globe.tsx.
const panelStyle: CSSProperties = {}

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

// Calendar date + time-of-day picker, the second of the three bound views
// over the shared current-timestamp state (the animation scrubber in
// TimeControls is the first) -- single shared "current timestamp".
// Bounded by the *real* available range
// (`allTimestamps`, the full fetched list), not just Globe's currently-
// loaded animation window -- picking a date outside that window has Globe
// extend the window backward via `onJump`, see Globe.tsx's
// `handleJumpToAbsoluteIndex`.
//
// GMT/UTC-vs-local scoping note: only the *displayed* time-of-day option
// labels respect the toggle (via `formatTimeOfDay`) -- the date grouping
// itself (`dateKeyOf`, `availableDates`, the `<input type="date">` min/max/
// value) stays UTC-bucketed regardless of the toggle. Local display is
// a client-side conversion of the displayed
// value, not a request to re-bucket which timestamps count as the "same
// day" -- doing that would mean a single UTC date's timestamps could span
// two different calendar-picker dates depending on the toggle, which is a
// much bigger behavior change than what was asked for.
function CalendarControl({
  allTimestamps,
  currentTime,
  onJump,
  disabled,
  timeDisplay,
  bare,
}: CalendarControlProps) {
  // See TimeControls.tsx's own comment on this same pattern -- kept
  // consistent with LayerSwitcher.tsx's original `defaultCollapsed=
  // {isNarrow}`.
  const isNarrow = useIsNarrowViewport()

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
    return bare ? (
      loading
    ) : (
      <Panel title="Date" style={panelStyle} defaultCollapsed={isNarrow}>
        {loading}
      </Panel>
    )
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
        disabled={disabled}
        onChange={(event) => jumpToDate(event.target.value)}
        // A native date input's picker, by default, only opens from a
        // click directly on its calendar-icon glyph -- clicking anywhere
        // else in the field just moves the text cursor between its
        // month/day/year segments. Matched to
        // GeoSat-Gallery's own date control (a fully custom calendar
        // popup there, opened by a click anywhere on its trigger
        // button): `showPicker()` (Chrome 99+) opens the *native* picker
        // programmatically, called on every click anywhere in the field,
        // not just the icon. Guarded in a try/catch -- `showPicker()`
        // throws if called without a real, recent user-activation
        // gesture or while `disabled`, and this handler can't tell
        // whether the click that reached it qualifies without asking the
        // browser directly.
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
        disabled={disabled}
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

  return bare ? (
    fields
  ) : (
    <Panel title="Date" style={panelStyle} defaultCollapsed={isNarrow}>
      {fields}
    </Panel>
  )
}

export default CalendarControl
