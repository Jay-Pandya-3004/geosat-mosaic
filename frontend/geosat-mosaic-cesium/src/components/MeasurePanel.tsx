import type { CSSProperties } from 'react'
import type { Cartesian3 } from 'cesium'
import {
  formatArea,
  formatDistance,
  polygonAreaSquareMeters,
  totalDistanceMeters,
  type MeasureMode,
} from '../lib/geometry'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface MeasurePanelProps {
  mode: MeasureMode
  points: Cartesian3[]
  onClear: () => void
}

// `zIndex: 2` on a narrow phone screen, one above every other fixed
// panel's `zIndex: 1`, so this panel always
// paints on top there (same pattern as geosat-mosaic-openlayers' own
// MeasurePanel.tsx, which got this same treatment first). Desktop keeps
// the original `1`, unchanged.
//
// `top: 60` on mobile, not `12`:
// this panel is horizontally centered and wide enough (up to `calc(100vw
// - 24px)`) that at phone widths it was landing directly on top of
// the Layers panel's own header/collapse-chevron (also `top: 12`,
// `left: 12`), obstructing it even though it's a completely different
// panel. Measuring the Layers header's rendered height
// (`getBoundingClientRect()`, independent of viewport width since it's
// pure padding/font-size, not `vw`-based) -- it ends at 37px from the
// top, so `60` clears it with margin. Same fix shape as
// geosat-mosaic-openlayers' own MeasurePanel.tsx (`top: isNarrow ? 84 :
// 12`), just a different pixel value -- that app's obstruction was a
// top button-row cluster above the map, not another panel's own header,
// so the two numbers aren't meant to match.
function panelStyle(isNarrow: boolean): CSSProperties {
  return {
    position: 'fixed',
    top: isNarrow ? 60 : 12,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: isNarrow ? 2 : 1,
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

// Result display for the measurement tools -- measure distance and
// calculate area: pure display, all the actual
// geodesic math lives in lib/geometry.ts. Rendered whenever measure mode
// is on (Globe.tsx), regardless of point count -- shows a hint until
// there's enough points for a real result instead of just not appearing,
// so the user always has feedback that measure mode is active.
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
