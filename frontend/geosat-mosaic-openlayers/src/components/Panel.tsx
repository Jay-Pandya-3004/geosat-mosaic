import { useState, type CSSProperties, type ReactNode } from 'react'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface PanelProps {
  title: string
  children: ReactNode
  // Position/size are entirely caller-owned (each panel in this app sits
  // at its own fixed spot, or as a flow child inside a shared flex stack
  // -- see Globe.tsx's right-column wrapper) -- merged in after this
  // component's own shared chrome (background/radius/color/font/padding),
  // so a caller can still override any of those too if it ever needs to.
  style?: CSSProperties
  defaultCollapsed?: boolean
}

const baseStyle: CSSProperties = {
  padding: '10px 12px',
  background: 'rgba(38, 38, 38, 0.8)',
  borderRadius: 4,
  color: '#edeeef',
  font: '13px sans-serif',
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  // On a narrow panel with a long title ("Contributing satellites (mid
  // scan)"): `space-between` only inserts space when
  // there's leftover room on the flex line -- with a title long enough to
  // nearly fill the row, there wasn't any, so the chevron ended up
  // touching the title text with no visible gap. `gap` enforces a
  // guaranteed minimum regardless, on top of `titleStyle`'s `minWidth: 0`
  // below (lets the title truncate instead of refusing to shrink).
  gap: 8,
  fontWeight: 'bold',
  marginBottom: 8,
  cursor: 'pointer',
  userSelect: 'none',
}

const titleStyle: CSSProperties = {
  // `minWidth: 0`, not `whiteSpace: nowrap` + ellipsis -- titles here are
  // informational (e.g. "Contributing satellites (mid scan)"), not just
  // labels, so on a narrow panel this wraps onto a second line rather
  // than silently truncating and losing part of the text. `flex: 1`
  // (paired with headerStyle's `gap`) is what actually guarantees the
  // chevron's spacing either way.
  flex: 1,
  minWidth: 0,
}

const chevronStyle: CSSProperties = {
  color: '#aaa',
  fontSize: 11,
  fontWeight: 'normal',
  flexShrink: 0,
}

// Bigger tap target/glyph on a phone screen -- the default 11px chevron
// is a genuinely fine-motor target on a touchscreen next to a finger,
// unlike a mouse pointer on desktop.
const chevronStyleNarrow: CSSProperties = {
  ...chevronStyle,
  fontSize: 18,
}

// Shared collapsible chrome for every fixed-position control panel in
// this app (Layers, Time, Date, Nominatim search, and Contributing
// Satellites are all collapsible). Clicking the header
// toggles the body; the panel itself stays mounted either way (children
// just stop rendering), so a panel with its own internal state (e.g.
// PlaceSearch's query/results) doesn't lose it on collapse.
function Panel({ title, children, style, defaultCollapsed }: PanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed ?? false)
  const isNarrow = useIsNarrowViewport()

  return (
    <div style={{ ...baseStyle, ...style }}>
      <div style={headerStyle} onClick={() => setCollapsed((value) => !value)}>
        <span style={titleStyle}>{title}</span>
        <span style={isNarrow ? chevronStyleNarrow : chevronStyle}>{collapsed ? '▸' : '▾'}</span>
      </div>
      {!collapsed && children}
    </div>
  )
}

export default Panel
