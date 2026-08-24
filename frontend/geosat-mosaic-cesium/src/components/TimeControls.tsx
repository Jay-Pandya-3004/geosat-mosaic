import type { CSSProperties } from 'react'
import Panel from './Panel'
import { formatDateTime, type TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface TimeControlsProps {
  timestamps: string[]
  index: number
  onIndexChange: (index: number) => void
  playing: boolean
  onPlayingChange: (playing: boolean) => void
  intervalMs: number
  onIntervalMsChange: (intervalMs: number) => void
  timeDisplay: TimeDisplayMode
  // On a narrow phone screen this is embedded directly inside
  // LayerSwitcher's own "Layers" panel instead of floating as its own
  // separate "Time" panel (or simply not rendered, which is what this
  // app used to do on mobile). `bare`
  // skips this component's own <Panel> chrome so it doesn't nest a
  // panel inside a panel -- same scrubber/play/speed logic either way,
  // just without the wrapper. Same pattern as CalendarControl.tsx's own
  // `bare` prop (and geosat-mosaic-openlayers's own TimeControls.tsx,
  // which got this same treatment first).
  bare?: boolean
}

// No `position`/`width` here -- this panel is a flow child inside a
// shared flex column wrapper in Globe.tsx (alongside CalendarControl/
// PlaceSearch), which owns both the actual fixed positioning for the
// whole stack AND the (viewport-responsive) width -- flex's default
// `align-items: stretch` makes this element fill that width automatically
// without needing its own copy of the same value. Collapsing any one of
// the three no longer needs a hardcoded pixel offset recalculated for the
// ones below it either -- the flex column just reflows.
const panelStyle: CSSProperties = {}

const timestampStyle: CSSProperties = {
  marginBottom: 6,
  fontVariantNumeric: 'tabular-nums',
}

const rowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
}

const buttonStyle: CSSProperties = {
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '2px 8px',
  cursor: 'pointer',
}

const sliderStyle: CSSProperties = {
  flex: 1,
  // Flex items default to `min-width: auto`, which for a <input
  // type="range"> means its browser-default intrinsic width (~170px) --
  // without this, the slider refuses to shrink to fit this row on a
  // narrow phone panel (`min(200px, 45vw)`, see LayerSwitcher.tsx's own
  // note on that width), overflowing the panel's right edge instead of
  // resizing with it.
  minWidth: 0,
}

const selectStyle: CSSProperties = {
  width: '100%',
  marginTop: 6,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

// ms per frame -> label -- animation player
// controls: scrubber slider, play/pause, speed, adjustable playback
// rate, not a fixed single speed.
const SPEED_OPTIONS: [number, string][] = [
  [2000, '0.5x'],
  [1000, '1x'],
  [500, '2x'],
  [250, '4x'],
]

// Scrubber + play/pause + speed -- animation player
// controls. `index` is the shared current-timestamp state (see the
// "Single shared 'current timestamp'" state) -- this component is a pure
// view over it, same as the eventual calendar/time-dropdown controls will
// be once built (not yet -- this first pass only covers scrubbing within
// the fixed last-96-timestamps window, not date-jumping or extending the
// range further back).
function TimeControls({
  timestamps,
  index,
  onIndexChange,
  playing,
  onPlayingChange,
  intervalMs,
  onIntervalMsChange,
  timeDisplay,
  bare,
}: TimeControlsProps) {
  // Same reasoning as LayerSwitcher.tsx's own `defaultCollapsed={isNarrow}`
  // -- on a narrow phone screen, this panel (and Date/Search below it in
  // the same flex column) started expanded by default and obstructed a
  // large chunk of the map. Only
  // affects the *initial* collapsed state (Panel's own `useState`), not a
  // live-controlled prop -- the user can still expand/collapse freely
  // either way, same as every other panel in this app.
  const isNarrow = useIsNarrowViewport()

  if (timestamps.length === 0) {
    const loading = <>Loading available timestamps…</>
    return bare ? (
      loading
    ) : (
      <Panel title="Time" style={panelStyle} defaultCollapsed={isNarrow}>
        {loading}
      </Panel>
    )
  }

  const fields = (
    <>
      <div style={timestampStyle}>{formatDateTime(timestamps[index], timeDisplay)}</div>
      <div style={rowStyle}>
        <button style={buttonStyle} onClick={() => onPlayingChange(!playing)}>
          {playing ? 'Pause' : 'Play'}
        </button>
        <input
          style={sliderStyle}
          type="range"
          min={0}
          max={timestamps.length - 1}
          step={1}
          value={index}
          onChange={(event) => onIndexChange(Number(event.target.value))}
        />
      </div>
      <select
        style={selectStyle}
        value={intervalMs}
        onChange={(event) => onIntervalMsChange(Number(event.target.value))}
      >
        {SPEED_OPTIONS.map(([ms, label]) => (
          <option key={ms} value={ms}>
            {label}
          </option>
        ))}
      </select>
    </>
  )

  return bare ? (
    fields
  ) : (
    <Panel title="Time" style={panelStyle} defaultCollapsed={isNarrow}>
      {fields}
    </Panel>
  )
}

export default TimeControls
