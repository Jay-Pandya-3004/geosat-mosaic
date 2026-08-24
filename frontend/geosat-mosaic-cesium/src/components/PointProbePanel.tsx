import { useRef, type CSSProperties } from 'react'
import type { PointProbeResult, SatelliteMetadataEntry } from '../lib/metadataApi'
import { formatScanTime, type TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface PointProbePanelProps {
  loading: boolean
  error: string | null
  result: PointProbeResult | null
  onClose: () => void
  // Building directly on point-probe -- click a point (extends point-probe).
  // Optional so
  // this component doesn't have to know about the feature if a caller
  // doesn't want it.
  onViewTimeseries?: () => void
  // GMT/UTC vs. local time toggle -- not literally named in
  // the narrower list of the toggle's touch points, but applied
  // here too for internal consistency (a toggle that changes some
  // on-screen times but not others, like satellite scan times right next
  // to a value that DID flip, would read as broken, not intentional).
  timeDisplay: TimeDisplayMode
}

const panelStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 1,
  width: 340,
  maxWidth: 'calc(100vw - 24px)',
  padding: '10px 12px',
  background: 'rgba(30, 30, 30, 0.96)',
  borderRadius: 6,
  color: '#edeeef',
  font: '13px sans-serif',
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

const closeButtonStyle: CSSProperties = {
  background: 'transparent',
  color: '#edeeef',
  border: 'none',
  fontSize: 18,
  lineHeight: 1,
  cursor: 'pointer',
  padding: 4,
}

const coordStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginBottom: 6,
}

const valueStyle: CSSProperties = {
  fontSize: 20,
  marginBottom: 4,
}

const resolutionStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginBottom: 8,
}

const satelliteBlockStyle: CSSProperties = {
  marginBottom: 6,
}

const satelliteLabelStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginBottom: 2,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
}

const errorStyle: CSSProperties = {
  color: '#ff8080',
}

const timeseriesButtonStyle: CSSProperties = {
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '4px 8px',
  cursor: 'pointer',
  fontSize: 12,
  marginTop: 4,
}

// On a narrow phone screen this panel's own width (capped to
// `calc(100vw - 24px)`) ends up spanning nearly the full screen, which
// puts it directly on top of -- not clear of -- the edge-docked
// LayerSwitcher/TimeControls panels instead of floating in the empty
// map area the way it does on desktop (phone
// screenshots showed Layers/Time content visibly overlapping this panel's
// bounds). Fix: on narrow viewports only, render behind a dimmed
// full-screen backdrop that captures clicks (tap outside to dismiss,
// same as closing via the × button) so only one panel is ever on
// screen at a time, instead of trying to co-exist with the docked
// panels in too little space.
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
  top: undefined,
  left: undefined,
  transform: undefined,
  width: '100%',
  maxWidth: 420,
  maxHeight: '100%',
  overflowY: 'auto',
}

function SatelliteBlock({
  label,
  satellite,
  weight,
  timeDisplay,
}: {
  label: string
  satellite: SatelliteMetadataEntry
  weight?: number
  timeDisplay: TimeDisplayMode
}) {
  return (
    <div style={satelliteBlockStyle}>
      <div style={satelliteLabelStyle}>
        {label}
        {weight !== undefined ? ` (${(weight * 100).toFixed(0)}%)` : ''}
      </div>
      <div>
        {satellite.key} — mid {formatScanTime(satellite.scan_mid, timeDisplay)} (start{' '}
        {formatScanTime(satellite.scan_start, timeDisplay)}, end{' '}
        {formatScanTime(satellite.scan_end, timeDisplay)})
        {satellite.band_wavelength_um !== null &&
          ` — ${satellite.band_wavelength_um.toFixed(2)} μm`}
      </div>
    </div>
  )
}

