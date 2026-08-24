import { useState, type CSSProperties, type FormEvent } from 'react'
import Panel from './Panel'
import { fetchPlaceSearch, type PlaceSearchResult } from '../lib/geocode'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

interface PlaceSearchProps {
  // Returns a warning string if the fly-to was refused (the
  // ±66° 2D-mode restriction), or null if it flew successfully -- lets this
  // component stay ignorant of Cesium/scene-mode details, Globe.tsx owns
  // the viewer and decides.
  onSelect: (result: PlaceSearchResult) => string | null
  // Same reasoning as CalendarControl.tsx's own `bare` prop -- on a narrow
  // phone screen this is embedded directly inside LayerSwitcher's "Layers"
  // panel (below "Show always recent") instead of its own separate
  // "Search" panel. Skips this component's own
  // <Panel> chrome when embedded.
  bare?: boolean
}

// No `position`/`width` -- see TimeControls.tsx's own note; this panel is
// the last of three flow children in the shared flex column wrapper in
// Globe.tsx (Time, then Date, then this one).
const panelStyle: CSSProperties = {}

const formStyle: CSSProperties = {
  display: 'flex',
  gap: 4,
}

const inputStyle: CSSProperties = {
  flex: 1,
  minWidth: 0,
  padding: '4px 6px',
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  font: '13px sans-serif',
}

const buttonStyle: CSSProperties = {
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '4px 8px',
  cursor: 'pointer',
  fontSize: 12,
}

const resultsStyle: CSSProperties = {
  marginTop: 6,
  maxHeight: 220,
  overflowY: 'auto',
}

// Mobile only: `bare` mode renders this list inside
// LayerSwitcher's own already-scrollable "Layers" panel (`overflowY:
// auto`), so this component's own nested `maxHeight`/`overflowY: auto`
// created two scrollable regions stacked inside each other -- a touch
// drag starting on the results list was captured by whichever of the two
// ended up owning the gesture, and in practice the results list itself
// couldn't be scrolled to its own bottom no matter how far the outer
// panel was scrolled. Standalone (desktop, non-`bare`) mode isn't inside
// another scrollable ancestor, so it keeps the cap -- fixing this is
// just "don't nest a second scroll container", not a general results-
// list redesign.
const resultsStyleBare: CSSProperties = {
  marginTop: 6,
}

const resultButtonStyle: CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  background: 'transparent',
  color: '#edeeef',
  border: 'none',
  borderTop: '1px solid #444',
  padding: '5px 2px',
  cursor: 'pointer',
  fontSize: 12,
}

const errorStyle: CSSProperties = {
  color: '#ff8080',
  marginTop: 6,
  fontSize: 12,
}

const warningStyle: CSSProperties = {
  color: '#ffcc66',
  marginTop: 6,
  fontSize: 12,
}

// Place search -- Nominatim place search with zoom/
// fly-to-location, subject to the ±66° 2D-mode restriction. Search fires
// only on submit (Enter or button), not per-keystroke -- Nominatim's usage
// policy asks for at most ~1 request/sec.
function PlaceSearch({ onSelect, bare }: PlaceSearchProps) {
  // See TimeControls.tsx's own comment on this same pattern -- kept
  // consistent with LayerSwitcher.tsx's original `defaultCollapsed=
  // {isNarrow}`.
  const isNarrow = useIsNarrowViewport()
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<PlaceSearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    const trimmed = query.trim()
    if (!trimmed) return

    setLoading(true)
    setError(null)
    setWarning(null)
    try {
      const found = await fetchPlaceSearch(trimmed)
      setResults(found)
      if (found.length === 0) {
        setError('No results found')
      }
    } catch (err) {
      setResults([])
      setError(err instanceof Error ? err.message : 'Place search failed')
    } finally {
      setLoading(false)
    }
  }

  const handlePick = (result: PlaceSearchResult) => {
    const refused = onSelect(result)
    setWarning(refused)
  }

  const content = (
    <>
      <form style={formStyle} onSubmit={handleSubmit}>
        <input
          style={inputStyle}
          type="text"
          placeholder="Search a place…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button style={buttonStyle} type="submit" disabled={loading}>
          {loading ? '…' : 'Go'}
        </button>
      </form>

      {error && <div style={errorStyle}>{error}</div>}
      {warning && <div style={warningStyle}>{warning}</div>}

      {results.length > 0 && (
        <div style={bare ? resultsStyleBare : resultsStyle}>
          {results.map((result, index) => (
            <button
              key={index}
              style={resultButtonStyle}
              onClick={() => handlePick(result)}
            >
              {result.displayName}
            </button>
          ))}
        </div>
      )}
    </>
  )

  return bare ? (
    content
  ) : (
    <Panel title="Search" style={panelStyle} defaultCollapsed={isNarrow}>
      {content}
    </Panel>
  )
}

export default PlaceSearch
