import type { CSSProperties, ReactNode } from 'react'
import Panel from './Panel'
import PaletteSelect from './PaletteSelect'
import TimeControls from './TimeControls'
import CalendarControl from './CalendarControl'
import PlaceSearch from './PlaceSearch'
import { mosaicLegendUrl, BAND_LABELS, MOSAIC_UNITS, type MosaicBand } from '../lib/mosaic'
import { BASEMAP_STYLE_LABELS, type BasemapStyle } from '../lib/basemap'
import type { ColorbarGroup } from '../lib/xcube'
import type { MeasureMode } from '../lib/geometry'
import type { TimeDisplayMode } from '../lib/time'
import type { PlaceSearchResult } from '../lib/geocode'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

const MEASURE_MODE_LABELS: Record<MeasureMode, string> = {
  off: 'Off',
  distance: 'Distance',
  area: 'Area',
}

// Fixed per-satellite legend colors -- same as geosat-mosaic-cesium's
// LayerSwitcher.tsx (must stay in sync with the same
// cmap=tab10&vmin=0&vmax=6&norm=cat combination used for the
// contribution mask layer there).
const SATELLITE_LEGEND_COLORS: [string, string][] = [
  ['no data', '#1f77b4'],
  ['goes 19', '#ff7f0e'],
  ['goes 18', '#d62728'],
  ['meteosat 10', '#8c564b'],
  ['meteosat 09', '#e377c2'],
  ['insat 3ds', '#bcbd22'],
  ['himawari 09', '#17becf'],
]

export interface ToggleableLayerState {
  visible: boolean
  opacity: number
}

interface LayerSwitcherProps {
  basemap: ToggleableLayerState
  onBasemapChange: (next: ToggleableLayerState) => void
  basemapStyle: BasemapStyle
  onBasemapStyleChange: (style: BasemapStyle) => void
  coastline: ToggleableLayerState
  onCoastlineChange: (next: ToggleableLayerState) => void
  mosaicOpacity: number
  onMosaicOpacityChange: (opacity: number) => void
  band: MosaicBand
  onBandChange: (band: MosaicBand) => void
  bandDisabled?: boolean
  onInfoClick: () => void
  colorbarGroups: ColorbarGroup[]
  palette: string
  onPaletteChange: (palette: string) => void
  currentTime: string | undefined
  colorMin: number
  onColorMinChange: (value: number) => void
  colorMax: number
  onColorMaxChange: (value: number) => void
  onResetColorRange: () => void
  resettingColorRange?: boolean
  contributionMask: ToggleableLayerState
  onContributionMaskChange: (next: ToggleableLayerState) => void
  pointProbeMode: boolean
  onPointProbeModeChange: (next: boolean) => void
  measureMode: MeasureMode
  onMeasureModeChange: (next: MeasureMode) => void
  deviceLocationEnabled: boolean
  onDeviceLocationEnabledChange: (next: boolean) => void
  deviceLocationError: string | null
  graticuleVisible: boolean
  onGraticuleVisibleChange: (next: boolean) => void
  timeDisplay: TimeDisplayMode
  onTimeDisplayChange: (next: TimeDisplayMode) => void
  syncEnabled: boolean
  onSyncEnabledChange: (next: boolean) => void
  showAlwaysRecent: boolean
  onShowAlwaysRecentChange: (next: boolean) => void
  showAlwaysRecentDisabled?: boolean
  // On a narrow phone screen, Time/Date/Search are rendered inline inside
  // this panel instead of standing alone in their own panels (see the
  // `isNarrow &&` guards below) -- consolidates the mobile layout down to
  // a single panel. Same props
  // TimeControls/CalendarControl/PlaceSearch take standalone, just handed
  // to LayerSwitcher instead so it can embed them via each one's own
  // `bare` prop.
  timestamps: string[]
  timeIndex: number
  onIndexChange: (index: number) => void
  playing: boolean
  onPlayingChange: (playing: boolean) => void
  intervalMs: number
  onIntervalMsChange: (intervalMs: number) => void
  allTimestamps: string[]
  onJump: (absoluteIndex: number) => void
  dateControlsDisabled?: boolean
  onSelectPlace: (result: PlaceSearchResult) => string | null
}

