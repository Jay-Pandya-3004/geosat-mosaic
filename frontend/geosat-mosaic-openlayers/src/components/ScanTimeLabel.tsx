import { useEffect, useState, type CSSProperties } from 'react'
import Panel from './Panel'
import type { MosaicBand } from '../lib/mosaic'
import { fetchSatelliteMetadata, type SatelliteMetadataEntry } from '../lib/metadataApi'
import { formatScanTime, type TimeDisplayMode } from '../lib/time'

interface ScanTimeLabelProps {
  band: MosaicBand
  time: string | undefined
  timeDisplay: TimeDisplayMode
}

const panelStyle: CSSProperties = {
  position: 'fixed',
  bottom: 28,
  left: 12,
  // Lower than every other fixed panel's `zIndex: 1` (LayerSwitcher
  // included) -- on a narrow phone screen this panel can grow tall enough
  // on its own (extended by the embedded Date/Search sections above) to
  // reach down into where this label sits, and it should lose that
  // overlap, not win it.
  zIndex: 0,
  font: '11px sans-serif',
  // `min()`, not a plain `260` -- the fixed 260px cap alone would overflow
  // off the right edge of a phone narrower than 260+24px of margin.
  maxWidth: 'min(260px, calc(100vw - 24px))',
}

const rowStyle: CSSProperties = {
  fontVariantNumeric: 'tabular-nums',
}

// Persistent on-map label -- on-map text label: mid
// scan time for every contributing satellite. Always visible (not
// hover-only, unlike point-probe), bound to the same shared
// current-timestamp state as the calendar/animation controls: band
// change, timestamp change, date change, and the animation running (each
// frame) all just mean a new `time`/`band` prop here, no separate wiring
// needed. `/satellite-metadata` runs at ~40ms/call (a small
// per-satellite-dimension read, not a full grid slice) -- comfortably
// fast enough for per-frame refresh, unlike InfoPanel.tsx's fetch (same
// endpoint) which deliberately skips animation ticks for a different
// reason (that panel also loads a thumbnail image and is normally left
// open to read once, not watched live).
//
// No interlock tying this to the band layer's own visibility needed --
// per the layer/control interlocks, the satellite
// mosaic layer's checkbox can never actually be turned off in this app
// (LayerSwitcher.tsx hardcodes `visible visibleDisabled` for it), so the
// "hide this label if the band layer is off" rule is
// defensive/unreachable through the UI, not an everyday case to build
// around.
function ScanTimeLabel({ band, time, timeDisplay }: ScanTimeLabelProps) {
  // Used to default-collapse on a narrow phone screen (`defaultCollapsed=
  // {isNarrow}`, same pattern as TimeControls.tsx/LayerSwitcher.tsx) --
  // a phone screenshot had shown this label's own satellite list
  // obstructing the bottom-left of the map by default. Reversed later:
  // this panel should stay open by default on mobile after all, so it's
  // now `false` unconditionally, same as desktop.
  const [satellites, setSatellites] = useState<SatelliteMetadataEntry[]>([])

  useEffect(() => {
    if (!time) return
    let cancelled = false
    fetchSatelliteMetadata(band, time)
      .then((result) => {
        if (!cancelled) setSatellites(result.satellites)
      })
      .catch(() => {
        // Silently keep the last-known-good list rather than blanking a
        // persistent label on one transient fetch error -- this call is
        // cheap and frequent (every animation frame), not worth a visible
        // error state for a single miss.
      })
    return () => {
      cancelled = true
    }
  }, [band, time])

  if (satellites.length === 0) return null

  return (
    <Panel title="Contributing satellites (mid scan)" style={panelStyle}>
      {satellites.map((satellite) => (
        <div key={satellite.key} style={rowStyle}>
          {satellite.key}: {formatScanTime(satellite.scan_mid, timeDisplay)}
        </div>
      ))}
    </Panel>
  )
}

export default ScanTimeLabel
