import type { CSSProperties } from 'react'

interface HelpPanelProps {
  open: boolean
  onClose: () => void
  // Same "push it up further" reasoning as InfoPanel.tsx's own isNarrow
  // prop -- TopLabel (directly below this panel now, both horizontally
  // centered) can wrap to two lines on a narrow viewport.
  isNarrow: boolean
}

function panelStyle(isNarrow: boolean): CSSProperties {
  return {
    position: 'fixed',
    // bottom, not a flat 12 -- TopLabel (this panel's own trigger button)
    // moved from top-center to bottom-center in the same layout rework,
    // so this panel now opens *above* the label instead of anchoring to
    // the opposite corner. Same offset InfoPanel.tsx uses for the same
    // reason (70 clears one line, 110 clears TopLabel's wrapped two-line
    // case).
    bottom: isNarrow ? 110 : 70,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 2,
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

// Matches geosat-mosaic-cesium's/geosat-mosaic-openlayers's own
// AppInfoPanel.tsx `linkStyle` -- this app has no separate About panel,
// so the source link lives here in Help instead.
const linkStyle: CSSProperties = {
  color: '#7ab8ff',
  wordBreak: 'break-all',
}

// On a narrow phone screen, tapping outside this panel closes it. Same
// dimmed-backdrop-plus-centered-card pattern
// geosat-mosaic-cesium's/geosat-mosaic-openlayers's own AppInfoPanel.tsx/
// HelpPanel.tsx already use for their equivalent mobile treatment: the
// backdrop's own onClick closes, and the card stops that click from
// bubbling up so tapping *inside* the panel doesn't also close it.
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
    maxWidth: 480,
    maxHeight: '100%',
  }
}

// Same role as geosat-mosaic's HelpPanel.tsx (that project's own Help
// button) -- purpose/getting-started/features/limitation sections, but
// describing THIS app's actual feature set, not copied verbatim (no map,
// no point-probe, no measurement tools here; this app has its own things
// -- zoom, per-image info table, collapsible panels -- GeoSat-MOSAIC
// doesn't).
function HelpPanel({ open, onClose, isNarrow }: HelpPanelProps) {
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
          GeoSat-Gallery is a simple slideshow viewer for the same global
          cloud/imagery mosaic GeoSat-MOSAIC renders live on a map -- here as
          pre-rendered preview images instead, one per band per timestamp, with
          coastlines and a lat/lon graticule baked in.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Getting started</div>
        <div style={bodyStyle}>
          Pick an infrared band (MIR/WV/TIR1/TIR2) from the <strong>Controls</strong>{' '}
          panel (top-center), then a date and time from the <strong>Date</strong>{' '}
          panel's (top-left) calendar, or press Play in the <strong>Animation</strong>{' '}
          panel (top-right) to step through every available timestamp
          automatically. Every panel's header can be clicked to collapse/expand
          it.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Viewing the image</div>
        <div style={bodyStyle}>
          Scroll to zoom in, scroll out to zoom back out -- the zoom follows
          wherever the cursor currently is. Double-click the image to reset the
          zoom. The fullscreen button (bottom-right) puts the whole page into
          fullscreen. Zoom resets automatically whenever the band or timestamp
          changes.
        </div>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Major features</div>
        <ul style={listStyle}>
          <li>Band, date, and time-of-day selection, all live during animation</li>
          <li>Animation player with a scrubber and adjustable speed</li>
          <li>Scroll-to-zoom on the image, with double-click to reset</li>
          <li>
            Per-image info panel (the <strong>i</strong> button next to the date/time
            label): contributing satellites' longitude, band wavelength, nominal
            nadir resolution, and scan start/mid/end times, plus a download link
            for the underlying raw data file
          </li>
          <li>Live sync and "show always recent" to follow newly-arriving data</li>
          <li>GMT/UTC vs. local time display toggle</li>
          <li>Fullscreen mode</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <div style={sectionLabelStyle}>Limitation</div>
        <div style={limitationStyle}>
          Only recent data is available -- the backend keeps a rolling window of
          roughly the last 7 days, not a full historical archive. Older
          timestamps are not retained and cannot be viewed once they age out of
          that window. Data processing also depends on backend compute
          resource availability, so new timestamps are processed periodically
          as resources free up rather than continuously in real time -- the
          application may sometimes show the most recently processed data
          from within that 7-day window instead of always the very latest.
        </div>
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
        <div
          className="themed-scrollbar"
          style={mobileCardStyle(isNarrow)}
          onClick={(event) => event.stopPropagation()}
        >
          {content}
        </div>
      </div>
    )
  }

  return (
    <div className="themed-scrollbar" style={panelStyle(isNarrow)}>
      {content}
    </div>
  )
}

export default HelpPanel