function panelStyle(isNarrow: boolean): CSSProperties {
  return {
    position: 'fixed',
    top: 12,
    left: 12,
    zIndex: 1,
    // `min()`, not a plain `200` -- defensive cap so this panel can never
    // overflow off the left/right edge of a genuinely tiny phone screen.
    // Used to be `min(200px, 45vw)` to avoid overlapping the right-side
    // Time/Date/Search column on a narrow screen -- now that those are
    // embedded into this panel instead on mobile (see the `isNarrow &&`
    // guards below), there's no longer a second right-side panel to
    // collide with, so the vw share is widened to `92vw`. With the old
    // `45vw`, on any phone-width viewport (45vw < ~444px is every phone),
    // this panel's own content box shrank below the fixed-width colorbar
    // (`COLORBAR_LENGTH = 200`) and the embedded Date/Time fields,
    // forcing a horizontal scrollbar -- `overflowY: 'auto'` alone
    // implicitly forces `overflowX` to `auto` too per the CSS Overflow
    // spec (mixing `visible` with a non-`visible` value on the other axis
    // isn't allowed, so the browser silently promotes it). `92vw` keeps
    // this panel's width comfortably above 200px content on every device
    // (even a 220px-wide viewport clears it), so the overflow condition
    // itself stops happening; `overflowX: 'hidden'` below is a defensive
    // backstop for the few-px-wide theoretical edge case, not the primary
    // fix.
    width: 'min(200px, 92vw)',
    // `100vh - 100px` on a narrow phone, not the `24px` desktop uses --
    // a bigger top/bottom margin on mobile, since this panel now holds a
    // lot more content there, with Time/Date/Search all embedded (see
    // the `isNarrow &&` guards below). Was `50px`, widened further after
    // that still wasn't enough. Desktop keeps the original tighter `24px`
    // margin.
    maxHeight: isNarrow ? 'calc(100vh - 100px)' : 'calc(100vh - 24px)',
    overflowY: 'auto',
    overflowX: 'hidden',
  }
}

const rowStyle: CSSProperties = {
  marginBottom: 8,
}

const labelStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  marginBottom: 2,
}

const sliderStyle: CSSProperties = {
  width: '100%',
}

const selectStyle: CSSProperties = {
  width: '100%',
  marginBottom: 4,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

const labelAccessorySelectStyle: CSSProperties = {
  marginLeft: 'auto',
  minWidth: 0,
  maxWidth: 108,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  fontSize: 11,
}

const COLORBAR_LENGTH = 200
const COLORBAR_THICKNESS = 14

const colorbarWrapStyle: CSSProperties = {
  width: COLORBAR_LENGTH,
  height: COLORBAR_THICKNESS,
  overflow: 'hidden',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  marginTop: 4,
}

const colorbarImgStyle: CSSProperties = {
  transform: 'rotate(90deg)',
  display: 'block',
}

const colorbarLabelsStyle: CSSProperties = {
  display: 'flex',
  justifyContent: 'space-between',
  fontSize: 11,
  marginBottom: 4,
}

const satelliteChipRowStyle: CSSProperties = {
  display: 'flex',
  flexWrap: 'wrap',
  gap: '4px 8px',
  marginTop: 4,
  marginBottom: 4,
}

const satelliteChipStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 4,
  fontSize: 11,
}

const satelliteSwatchStyle: CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: 2,
  flexShrink: 0,
}

const rangeRowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  marginBottom: 4,
}

const numberInputStyle: CSSProperties = {
  width: 0,
  flex: 1,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

const bandRowStyle: CSSProperties = {
  display: 'flex',
  gap: 4,
  marginBottom: 4,
}

const toolRowStyle: CSSProperties = {
  ...labelStyle,
  marginTop: 8,
  marginBottom: 0,
}

const measureRowStyle: CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'space-between',
  gap: 6,
  marginTop: 8,
}

const measureSelectStyle: CSSProperties = {
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
}

const infoButtonStyle: CSSProperties = {
  flexShrink: 0,
  width: 22,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  lineHeight: 1,
  cursor: 'pointer',
  fontStyle: 'italic',
  fontFamily: 'Georgia, serif',
}

const resetButtonStyle: CSSProperties = {
  flexShrink: 0,
  background: '#1c1c1c',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 3,
  padding: '2px 6px',
  lineHeight: 1,
  cursor: 'pointer',
}

// Wraps the embedded (narrow-viewport-only) Time/Date/Search sections --
// a top border + padding so they read as a distinct sub-section of this
// panel, not just more rows blended into the surrounding toggle list.
// Same treatment geosat-mosaic-cesium's own LayerSwitcher.tsx uses for
// its own embedded Date/Search sections.
const embeddedSectionStyle: CSSProperties = {
  marginTop: 10,
  paddingTop: 10,
  borderTop: '1px solid #444',
}

function LayerRow({
  label,
  visible,
  onVisibleChange,
  visibleDisabled,
  opacity,
  onOpacityChange,
  labelAccessory,
  children,
}: {
  label: string
  visible: boolean
  onVisibleChange?: (visible: boolean) => void
  visibleDisabled?: boolean
  opacity: number
  onOpacityChange: (opacity: number) => void
  labelAccessory?: ReactNode
  children?: ReactNode
}) {
  return (
    <div style={rowStyle}>
      <label style={labelStyle}>
        <input
          type="checkbox"
          checked={visible}
          disabled={visibleDisabled}
          onChange={(event) => onVisibleChange?.(event.target.checked)}
        />
        {label}
        {labelAccessory}
      </label>
      <input
        style={sliderStyle}
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={opacity}
        disabled={!visible}
        onChange={(event) => onOpacityChange(Number(event.target.value))}
      />
      {children}
    </div>
  )
}

