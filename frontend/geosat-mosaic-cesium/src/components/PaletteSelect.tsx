import { useEffect, useRef, useState, type CSSProperties } from 'react'
import type { ColorbarGroup } from '../lib/xcube'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface PaletteSelectProps {
  palette: string
  onPaletteChange: (name: string) => void
  colorbarGroups: ColorbarGroup[]
}

const THUMB_LENGTH = 40
const THUMB_THICKNESS = 10

const wrapStyle: CSSProperties = {
  position: 'relative',
}

const triggerStyle: CSSProperties = {
  width: '100%',
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  marginBottom: 4,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '3px 6px',
  cursor: 'pointer',
  font: 'inherit',
  fontSize: 13,
  textAlign: 'left',
}

const swatchWrapStyle: CSSProperties = {
  width: THUMB_LENGTH,
  height: THUMB_THICKNESS,
  overflow: 'hidden',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  flexShrink: 0,
}

const swatchImgStyle: CSSProperties = {
  width: '100%',
  height: '100%',
  display: 'block',
  // 'fill' (stretch to exactly the box, ignoring aspect ratio), not
  // 'cover' -- xcube's /colorbars preview PNGs are extremely wide/short
  // (256x1), so 'cover' scaled them up to fill this
  // swatch's height and then cropped virtually all of the resulting
  // width, showing only a ~1.5%-wide sliver near the gradient's cold end
  // -- looked like a flat solid color, not a gradient, for every
  // palette. 'fill' shows the whole 256-step gradient compressed to the
  // swatch's actual width instead.
  objectFit: 'fill',
}

const triggerLabelStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}

const caretStyle: CSSProperties = {
  fontSize: 9,
  color: '#aaa',
  flexShrink: 0,
}

const listStyle: CSSProperties = {
  position: 'fixed',
  zIndex: 5,
  // On desktop, this used to
  // be `width: 'max-content'`, letting the list grow wider than the
  // trigger button itself whenever a palette/group name was long enough
  // (each option's own `whiteSpace: nowrap` meant text could never
  // shrink to fit, only push the box wider) -- looked mismatched, a
  // popup visibly wider than what opened it. Now sized to exactly match
  // the trigger's own width (set inline at open time, see
  // `position.width` below) and long names wrap onto a 2nd line inside
  // that fixed width instead (see `optionNameStyle`'s `minWidth: 0`).
  maxHeight: 320,
  overflowY: 'auto',
  background: '#1c1c1c',
  border: '1px solid #555',
  borderRadius: 3,
  boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
  padding: '4px 0',
}

const groupLabelStyle: CSSProperties = {
  padding: '4px 8px 2px',
  fontSize: 10,
  color: '#888',
  textTransform: 'uppercase',
  letterSpacing: 0.5,
}

const optionRowStyle: CSSProperties = {
  display: 'flex',
  // 'flex-start', not 'center' -- once a long name wraps onto a 2nd
  // line (see `listStyle`'s own comment above), centering vertically
  // against the small swatch looked off; top-aligning both reads
  // cleanly whether the name wraps or not.
  alignItems: 'flex-start',
  gap: 6,
  padding: '3px 8px',
  cursor: 'pointer',
  fontSize: 13,
}

const optionRowSelectedStyle: CSSProperties = {
  ...optionRowStyle,
  background: '#3a3a3a',
}

// `flex: 1; min-width: 0` -- same reasoning as this app's other flex-
// item-that-needs-to-shrink fixes (e.g. TimeControls.tsx's scrubber):
// without `min-width: 0`, a flex item defaults to refusing to shrink
// below its own content's natural (single-line) width, which would
// have silently defeated the whole point of fixing the list's width
// above -- the name would've just overflowed instead of wrapping.
const optionNameStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
}

// On a narrow phone screen,
// this list is `position: fixed`, technically an overlay already
// (not clipped by LayerSwitcher's own `overflow: auto`), but its
// position/width come straight from the trigger button's own
// `getBoundingClientRect()` -- on mobile that trigger lives inside the
// ~180px-wide, left-docked Layers panel, so the list opened hugging the
// same left edge at roughly the same narrow width and same dark
// background as the panel itself. Visually indistinguishable from "more
// panel content", not a real dropdown popup, unlike this app's native
// <select> elements (band/basemap/time-of-day), which the OS always
// renders as a distinct floating/overlay picker regardless of the
// trigger's own size or position. Fixed the same way this app's other
// popups (Point Probe/Band Info/Timeseries, see PointProbePanel.tsx's
// own comment on this exact pattern) already solve an analogous mobile
// problem: a dimmed full-screen backdrop + a centered card, clearly
// distinct from the panel underneath. Desktop keeps the original
// trigger-anchored box unchanged -- explicitly fine as-is there.
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

