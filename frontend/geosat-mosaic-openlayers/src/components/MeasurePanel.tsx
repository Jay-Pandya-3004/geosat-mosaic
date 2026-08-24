import type { CSSProperties } from 'react'
import {
  formatArea,
  formatDistance,
  polygonAreaSquareMeters,
  totalDistanceMeters,
  type LonLat,
  type MeasureMode,
} from '../lib/geometry'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface MeasurePanelProps {
  mode: MeasureMode
  points: LonLat[]
  onClear: () => void
}

// `top: 12` is fine on desktop (this panel is horizontally centered, its
// edges stay well clear of the top-right About/Help/zoom button cluster
// on any wide viewport) but not on a narrow phone screen -- this panel's
// own width (up to `calc(100vw - 24px)`) reaches under that cluster
// there. `top: 84` clears both button rows (About/Help, then the zoom
// row below it -- see Map.tsx's own `aboutHelpRowStyle`/`zoomRowStyle`)
// on narrow viewports specifically. `zIndex: 2`, not the `1` every other
// fixed panel in this app uses -- belt-and-suspenders on top of the `top`
// fix above, so this panel always paints above the button cluster
// regardless of DOM order even if the `top` offset ever falls short
// (e.g. a future button added to that row).
function panelStyle(isNarrow: boolean): CSSProperties {
  return {
    position: 'fixed',
    top: isNarrow ? 84 : 12,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 2,
    width: 260,
    maxWidth: 'calc(100vw - 24px)',
    padding: '10px 12px',
    background: 'rgba(30, 30, 30, 0.96)',
    borderRadius: 6,
    color: '#edeeef',
    font: '13px sans-serif',
  }
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 6,
}

const titleStyle: CSSProperties = {
  fontWeight: 'bold',
}

const clearButtonStyle: CSSProperties = {
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '2px 8px',
  cursor: 'pointer',
  fontSize: 12,
}

const valueStyle: CSSProperties = {
  fontSize: 20,
}

const hintStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginTop: 4,
}

// Result display for the measurement tools -- pure display, all the
// actual geodesic math lives in lib/geometry.ts. Rendered whenever
// measure mode is on (Map.tsx), regardless of point count -- shows a
// hint until there's enough points for a result instead of just not
// appearing, so the user always has feedback that measure mode is
// active. Same UI as geosat-mosaic-cesium's own MeasurePanel.tsx, just
// `LonLat` points instead of `Cartesian3`.
function MeasurePanel({ mode, points, onClear }: MeasurePanelProps) {
  const isNarrow = useIsNarrowViewport()
  const label = mode === 'distance' ? 'Distance' : 'Area'
  const value =
    mode === 'distance'
      ? formatDistance(totalDistanceMeters(points))
      : formatArea(polygonAreaSquareMeters(points))
  const minPoints = mode === 'distance' ? 2 : 3

  return (
    <div style={panelStyle(isNarrow)}>
      <div style={headerStyle}>
        <span style={titleStyle}>{label}</span>
        <button style={clearButtonStyle} onClick={onClear} disabled={points.length === 0}>
          Clear
        </button>
      </div>
      {points.length >= minPoints ? (
        <div style={valueStyle}>{value}</div>
      ) : (
        <div style={hintStyle}>
          Click the map to place points ({mode === 'distance' ? 'at least 2' : 'at least 3'}{' '}
          needed).
        </div>
      )}
      {points.length > 0 && points.length < minPoints && (
        <div style={hintStyle}>{points.length} point placed so far.</div>
      )}
    </div>
  )
}

export default MeasurePanel
