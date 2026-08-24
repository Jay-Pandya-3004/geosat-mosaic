import { useEffect, useState, type CSSProperties } from 'react'

const buttonStyle: CSSProperties = {
  position: 'fixed',
  // bottom-right, not top-right -- moved as part of the same App.tsx
  // layout rework that split the old Controls/Animation/Date column into
  // three separate top corners and moved TopLabel (band/time + Help/Info
  // buttons) down to bottom-center.
  bottom: 12,
  right: 12,
  zIndex: 2,
  width: 34,
  height: 34,
  background: 'rgba(38, 38, 38, 0.9)',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 4,
  cursor: 'pointer',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
}

// Enter/exit-fullscreen glyphs (Unicode "⛶"/"⤢") render as an empty box
// on at least one system tested -- the font simply has no glyph for them.
// Inline SVG paths instead: no font/glyph-coverage dependency, renders
// identically everywhere.
function EnterIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" />
    </svg>
  )
}

function ExitIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M9 4v5H4M15 4v5h5M20 15h-5v5M4 15h5v5" />
    </svg>
  )
}

// Fullscreen toggle. Uses the Fullscreen API on `document.documentElement`
// (the whole page, not just the image) -- tracks `fullscreenchange`
// rather than only its own click state, since the browser's own
// Esc-to-exit (or any other exit path) needs to flip the icon back too.
function FullscreenButton() {
  const [isFullscreen, setIsFullscreen] = useState(Boolean(document.fullscreenElement))

  useEffect(() => {
    const handleChange = () => setIsFullscreen(Boolean(document.fullscreenElement))
    document.addEventListener('fullscreenchange', handleChange)
    return () => document.removeEventListener('fullscreenchange', handleChange)
  }, [])

  function toggle() {
    if (document.fullscreenElement) {
      document.exitFullscreen()
    } else {
      document.documentElement.requestFullscreen()
    }
  }

  return (
    <button
      style={buttonStyle}
      onClick={toggle}
      aria-label={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
      title={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
    >
      {isFullscreen ? <ExitIcon /> : <EnterIcon />}
    </button>
  )
}

export default FullscreenButton