// Full feature set as of Pass 5 of this app's pass-by-pass build plan.
// No embedded-mobile-panels (Date/Search folded into this panel on a
// narrow screen) or full-screen-modal dropdowns the way geosat-mosaic-
// cesium's own LayerSwitcher.tsx later grew -- that was extensive,
// iterative polish driven by direct phone testing on that app; this
// app's own mobile pass (see Map.tsx) is deliberately lighter, not a
// blind guess at replicating every one of those follow-up rounds without
// the same testing.
function LayerSwitcher({
  basemap,
  onBasemapChange,
  basemapStyle,
  onBasemapStyleChange,
  coastline,
  onCoastlineChange,
  mosaicOpacity,
  onMosaicOpacityChange,
  band,
  onBandChange,
  bandDisabled,
  onInfoClick,
  colorbarGroups,
  palette,
  onPaletteChange,
  currentTime,
  colorMin,
  onColorMinChange,
  colorMax,
  onColorMaxChange,
  onResetColorRange,
  resettingColorRange,
  contributionMask,
  onContributionMaskChange,
  pointProbeMode,
  onPointProbeModeChange,
  measureMode,
  onMeasureModeChange,
  deviceLocationEnabled,
  onDeviceLocationEnabledChange,
  deviceLocationError,
  graticuleVisible,
  onGraticuleVisibleChange,
  timeDisplay,
  onTimeDisplayChange,
  syncEnabled,
  onSyncEnabledChange,
  showAlwaysRecent,
  onShowAlwaysRecentChange,
  showAlwaysRecentDisabled,
  timestamps,
  timeIndex,
  onIndexChange,
  playing,
  onPlayingChange,
  intervalMs,
  onIntervalMsChange,
  allTimestamps,
  onJump,
  dateControlsDisabled,
  onSelectPlace,
}: LayerSwitcherProps) {
  // Same reasoning as TimeControls.tsx/CalendarControl.tsx/PlaceSearch.tsx's
  // own `defaultCollapsed={isNarrow}` -- this is by far the tallest panel
  // in the app (basemap+mosaic+mask+coastline+probe+measure+location+
  // graticule+time/sync rows all in one), so it's the single biggest
  // contributor to the narrow-viewport overlap problem those panels were
  // already fixed for.
  const isNarrow = useIsNarrowViewport()
  return (
    <Panel title="Layers" style={panelStyle(isNarrow)} defaultCollapsed={isNarrow}>
      <LayerRow
        label="Basemap"
        visible={basemap.visible}
        onVisibleChange={(visible) => onBasemapChange({ ...basemap, visible })}
        opacity={basemap.opacity}
        onOpacityChange={(opacity) => onBasemapChange({ ...basemap, opacity })}
        labelAccessory={
          <select
            style={labelAccessorySelectStyle}
            value={basemapStyle}
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => onBasemapStyleChange(event.target.value as BasemapStyle)}
          >
            {(Object.entries(BASEMAP_STYLE_LABELS) as [BasemapStyle, string][]).map(
              ([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ),
            )}
          </select>
        }
      />
      <LayerRow
        label="Satellite mosaic"
        visible
        visibleDisabled
        opacity={mosaicOpacity}
        onOpacityChange={onMosaicOpacityChange}
      >
        <div style={bandRowStyle}>
          <select
            style={{ ...selectStyle, width: 0, flex: 1, marginBottom: 0 }}
            value={band}
            disabled={bandDisabled}
            onChange={(event) => onBandChange(event.target.value as MosaicBand)}
          >
            {(Object.entries(BAND_LABELS) as [MosaicBand, string][]).map(([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ))}
          </select>
          <button
            type="button"
            style={infoButtonStyle}
            onClick={onInfoClick}
            title={`${BAND_LABELS[band]} file/metadata/thumbnail info`}
          >
            i
          </button>
        </div>
        <PaletteSelect palette={palette} onPaletteChange={onPaletteChange} colorbarGroups={colorbarGroups} />
        <div style={rangeRowStyle}>
          <input
            style={numberInputStyle}
            type="number"
            value={colorMin}
            onChange={(event) => onColorMinChange(Number(event.target.value))}
          />
          <span>–</span>
          <input
            style={numberInputStyle}
            type="number"
            value={colorMax}
            onChange={(event) => onColorMaxChange(Number(event.target.value))}
          />
          <button
            type="button"
            style={resetButtonStyle}
            onClick={onResetColorRange}
            disabled={resettingColorRange}
            title="Reset to the real min/max for the current band and timestamp"
          >
            {resettingColorRange ? '…' : '↺'}
          </button>
        </div>
        {currentTime && (
          <div style={colorbarWrapStyle}>
            <img
              style={colorbarImgStyle}
              src={mosaicLegendUrl(band, palette, colorMin, colorMax, currentTime, COLORBAR_LENGTH, COLORBAR_THICKNESS)}
              width={COLORBAR_THICKNESS}
              height={COLORBAR_LENGTH}
              alt={`${BAND_LABELS[band]} colorbar (${palette}, ${colorMin}-${colorMax})`}
            />
          </div>
        )}
        <div style={colorbarLabelsStyle}>
          <span>{colorMin}</span>
          <span>{MOSAIC_UNITS}</span>
          <span>{colorMax}</span>
        </div>
      </LayerRow>
      {isNarrow && (
        <div style={embeddedSectionStyle}>
          <TimeControls
            bare
            timestamps={timestamps}
            index={timeIndex}
            onIndexChange={onIndexChange}
            playing={playing}
            onPlayingChange={onPlayingChange}
            intervalMs={intervalMs}
            onIntervalMsChange={onIntervalMsChange}
            timeDisplay={timeDisplay}
          />
        </div>
      )}
      {isNarrow && (
        <div style={embeddedSectionStyle}>
          <CalendarControl
            bare
            allTimestamps={allTimestamps}
            currentTime={currentTime}
            onJump={onJump}
            disabled={dateControlsDisabled}
            timeDisplay={timeDisplay}
          />
        </div>
      )}
      <LayerRow
        label="Contribution mask"
        visible={contributionMask.visible}
        onVisibleChange={(visible) => onContributionMaskChange({ ...contributionMask, visible })}
        opacity={contributionMask.opacity}
        onOpacityChange={(opacity) => onContributionMaskChange({ ...contributionMask, opacity })}
      >
        {contributionMask.visible && (
          <div style={satelliteChipRowStyle}>
            {SATELLITE_LEGEND_COLORS.map(([label, color]) => (
              <span key={label} style={satelliteChipStyle}>
                <span style={{ ...satelliteSwatchStyle, background: color }} />
                {label}
              </span>
            ))}
          </div>
        )}
      </LayerRow>
      <LayerRow
        label="Coastline"
        visible={coastline.visible}
        onVisibleChange={(visible) => onCoastlineChange({ ...coastline, visible })}
        opacity={coastline.opacity}
        onOpacityChange={(opacity) => onCoastlineChange({ ...coastline, opacity })}
      />
      <label style={toolRowStyle}>
        <input
          type="checkbox"
          checked={pointProbeMode}
          onChange={(event) => onPointProbeModeChange(event.target.checked)}
        />
        Point probe (click map for value)
      </label>
      <div style={measureRowStyle}>
        <span>Measure</span>
        <select
          style={measureSelectStyle}
          value={measureMode}
          onChange={(event) => onMeasureModeChange(event.target.value as MeasureMode)}
        >
          {(Object.entries(MEASURE_MODE_LABELS) as [MeasureMode, string][]).map(
            ([value, text]) => (
              <option key={value} value={value}>
                {text}
              </option>
            ),
          )}
        </select>
      </div>
      <label style={toolRowStyle} title={deviceLocationError ?? undefined}>
        <input
          type="checkbox"
          checked={deviceLocationEnabled}
          onChange={(event) => onDeviceLocationEnabledChange(event.target.checked)}
        />
        My location{deviceLocationError ? ' (unavailable)' : ''}
      </label>
      <label style={toolRowStyle}>
        <input
          type="checkbox"
          checked={graticuleVisible}
          onChange={(event) => onGraticuleVisibleChange(event.target.checked)}
        />
        Graticule
      </label>
      <label style={toolRowStyle}>
        <input
          type="checkbox"
          checked={timeDisplay === 'local'}
          onChange={(event) => onTimeDisplayChange(event.target.checked ? 'local' : 'utc')}
        />
        Local time
      </label>
      <label style={toolRowStyle} title="Refresh the available timestamp range every 5 minutes">
        <input
          type="checkbox"
          checked={syncEnabled}
          onChange={(event) => onSyncEnabledChange(event.target.checked)}
        />
        Sync
      </label>
      <label style={toolRowStyle} title="Also jump to the newest timestamp on every sync refresh">
        <input
          type="checkbox"
          checked={showAlwaysRecent}
          disabled={showAlwaysRecentDisabled}
          onChange={(event) => onShowAlwaysRecentChange(event.target.checked)}
        />
        Show always recent
      </label>
      {isNarrow && (
        <div style={embeddedSectionStyle}>
          <PlaceSearch bare onSelect={onSelectPlace} />
        </div>
      )}
    </Panel>
  )
}

export default LayerSwitcher
