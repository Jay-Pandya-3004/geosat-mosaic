import type { CSSProperties } from 'react'
import { BAND_LABELS, type GalleryBand } from '../lib/gallery'
import { formatDateTime, type TimeDisplayMode } from '../lib/time'

interface TopLabelProps {
  band: GalleryBand
  time: string | undefined
  timeDisplay: TimeDisplayMode
  onInfoClick: () => void
  infoOpen: boolean
  onHelpClick: () => void
  helpOpen: boolean
}

const containerStyle: CSSProperties = {
  position: 'fixed',
  // bottom, not top -- part of a layout rework that moved this whole
  // label+Help+Info row down to bottom-center (Date/Controls/Animation
  // took over the top row instead, one per corner -- see App.tsx).
  // FullscreenButton moved to bottom-right in the same change, so it's
  // still this row's only real neighbor.
  bottom: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 2,
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  padding: '6px 14px',
  background: 'rgba(30, 30, 30, 0.8)',
  borderRadius: 6,
  color: '#edeeef',
  // Capped well short of the full viewport width (unlike every other
  // panel's `calc(100vw - 24px)`) -- this element is centered, and
  // FullscreenButton (now bottom-right) shares its row on the right side.
  // Centering means the reserved clearance has to apply symmetrically on
  // both sides even though only the right actually has a neighbor now.
  // 100vw-140px keeps the same generous margin the old two-neighbor
  // layout used.
  maxWidth: 'calc(100vw - 140px)',
}

// Date/band label stays big and centered, the app's primary readout.
// clamp() scales the size down smoothly on narrow viewports instead of a
// fixed breakpoint -- paired with removing `whiteSpace: nowrap` below so
// a still-too-long string wraps to a second line rather than overflowing
// its container or getting clipped.
const labelStyle: CSSProperties = {
  fontSize: 'clamp(14px, 4.5vw, 20px)',
  fontWeight: 'bold',
  fontVariantNumeric: 'tabular-nums',
  textAlign: 'center',
}

const roundButtonStyle: CSSProperties = {
  background: 'transparent',
  color: '#edeeef',
  border: '1px solid #777',
  borderRadius: '50%',
  width: 26,
  height: 26,
  minWidth: 26,
  lineHeight: 1,
  fontSize: 14,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}

const roundButtonActiveStyle: CSSProperties = {
  ...roundButtonStyle,
  background: '#2f6fb0',
  border: '1px solid #7ab8ff',
}

// Big centered date/time label + the help button (left) and info button
// (right). One component since all three are always shown together,
// centered as a single row.
function TopLabel({
  band,
  time,
  timeDisplay,
  onInfoClick,
  infoOpen,
  onHelpClick,
  helpOpen,
}: TopLabelProps) {
  return (
    <div style={containerStyle}>
      <button
        style={helpOpen ? roundButtonActiveStyle : roundButtonStyle}
        onClick={onHelpClick}
        aria-label="Help"
        title="Help"
      >
        ?
      </button>
      <span style={labelStyle}>
        {BAND_LABELS[band]}
        {time ? ` — ${formatDateTime(time, timeDisplay)}` : ''}
      </span>
      <button
        style={infoOpen ? roundButtonActiveStyle : roundButtonStyle}
        onClick={onInfoClick}
        aria-label="Image info"
        title="Image info"
      >
        i
      </button>
    </div>
  )
}

export default TopLabel
