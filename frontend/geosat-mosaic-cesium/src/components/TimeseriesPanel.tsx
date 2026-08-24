import { useMemo, type CSSProperties } from 'react'
import Plotly from 'plotly.js-dist-min'
import createPlotlyComponent from 'react-plotly.js/factory'
import type { TimeseriesPoint, TimeseriesResponse } from '../lib/metadataApi'
import { formatDateTime, formatScanTime, type TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

const Plot = createPlotlyComponent(Plotly)

interface TimeseriesPanelProps {
  open: boolean
  onClose: () => void
  loading: boolean
  error: string | null
  data: TimeseriesResponse | null
  band: string
  // GMT/UTC vs. local time toggle -- applies
  // everywhere at once, including the Plotly.js timeseries axis/tooltips.
  // Scoping note: only the axis *title* and hover-text times respect the
  // toggle -- the actual plotted x-values stay UTC timestamps
  // (unchanged), since Plotly has no built-in timezone-aware date-axis
  // conversion and re-deriving one is a lot of complexity for a display-only
  // need. The doc's own accountability-data emphasis is served by the
  // custom hover text either way, which fully respects the toggle.
  timeDisplay: TimeDisplayMode
  // Total number of timestamps that actually exist (`allTimestamps.length`
  // in Globe.tsx), vs. `data.points.length` which is however many were in
  // whatever range was actually fetched (the currently-loaded animation
  // window by default, per Globe.tsx's handleViewTimeseries comment --
  // window-extend-only-backward, so this can be well short of the total).
  // Used to show "showing N of M" and to decide whether the "load full
  // history" button below has anything left to do.
  totalAvailable: number
  onLoadFullHistory: () => void
}

const panelStyle: CSSProperties = {
  position: 'fixed',
  bottom: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 2,
  width: 820,
  maxWidth: 'calc(100vw - 24px)',
  padding: '10px 14px',
  background: 'rgba(30, 30, 30, 0.96)',
  borderRadius: 6,
  color: '#edeeef',
  font: '13px sans-serif',
}

const headerStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  marginBottom: 4,
}

const titleStyle: CSSProperties = {
  fontWeight: 'bold',
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

const rangeInfoStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 8,
  fontSize: 11,
  color: '#aaa',
  margin: '2px 0 6px',
}

const loadFullHistoryButtonStyle: CSSProperties = {
  background: '#3a3a3a',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 4,
  padding: '2px 8px',
  fontSize: 11,
  cursor: 'pointer',
  flexShrink: 0,
}

const disclaimerStyle: CSSProperties = {
  fontSize: 11,
  color: '#aaa',
  marginTop: 4,
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
  overflowY: 'auto',
}

// Same accountability info as the map point-probe, on hover in the Plotly
// timeseries/analysis chart --
// band+value, contributing satellite(s) (primary, plus runner-up if
// blended), scan times, and the informational satellite-count ("N/M
// satellites present", M = the max seen across this series, not a
// hardcoded constant -- see metadataApi.ts's own note on why). Also
// spatial resolution + band wavelength -- same
// per-pixel/per-satellite .nc-sourced fields as PointProbePanel.tsx
// (see that component's own comment for where they come from); spatial
// resolution once per point (it's per-pixel, not per-satellite), band
// wavelength appended to each satellite's own line (it genuinely differs
// satellite to satellite).
function hoverText(
  point: TimeseriesPoint,
  maxSatelliteCount: number,
  timeDisplay: TimeDisplayMode,
): string {
  const time = formatDateTime(point.time, timeDisplay)
  if (point.error) return `${time}<br>${point.error}`
  if (point.value === null || !point.primary) return `${time}<br>No data`

  const wavelength = (satellite: { band_wavelength_um: number | null }) =>
    satellite.band_wavelength_um === null ? '' : `, ${satellite.band_wavelength_um.toFixed(2)} μm`

  const lines = [
    time,
    `${point.value.toFixed(1)} K`,
    point.pixel_resolution_km === null
      ? null
      : `${point.pixel_resolution_km.toFixed(2)} km resolution`,
    `${point.primary.key} — mid ${formatScanTime(point.primary.scan_mid, timeDisplay)}${wavelength(point.primary)}`,
  ].filter((line): line is string => line !== null)
  if (point.is_blended && point.runner_up) {
    const weight = point.blend_weight_winner
    lines.push(
      `+ ${point.runner_up.key} (blended${weight === null ? '' : `, ${(weight * 100).toFixed(0)}%/${(100 - weight * 100).toFixed(0)}%`})${wavelength(point.runner_up)}`,
    )
  }
  lines.push(`${point.satellite_count}/${maxSatelliteCount} satellites present`)
  return lines.join('<br>')
}

