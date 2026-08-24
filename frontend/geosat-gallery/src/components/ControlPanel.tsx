import type { CSSProperties } from 'react'
import Panel from './Panel'
import { ALL_BANDS, BAND_LABELS, type GalleryBand } from '../lib/gallery'
import type { TimeDisplayMode } from '../lib/time'

interface ControlPanelProps {
  band: GalleryBand
  onBandChange: (band: GalleryBand) => void
  syncEnabled: boolean
  onSyncEnabledChange: (value: boolean) => void
  showAlwaysRecent: boolean
  onShowAlwaysRecentChange: (value: boolean) => void
  showAlwaysRecentDisabled: boolean
  timeDisplay: TimeDisplayMode
  onTimeDisplayChange: (mode: TimeDisplayMode) => void
  // On a narrow phone screen this is embedded directly inside one shared
  // combined panel (App.tsx) alongside Animation/Date instead of floating
  // as its own separate "Controls" panel. `bare` skips this component's
  // own <Panel> chrome so it doesn't nest a panel inside a panel -- same
  // pattern as geosat-mosaic's TimeControls.tsx/CalendarControl.tsx.
  bare?: boolean
}

const fieldStyle: CSSProperties = {
  width: '100%',
  marginBottom: 8,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

const checkboxRowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  marginBottom: 6,
}

// Band select, sync/"show always recent" toggles, and the GMT/local time
// display toggle -- all the app-wide, not-per-frame controls in one panel.
// The animation-interlock rules keep band
// selection enabled during playback (unlike GeoSat-MOSAIC) -- only
// "show always recent" gets disabled, via `showAlwaysRecentDisabled`
// (App.tsx also force-unchecks it the instant playback starts, more
// specific than GeoSat-MOSAIC's "disabled but stays checked" rule for the
// same control -- see App.tsx's own comment on why).
function ControlPanel({
  band,
  onBandChange,
  syncEnabled,
  onSyncEnabledChange,
  showAlwaysRecent,
  onShowAlwaysRecentChange,
  showAlwaysRecentDisabled,
  timeDisplay,
  onTimeDisplayChange,
  bare,
}: ControlPanelProps) {
  const fields = (
    <>
      <select
        style={fieldStyle}
        value={band}
        onChange={(event) => onBandChange(event.target.value as GalleryBand)}
      >
        {ALL_BANDS.map((option) => (
          <option key={option} value={option}>
            {BAND_LABELS[option]}
          </option>
        ))}
      </select>
      <label style={checkboxRowStyle}>
        <input
          type="checkbox"
          checked={syncEnabled}
          onChange={(event) => onSyncEnabledChange(event.target.checked)}
        />
        Sync
      </label>
      <label style={checkboxRowStyle}>
        <input
          type="checkbox"
          checked={showAlwaysRecent}
          disabled={showAlwaysRecentDisabled}
          onChange={(event) => onShowAlwaysRecentChange(event.target.checked)}
        />
        Show always recent
      </label>
      <label style={checkboxRowStyle}>
        <input
          type="checkbox"
          checked={timeDisplay === 'local'}
          onChange={(event) => onTimeDisplayChange(event.target.checked ? 'local' : 'utc')}
        />
        Local time
      </label>
    </>
  )

  return bare ? fields : <Panel title="Controls">{fields}</Panel>
}

export default ControlPanel
