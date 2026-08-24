import { useState, type CSSProperties, type ReactNode } from 'react'

interface PanelProps {
  title: string
  children: ReactNode
  defaultCollapsed?: boolean
  // Merged in after this component's own shared chrome (width/background/
  // radius/color/font/overflow), so a caller can still override any of
  // those too -- same pattern geosat-mosaic-cesium's/geosat-mosaic-
  // openlayers's own Panel.tsx already use for their equivalent mobile
  // combined-panel maxHeight/overflow overrides.
  style?: CSSProperties
}

const panelStyle: CSSProperties = {
  width: 200,
  maxWidth: 'calc(100vw - 24px)',
  background: 'rgba(38, 38, 38, 0.9)',
  borderRadius: 4,
  color: '#edeeef',
  font: '13px sans-serif',
  overflow: 'hidden',
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  padding: '10px 12px',
  fontWeight: 'bold',
  cursor: 'pointer',
  userSelect: 'none',
}

const chevronStyle: CSSProperties = {
  color: '#aaa',
  fontSize: 11,
}

const bodyStyle: CSSProperties = {
  padding: '0 12px 10px',
}

// Shared collapsible chrome for every control panel in this app -- a
// click on the header toggles the body, panel itself stays mounted
// (collapsed state, not unmounted) so toggling doesn't lose scroll
// position/etc in whatever's inside.
function Panel({ title, children, defaultCollapsed, style }: PanelProps) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed ?? false)

  return (
    <div style={{ ...panelStyle, ...style }}>
      <div style={headerStyle} onClick={() => setCollapsed((value) => !value)}>
        <span>{title}</span>
        <span style={chevronStyle}>{collapsed ? '▸' : '▾'}</span>
      </div>
      {!collapsed && <div style={bodyStyle}>{children}</div>}
    </div>
  )
}

export default Panel
