import type { CSSProperties } from 'react'
import Panel from './Panel'
import { formatDateTime, type TimeDisplayMode } from '../lib/time'

interface AnimationControlsProps {
  timestamps: string[]
  index: number
  onIndexChange: (index: number) => void
  playing: boolean
  onPlayingChange: (playing: boolean) => void
  intervalMs: number
  onIntervalMsChange: (intervalMs: number) => void
  timeDisplay: TimeDisplayMode
  // See ControlPanel.tsx's own `bare` prop for the full reasoning -- same
  // mobile-combined-panel embedding.
  bare?: boolean
}

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
  // without this, the slider refuses to shrink to fit a narrow mobile
  // panel, overflowing its right edge instead of resizing with it. Same
  // bug already found and fixed once for geosat-mosaic-cesium's/
  // geosat-mosaic-openlayers's own TimeControls.tsx -- added here
  // preemptively rather than waiting to hit it again, now that this panel
  // can be embedded in a narrower mobile combined panel.
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

const SPEED_OPTIONS: [number, string][] = [
  [2000, '0.5x'],
  [1000, '1x'],
  [500, '2x'],
  [250, '4x'],
]

// Scrubber + play/pause + speed, over the shared current-timestamp index --
// same shape as geosat-mosaic's TimeControls.tsx, minus its Cesium-tile
// pacing concerns (see useAnimationDriver.ts for this app's equivalent
// load-gating).
function AnimationControls({
  timestamps,
  index,
  onIndexChange,
  playing,
  onPlayingChange,
  intervalMs,
  onIntervalMsChange,
  timeDisplay,
  bare,
}: AnimationControlsProps) {
  if (timestamps.length === 0) {
    const loading = <>Loading available timestamps…</>
    return bare ? loading : <Panel title="Animation">{loading}</Panel>
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

  return bare ? fields : <Panel title="Animation">{fields}</Panel>
}

export default AnimationControls
