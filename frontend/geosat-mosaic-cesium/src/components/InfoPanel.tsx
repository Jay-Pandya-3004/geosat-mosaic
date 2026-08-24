import { useEffect, useState, type CSSProperties } from 'react'
import type { MosaicBand } from './MosaicLayer'
import { fetchSatelliteMetadata, type SatelliteMetadata } from '../lib/metadataApi'
import { mosaicThumbnailUrl, rawFileDownloadUrl } from '../lib/thredds'
import { formatScanTime, type TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface InfoPanelProps {
  open: boolean
  onClose: () => void
  band: MosaicBand
  time?: string
  // Deliberately does NOT refresh on every animation tick, even though
  // `time` technically changes every frame during playback -- this backend
  // call opens a real ~500-700MB file on disk per request, and refetching
  // on every frame (as fast as every 250ms at 4x) would add real I/O load
  // for a panel most likely left open just to read once, not to watch
  // live. Same reasoning already applied to the legend min/max
  // auto-refresh (see Globe.tsx) -- band/date/time controls are already
  // locked during playback anyway, so this only skips reacting to the one
  // thing that *does* still change then.
  playing: boolean
  // GMT/UTC vs. local time toggle -- applied here too (not just the
  // narrower set of literal touch points) for consistency, see
  // PointProbePanel.tsx's same note.
  timeDisplay: TimeDisplayMode
}

// A non-blocking fixed panel, like LayerSwitcher/TimeControls/
// CalendarControl -- deliberately NOT a full-screen modal overlay. A modal
// would sit on top of and block clicks to LayerSwitcher's own band
// selector, which would make this panel's live band-change refresh
// (see the `playing` prop below) unreachable through the UI -- caught by
// testing the interaction live, not just visually. Bottom-center, the one
// stretch of screen not already claimed by another fixed panel (Layers
// top-left, Time/Date top-right).
const panelStyle: CSSProperties = {
  position: 'fixed',
  bottom: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 1,
  width: 460,
  maxWidth: 'calc(100vw - 24px)',
  maxHeight: 'calc(60vh)',
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

const linkStyle: CSSProperties = {
  color: '#7ab8ff',
  wordBreak: 'break-all',
}

const thumbnailStyle: CSSProperties = {
  display: 'block',
  maxWidth: '100%',
  borderRadius: 3,
  marginBottom: 4,
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
}

const tdStyle: CSSProperties = {
  padding: '3px 6px 3px 0',
  borderBottom: '1px solid #3a3a3a',
}

const errorStyle: CSSProperties = {
  color: '#ff8080',
}

// Same narrow-viewport overlap problem, and same fix, as
// PointProbePanel.tsx's own comment on these two constants -- this
// panel's bottom-anchored `panelStyle` overlaps the docked LayerSwitcher/
// TimeControls panels on a phone screen instead of floating clear of
// them the way it does on desktop.
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

// File/metadata/thumbnail detail for the currently displayed band+
// timestamp -- the info button next to the band layer
// name. Three pieces, all keyed off the same band+time the map is
// currently showing:
// - Download link for the exact raw tiera_<timestamp>.nc file.
// - Inline gallery thumbnail preview, filename as its own caption/link.
// - Per-satellite metadata table (key, longitude, scan start/mid/end).
function InfoPanel({ open, onClose, band, time, playing, timeDisplay }: InfoPanelProps) {
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

  const isNarrow = useIsNarrowViewport()

  if (!open) return null

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>{band.toUpperCase()} band info</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {loading && <div>Loading…</div>}
      {error && <div style={errorStyle}>{error}</div>}

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

          {time && (
            <div style={sectionStyle}>
              <div style={sectionLabelStyle}>Thumbnail</div>
              <img
                style={thumbnailStyle}
                src={mosaicThumbnailUrl(band, time, data.actual_mid_time)}
                alt={`${band} thumbnail for ${data.filename}`}
              />
              <a
                style={linkStyle}
                href={mosaicThumbnailUrl(band, time, data.actual_mid_time)}
                target="_blank"
                rel="noreferrer"
              >
                {mosaicThumbnailUrl(band, time, data.actual_mid_time).split('/').pop()}
              </a>
            </div>
          )}

          <div style={sectionStyle}>
            <div style={sectionLabelStyle}>Contributing satellites</div>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Satellite</th>
                  <th style={thStyle}>Lon</th>
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
                    <td style={tdStyle}>{formatScanTime(satellite.scan_start, timeDisplay)}</td>
                    <td style={tdStyle}>{formatScanTime(satellite.scan_mid, timeDisplay)}</td>
                    <td style={tdStyle}>{formatScanTime(satellite.scan_end, timeDisplay)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
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

export default InfoPanel