// Point-probe result popup: for the
// currently displayed band, click a location to see that pixel's value,
// contributing satellite(s), and their scan mid/start/end time. Blended
// pixels (the "blended-pixel nuance") show both the
// primary and runner-up satellite, not just the primary, since a blended
// pixel's value is genuinely a mix of both.
//
// Spatial resolution + band wavelength: both come
// from per-pixel/per-satellite .nc variables (pixel_resolution_km_
// <band>, satellite_band_wavelength_um_<band>), not derived/hardcoded --
// see geosat-mosaic-api/main.py's _probe_file/_satellite_entry. Spatial
// resolution is the *actual* per-pixel ground resolution (accounts for
// view-zenith-angle degradation away from nadir), not each satellite's
// fixed nominal figure, so it's shown once per probe result, not per
// satellite. Wavelength genuinely differs per satellite (see
// SatelliteBlock), so it's shown inside each satellite's own block.
function PointProbePanel({
  loading,
  error,
  result,
  onClose,
  onViewTimeseries,
  timeDisplay,
}: PointProbePanelProps) {
  const isNarrow = useIsNarrowViewport()

  // Mobile only: this panel is opened by a tap landing on
  // the Cesium canvas itself (Cesium's own internal touch handling), not
  // a normal React button click -- unlike the mobile backdrop's
  // tap-outside-to-dismiss below. A single physical tap on a touch device
  // can produce a trailing/echoed click shortly after the original one; if
  // that echo fires after this backdrop has already mounted at the same
  // screen position (this component stays mounted the whole time
  // `pointProbeMode` is on -- see Globe.tsx -- so "just started showing"
  // happens on every fresh probe, not just first mount), it lands on the
  // backdrop instead of the canvas and immediately closes the panel that
  // just opened. Reported as "opens and closes immediately" near the
  // poles specifically, plausibly because reaching a pole usually means
  // panning/zooming right before the tap, leaving a residual
  // gesture-release event close in time to the probe tap. Fix: ignore
  // the backdrop's dismiss click for a brief window right after the
  // panel newly starts showing -- long enough to absorb a same-gesture
  // echo, short enough that a real, deliberate tap-to-dismiss still
  // feels instant.
  const isShowing = loading || error !== null || result !== null
  const wasShowingRef = useRef(false)
  const openedAtRef = useRef(0)
  if (isShowing && !wasShowingRef.current) {
    openedAtRef.current = Date.now()
  }
  wasShowingRef.current = isShowing

  if (!isShowing) return null

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>Point probe</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {loading && <div>Loading…</div>}
      {error && <div style={errorStyle}>{error}</div>}

      {result && (
        <>
          <div style={coordStyle}>
            {result.lat.toFixed(3)}°, {result.lon.toFixed(3)}°
          </div>

          {result.value === null || !result.primary ? (
            <div style={valueStyle}>No data at this location</div>
          ) : (
            <>
              <div style={valueStyle}>{result.value.toFixed(1)} K</div>
              {result.pixel_resolution_km !== null && (
                <div style={resolutionStyle}>
                  Spatial resolution: {result.pixel_resolution_km.toFixed(2)} km
                </div>
              )}
              <SatelliteBlock
                label={result.is_blended ? 'Primary satellite' : 'Satellite'}
                satellite={result.primary}
                weight={result.is_blended ? (result.blend_weight_winner ?? undefined) : undefined}
                timeDisplay={timeDisplay}
              />
              {result.is_blended && result.runner_up && (
                <SatelliteBlock
                  label="Runner-up satellite"
                  satellite={result.runner_up}
                  weight={
                    result.blend_weight_winner === null
                      ? undefined
                      : 1 - result.blend_weight_winner
                  }
                  timeDisplay={timeDisplay}
                />
              )}
              {onViewTimeseries && (
                <button style={timeseriesButtonStyle} onClick={onViewTimeseries}>
                  View timeseries
                </button>
              )}
            </>
          )}
        </>
      )}
    </>
  )

  if (isNarrow) {
    return (
      <div
        style={mobileBackdropStyle}
        onClick={() => {
          if (Date.now() - openedAtRef.current < 400) return
          onClose()
        }}
      >
        <div style={mobileCardStyle} onClick={(event) => event.stopPropagation()}>
          {content}
        </div>
      </div>
    )
  }

  return <div style={panelStyle}>{content}</div>
}

export default PointProbePanel