const mobileListStyle: CSSProperties = {
  width: '100%',
  maxWidth: 320,
  maxHeight: '70vh',
  overflowY: 'auto',
  background: '#1c1c1c',
  border: '1px solid #555',
  borderRadius: 6,
  boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
  padding: '4px 0',
}

// Custom palette picker: shows the actual colorbar
// gradient next to each palette's name in the dropdown, not just text.
// Native <select><option> elements can't render images/gradients inside
// an option (a hard HTML limitation, not a styling choice) -- this is a
// from-scratch dropdown for that reason, not a styled <select>.
//
// Swatches are xcube's own `/colorbars` preview PNGs (base64, embedded in
// `colorbarGroups`, fetched once in Globe.tsx) -- not a live per-option
// legend fetch keyed to the current band/range like this component used
// before the 2026-08-15 xcube tile-serving switch (see MosaicLayer.tsx).
// Simpler (zero extra requests to open this dropdown) at the cost of the
// preview being a generic full-range gradient rather than reflecting the
// live colorMin/colorMax.
function PaletteSelect({ palette, onPaletteChange, colorbarGroups }: PaletteSelectProps) {
  const isNarrow = useIsNarrowViewport()
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ top: number; left: number; width: number } | null>(
    null,
  )
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const handleClickAway = (event: MouseEvent) => {
      if (triggerRef.current?.contains(event.target as Node)) return
      setOpen(false)
    }
    // 'click', not 'mousedown' -- see the git history/older comment on
    // this same listener for why (a real bug, not
    // theoretical): 'mousedown' unmounts the list before the browser
    // dispatches the row's own 'click' (palette selection), silently
    // closing the dropdown without ever calling onPaletteChange.
    window.addEventListener('click', handleClickAway)
    return () => window.removeEventListener('click', handleClickAway)
  }, [open])

  const handleToggle = () => {
    if (!open && triggerRef.current) {
      const rect = triggerRef.current.getBoundingClientRect()
      setPosition({ top: rect.bottom + 2, left: rect.left, width: rect.width })
    }
    setOpen((previous) => !previous)
  }

  const thumbnail = (previewPng: string) => (
    <span style={swatchWrapStyle}>
      <img style={swatchImgStyle} src={`data:image/png;base64,${previewPng}`} alt="" />
    </span>
  )

  const selectedPreview = colorbarGroups
    .flatMap(([, entries]) => entries)
    .find((entry) => entry.name === palette)?.previewPng

  const options = (
    <>
      {colorbarGroups.map(([title, entries]) => (
        <div key={title}>
          <div style={groupLabelStyle}>{title}</div>
          {entries.map(({ name, previewPng }) => (
            <div
              key={name}
              style={name === palette ? optionRowSelectedStyle : optionRowStyle}
              onClick={() => {
                onPaletteChange(name)
                setOpen(false)
              }}
            >
              {thumbnail(previewPng)}
              <span style={optionNameStyle}>{name}</span>
            </div>
          ))}
        </div>
      ))}
    </>
  )

  return (
    <div style={wrapStyle}>
      <button type="button" ref={triggerRef} style={triggerStyle} onClick={handleToggle}>
        {selectedPreview && thumbnail(selectedPreview)}
        <span style={triggerLabelStyle}>{palette}</span>
        <span style={caretStyle}>{open ? '▲' : '▼'}</span>
      </button>
      {open && isNarrow && (
        // No onClick here to specifically dismiss on backdrop tap -- the
        // existing `handleClickAway` window listener above already
        // covers it (a backdrop tap is a click outside `triggerRef`,
        // same as it always was for the desktop box), no need for a
        // second mechanism.
        <div style={mobileBackdropStyle}>
          <div style={mobileListStyle}>{options}</div>
        </div>
      )}
      {open && !isNarrow && position && (
        <div style={{ ...listStyle, top: position.top, left: position.left, width: position.width }}>
          {options}
        </div>
      )}
    </div>
  )
}

export default PaletteSelect