function pointsToCsv(points: TimeseriesPoint[]): string {
  const header = 'time,value_K,satellite,scan_mid_utc,satellite_count,error'
  const rows = points.map((point) =>
    [
      point.time,
      point.value ?? '',
      point.primary?.key ?? '',
      point.primary?.scan_mid ?? '',
      point.satellite_count ?? '',
      point.error ?? '',
    ]
      .map((field) => `"${String(field).replace(/"/g, '""')}"`)
      .join(','),
  )
  return [header, ...rows].join('\n')
}

function downloadCsv(points: TimeseriesPoint[], band: string) {
  const csv = pointsToCsv(points)
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${band}-timeseries.csv`
  link.click()
  URL.revokeObjectURL(url)
}

// Timeseries chart for a point-probed location, using Plotly.js
// for interactive timeseries plots. PNG download, box-zoom
// (crop), and reset-axes all come free from Plotly's own default modebar
// -- only CSV export needed a custom button (a plot export/interaction control).
function TimeseriesPanel({
  open,
  onClose,
  loading,
  error,
  data,
  band,
  timeDisplay,
  totalAvailable,
  onLoadFullHistory,
}: TimeseriesPanelProps) {
  const maxSatelliteCount = useMemo(() => {
    if (!data) return 0
    return data.points.reduce((max, point) => Math.max(max, point.satellite_count ?? 0), 0)
  }, [data])

  const isNarrow = useIsNarrowViewport()

  if (!open) return null

  const content = (
    <>
      <div style={headerStyle}>
        <span style={titleStyle}>{band.toUpperCase()} timeseries</span>
        <button style={closeButtonStyle} onClick={onClose} aria-label="Close">
          ×
        </button>
      </div>

      {loading && <div>Loading… (can take a while for a long time range)</div>}
      {error && <div style={errorStyle}>{error}</div>}

      {data && (
        <>
          <div style={rangeInfoStyle}>
            <span>
              Showing {data.points.length} of {totalAvailable} available timestamps
            </span>
            {data.points.length < totalAvailable && (
              <button
                style={loadFullHistoryButtonStyle}
                onClick={onLoadFullHistory}
                disabled={loading}
                title="Fetch every available timestamp instead of just the currently-loaded animation window -- can take significantly longer, the backend processes each file sequentially."
              >
                Load full history
              </button>
            )}
          </div>
          <Plot
            data={[
              {
                x: data.points.map((point) => point.time),
                y: data.points.map((point) => (point.value === null ? null : point.value)),
                type: 'scatter',
                mode: 'lines+markers',
                marker: { color: '#7ab8ff', size: 6 },
                line: { color: '#7ab8ff' },
                text: data.points.map((point) => hoverText(point, maxSatelliteCount, timeDisplay)),
                hoverinfo: 'text',
              },
            ]}
            layout={{
              // On a narrow mobile card (maxWidth 480, vs. this panel's
              // 820 desktop width) a fixed 780px plot would just overflow
              // the card -- autosize + useResizeHandler/style below let
              // Plotly size itself to the actual card width instead.
              ...(isNarrow ? { autosize: true } : { width: 780 }),
              height: isNarrow ? 280 : 320,
              margin: { l: 50, r: 20, t: 10, b: 40 },
              paper_bgcolor: 'rgba(0,0,0,0)',
              plot_bgcolor: 'rgba(0,0,0,0)',
              font: { color: '#edeeef', size: 11 },
              // Axis title stays "Time (UTC)" regardless of the toggle --
              // the plotted x-values/tick labels are always real UTC (see
              // this component's own prop comment on why), only the hover
              // text's displayed time changes. Each hover line is already
              // self-describing either way (formatDateTime appends "UTC"
              // or a local zone abbreviation like "MDT"), so there's no
              // ambiguity left for a dynamic axis title to resolve.
              xaxis: { gridcolor: '#444', title: { text: 'Time (UTC)' } },
              yaxis: { gridcolor: '#444', title: { text: 'Value (K)' } },
            }}
            useResizeHandler={isNarrow}
            style={isNarrow ? { width: '100%' } : undefined}
            config={{
              modeBarButtonsToAdd: [
                {
                  name: 'Download CSV',
                  icon: Plotly.Icons.disk,
                  click: () => downloadCsv(data.points, band),
                },
              ],
            }}
          />
          <div style={disclaimerStyle}>
            Some observations may be revised if additional satellite data becomes available
            later. Satellite count shown per point is how many satellites contributed to that
            timestamp's file, out of the most seen in this range.
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

export default TimeseriesPanel
