import type { CSSProperties } from 'react'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface AppInfoPanelProps {
  open: boolean
  onClose: () => void
}

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

const bodyStyle: CSSProperties = {
  lineHeight: 1.5,
}

const satelliteListStyle: CSSProperties = {
  margin: 0,
  paddingLeft: 18,
  lineHeight: 1.6,
}

// Wider line-height and slightly larger text than satelliteListStyle's
// desktop-tuned defaults -- this list only renders inside the mobile
// backdrop/card layout below, where list items are the main thing a
// thumb has to read and tap-scroll through, so touch-friendly spacing
// matters more here than on desktop.
const mobileCreditListStyle: CSSProperties = {
  ...satelliteListStyle,
  lineHeight: 1.8,
  fontSize: 14,
}

// Matches InfoPanel.tsx's own `linkStyle` (same panel family, same look).
const linkStyle: CSSProperties = {
  color: '#7ab8ff',
  wordBreak: 'break-all',
}

// On a narrow phone screen, tapping outside this panel closes it.
// Same dimmed-backdrop-plus-centered-card pattern
// InfoPanel.tsx/PointProbePanel.tsx already use for their own mobile
// treatment (and geosat-mosaic-openlayers' own AppInfoPanel.tsx, which
// got this same treatment first): the backdrop's own onClick closes, and
// the card stops that click from bubbling up so tapping *inside* the
// panel doesn't also close it.
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
  // Capped well below 100% (of the backdrop's already-16px-padded space)
  // so the backdrop stays visibly dimmed above/below the card even with
  // both credit lists open -- without the cap, this was reading as
  // a full-screen takeover rather than a card floating over the map.
  // overflowY: 'auto' (inherited from panelStyle) still handles content
  // taller than this via internal scrolling.
  maxHeight: '78vh',
}

// General project-info panel: project purpose (text above), data credit
// (satellites/agencies above), and a GitHub link. Distinct from InfoPanel.tsx, which is
// per-band/per-timestamp file metadata -- this one is static,
// project-level content with no backend fetch at all.
function AppInfoPanel({ open, onClose }: AppInfoPanelProps) {
  const isNarrow = useIsNarrowViewport()
  if (!open) return null

  // Mobile gets the wider, larger-text list style (mobileCreditListStyle)
  // since these two lists are the bulk of what a thumb has to read/scroll
  // through in the backdrop/card layout below; desktop keeps the original
  // tighter list.
  const creditListStyle = isNarrow ? mobileCreditListStyle : satelliteListStyle

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>About GeoSat-MOSAIC-Cesium</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Purpose</div>
        <div style={bodyStyle}>
          Global cloud/imagery mosaic built from 6 geostationary satellites,
          reprojected to a common lat/lon grid, with per-pixel source selection
          (minimum time gap, lowest view zenith angle) and seam blending at
          overlaps.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Satellite data</div>
        <ul style={creditListStyle}>
          <li>GOES-18 / GOES-19 (NOAA)</li>
          <li>Meteosat-9 / Meteosat-10 (EUMETSAT)</li>
          <li>INSAT-3DR / INSAT-3DS (ISRO/SAC/MOSDAC)</li>
          <li>Himawari-9 (JMA)</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Basemaps & overlays</div>
        <ul style={creditListStyle}>
          <li>Natural Earth II — via CesiumJS</li>
          <li>OpenStreetMap — © OpenStreetMap contributors</li>
          <li>Esri World Imagery — Esri, Maxar, Earthstar Geographics</li>
          <li>NASA EOSDIS GIBS / VIIRS Night Lights — NASA/NOAA</li>
          <li>Coastline overlay — Natural Earth, 50m coastline</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Source</div>
        <a
          style={linkStyle}
          href="https://github.com/jay-pandya-3004/geosat-mosaic"
          target="_blank"
          rel="noreferrer"
        >
          github.com/jay-pandya-3004/geosat-mosaic
        </a>
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

export default AppInfoPanel
