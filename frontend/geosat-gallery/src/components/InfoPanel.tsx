import { useEffect, useState, type CSSProperties } from 'react'
import type { GalleryBand } from '../lib/gallery'
import { fetchSatelliteMetadata, type SatelliteMetadata } from '../lib/metadataApi'
import { rawFileDownloadUrl } from '../lib/thredds'
import { formatScanTime, type TimeDisplayMode } from '../lib/time'

interface InfoPanelProps {
  open: boolean
  onClose: () => void
  band: GalleryBand
  time: string | undefined
  // Deliberately does NOT refresh on every animation tick, same reasoning
  // as geosat-mosaic's InfoPanel.tsx: this backend call opens a
  // ~500-700MB file on disk per request, and this app's controls stay live
  // during playback (unlike GeoSat-MOSAIC's), so `time` could otherwise
  // change as fast as every 250ms at 4x -- a panel left open during
  // playback is most likely being watched for the currently-visible
  // frame's info anyway, but the fetch itself only re-runs once playback
  // stops.
  playing: boolean
  timeDisplay: TimeDisplayMode
  // TopLabel (this panel's own trigger button, the "i" icon) is now
  // bottom-center, not top-center -- this panel opens *above* it instead
  // of below. 110 (vs. 70) clears TopLabel's wrapped two-line case on a
  // narrow viewport, same delta as before, just applied to `bottom`
  // instead of `top`.
  isNarrow: boolean
}

function panelStyle(isNarrow: boolean): CSSProperties {
  return {
    position: 'fixed',
    bottom: isNarrow ? 110 : 70,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 2,
    // Widened from 480 -- the 7-column satellite table (Satellite/Lon/
    // Band wavelength/Resolution/Scan start/mid/end, several
    // `white-space: nowrap` columns) didn't fit at 480px and forced
    // tableWrapStyle's own overflowX:auto scrollbar into view on every
    // render. Measured via a detached clone with `width: auto` (bypassing
    // the table's own `width: 100%`): intrinsic content width is ~476px
    // at this font size, needing ~508px total once this panel's own 16px
    // left+right padding is added back. 600 (still capped by maxWidth
    // below on any narrower viewport) clears that with a margin --
    // verified via `scrollWidth === clientWidth` on the wrap div (zero
    // overflow).
    width: 600,
    maxWidth: 'calc(100vw - 24px)',
    maxHeight: 'calc(70vh)',
    overflowY: 'auto',
    padding: '14px 16px',
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

const linkStyle: CSSProperties = {
  color: '#7ab8ff',
  wordBreak: 'break-all',
}

const tableWrapStyle: CSSProperties = {
  overflowX: 'auto',
}

const tableStyle: CSSProperties = {
  width: '100%',
  borderCollapse: 'collapse',
  fontSize: 11,
}

const thStyle: CSSProperties = {
  textAlign: 'left',
  padding: '2px 6px 2px 0',
  color: '#aaa',
  fontWeight: 'normal',
  borderBottom: '1px solid #555',
  whiteSpace: 'nowrap',
}

const tdStyle: CSSProperties = {
  padding: '3px 6px 3px 0',
  borderBottom: '1px solid #3a3a3a',
  whiteSpace: 'nowrap',
}

const errorStyle: CSSProperties = {
  color: '#ff8080',
}

// On a narrow phone screen, tapping outside this panel closes it. Same
// dimmed-backdrop-plus-centered-card pattern as HelpPanel.tsx's own
// mobile treatment (see that file's own comment for the wider precedent
// this follows).
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

function mobileCardStyle(isNarrow: boolean): CSSProperties {
  return {
    ...panelStyle(isNarrow),
    position: 'static',
    bottom: undefined,
    left: undefined,
    transform: undefined,
    width: '100%',
    // 600, not the generic 480 HelpPanel.tsx/geosat-mosaic-cesium's own
    // equivalents use -- this panel's own content-width requirement (the
    // satellite table) is ~508px.
    // 480 would reintroduce the same horizontal-overflow bug that width
    // increase fixed, on the (uncommon but real) case of a wide-but-short
    // viewport still counting as "narrow" via useIsNarrowViewport's
    // min(width,height) check -- e.g. a landscape tablet/phone.
    maxWidth: 600,
    maxHeight: '100%',
  }
}

function fmt(value: number | null, digits: number, suffix: string): string {
  return value === null ? '—' : `${value.toFixed(digits)}${suffix}`
}

// Per-image detail panel: contributing satellites' band wavelength,
// nominal nadir spatial resolution, longitude, and scan start/mid/end for
// the currently displayed .png, plus a download link for the underlying
// raw .nc file. Same backend/rationale as
// GeoSat-MOSAIC's own InfoPanel.tsx (THREDDS structurally can't serve
// these fields to a browser) -- no thumbnail preview here, unlike that
// version, since the gallery PNG *is* already the entire main view in
// this app (see ImageViewer.tsx), a redundant second copy of the same
// image would add nothing.
function InfoPanel({ open, onClose, band, time, playing, timeDisplay, isNarrow }: InfoPanelProps) {
  const [data, setData] = useState<SatelliteMetadata | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!open || playing || !time) return
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchSatelliteMetadata(band, time)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, playing, band, time])

  if (!open) return null

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>{band.toUpperCase()} image info</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {loading && <div>Loading…</div>}
      {error && <div style={errorStyle}>{error}</div>}
      {playing && !data && <div>Pause animation to load image info.</div>}

      {data && (
        <>
          <div style={sectionStyle}>
            <div style={sectionLabelStyle}>Source file</div>
            <a
              style={linkStyle}
              href={rawFileDownloadUrl(data.filename)}
              target="_blank"
              rel="noreferrer"
            >
              {data.filename}
            </a>
          </div>

          <div style={sectionStyle}>
            <div style={sectionLabelStyle}>Contributing satellites</div>
            <div style={tableWrapStyle}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    <th style={thStyle}>Satellite</th>
                    <th style={thStyle}>Lon</th>
                    <th style={thStyle}>Band wavelength</th>
                    <th style={thStyle}>Resolution (nadir)</th>
                    <th style={thStyle}>Scan start</th>
                    <th style={thStyle}>Scan mid</th>
                    <th style={thStyle}>Scan end</th>
                  </tr>
                </thead>
                <tbody>
                  {data.satellites.map((satellite) => (
                    <tr key={satellite.key}>
                      <td style={tdStyle}>{satellite.key}</td>
                      <td style={tdStyle}>{satellite.longitude.toFixed(1)}</td>
                      <td style={tdStyle}>{fmt(satellite.band_wavelength_um, 2, ' μm')}</td>
                      <td style={tdStyle}>{fmt(satellite.spatial_resolution_km, 1, ' km')}</td>
                      <td style={tdStyle}>{formatScanTime(satellite.scan_start, timeDisplay)}</td>
                      <td style={tdStyle}>{formatScanTime(satellite.scan_mid, timeDisplay)}</td>
                      <td style={tdStyle}>{formatScanTime(satellite.scan_end, timeDisplay)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  )

  if (isNarrow) {
    return (
      <div style={mobileBackdropStyle} onClick={onClose}>
        <div style={mobileCardStyle(isNarrow)} onClick={(event) => event.stopPropagation()}>
          {content}
        </div>
      </div>
    )
  }

  return <div style={panelStyle(isNarrow)}>{content}</div>
}

export default InfoPanel
