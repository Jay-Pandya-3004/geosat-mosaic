import type { CSSProperties } from 'react'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface HelpPanelProps {
  open: boolean
  onClose: () => void
}

const panelStyle: CSSProperties = {
  position: 'fixed',
  bottom: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 1,
  width: 520,
  maxWidth: 'calc(100vw - 24px)',
  maxHeight: 'calc(70vh)',
  overflowY: 'auto',
  padding: '14px 16px',
  background: 'rgba(30, 30, 30, 0.96)',
  borderRadius: 6,
  color: '#edeeef',
  font: '13px sans-serif',
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 10,
}

const titleStyle: CSSProperties = {
  fontWeight: 'bold',
  fontSize: 14,
}

const closeButtonStyle: CSSProperties = {
  background: 'transparent',
  color: '#edeeef',
  border: 'none',
  fontSize: 18,
  lineHeight: 1,
  cursor: 'pointer',
  padding: 4,
}

const sectionStyle: CSSProperties = {
  marginBottom: 12,
}

const sectionLabelStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginBottom: 4,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
}

const bodyStyle: CSSProperties = {
  lineHeight: 1.5,
}

const listStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  lineHeight: 1.6,
}

const limitationStyle: CSSProperties = {
  ...bodyStyle,
  color: '#ffcc66',
}

// On a narrow phone screen, tapping outside this panel closes it. Same
// dimmed-backdrop-plus-centered-card pattern
// InfoPanel.tsx/PointProbePanel.tsx already use for their own mobile
// treatment: the backdrop's own onClick closes, and the card stops that
// click from bubbling up so tapping *inside* the panel doesn't also
// close it.
const mobileBackdropStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 10,
  background: 'rgba(0, 0, 0, 0.7)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  padding: 16,
}

const mobileCardStyle: CSSProperties = {
  ...panelStyle,
  position: 'static',
  bottom: undefined,
  left: undefined,
  transform: undefined,
  width: '100%',
  maxWidth: 480,
  maxHeight: '100%',
}

// Replaces Cesium's default Navigation Instructions ("?") popup with
// app-specific content: usage guide, major features, purpose, and the
// recent-data-only limitation, in the same Help button slot. The generic
// mouse/touch camera-control info Cesium's
// own widget showed is folded into "Navigating the map" below rather than
// dropped outright.
function HelpPanel({ open, onClose }: HelpPanelProps) {
  const isNarrow = useIsNarrowViewport()
  if (!open) return null

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>Help</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Purpose</div>
        <div style={bodyStyle}>
          GeoSat-MOSAIC-OpenLayers is a global cloud/imagery mosaic built from 6
          geostationary satellites, reprojected to a common lat/lon grid, with
          per-pixel source selection (minimum time gap, lowest view zenith
          angle) and seam blending at overlaps.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Getting started</div>
        <div style={bodyStyle}>
          Pick an infrared band (MIR/WV/TIR1/TIR2) and a timestamp from the{' '}
          <strong>Time</strong>/<strong>Date</strong> panels, or press Play to
          animate. Use the <strong>Layers</strong> panel (top-left) to toggle
          layers, opacity, colormap, and every other map tool below.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Navigating the map</div>
        <div style={bodyStyle}>
          Left-drag to pan, scroll (or the +/− buttons, top-left) to zoom.
          This is a 2D-only map -- there's no 3D/globe mode to switch to.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Major features</div>
        <ul style={listStyle}>
          <li>4 toggleable layers: basemap, coastline, satellite mosaic, contribution mask</li>
          <li>Animation player with scrubber, adjustable speed, and calendar date-jump</li>
          <li>Colormap/palette picker with user-adjustable min/max</li>
          <li>Point-probe: click the map for a pixel's value and contributing satellite(s)</li>
          <li>Interactive timeseries chart at any probed location</li>
          <li>Distance/area measurement tools and a device-location marker</li>
          <li>Lat/lon graticule overlay and Nominatim place search</li>
          <li>GMT/UTC vs. local time display toggle, applied everywhere at once</li>
          <li>Live sync and "show always recent" to follow newly-arriving data</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Limitation</div>
        <div style={limitationStyle}>
          Only recent data is available — the backend keeps a rolling window of
          roughly the last 7 days, not a full historical archive. Older
          timestamps are not retained and cannot be viewed once they age out of
          that window. Data processing also depends on backend compute
          resource availability, so new timestamps are processed periodically
          as resources free up rather than continuously in real time — the
          application may sometimes show the most recently processed data
          from within that 7-day window instead of always the very latest.
        </div>
      </div>
    </>
  )

  if (isNarrow) {
    return (
      <div style={mobileBackdropStyle} onClick={onClose}>
        <div style={mobileCardStyle} onClick={(event) => event.stopPropagation()}>
          {content}
        </div>
      </div>
    )
  }

  return <div style={panelStyle}>{content}</div>
}

export default HelpPanel
