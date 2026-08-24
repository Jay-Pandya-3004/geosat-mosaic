import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import Panel from './Panel'
import { mosaicLegendUrl, type MosaicBand } from './MosaicLayer'
import { BASEMAP_STYLE_LABELS, type BasemapStyle } from './BasemapLayer'
import PaletteSelect from './PaletteSelect'
import TimeControls from './TimeControls'
import CalendarControl from './CalendarControl'
import PlaceSearch from './PlaceSearch'
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

// Fixed per-satellite legend colors -- must stay in sync with
// ContributionMaskLayer.tsx's SATELLITE_CMAP/VMIN/VMAX (`tab10`, 0, 6):
// these are that same `cmap=tab10&vmin=0&vmax=6&norm=cat` combination's
// output colors for flag values 0-6, checked for 3 of the 7 (index 1, 3, 9
// of tab10) by sampling actual rendered tile pixels and matching the
// standard matplotlib tab10 reference palette for the rest. Flag-value ->
// satellite-name order comes from THREDDS's own OPeNDAP DAS output for
// satellite_id_mir/wv/tir1/tir2 (identical across all 4 bands: 0=no_data,
// 1=goes_19, 2=goes_18, 3=meteosat_10, 4=meteosat_09, 5=insat_3ds,
// 6=himawari_09), despite each band's raw flag_values/flag_meanings
// attribute pair listing entries in a different on-disk order. Replaces
// the THREDDS/EDAL `default-categorical`-sampled colors this had before
// the 2026-08-15 xcube tile-serving switch (see ContributionMaskLayer.tsx)
// -- an accepted visual change, since xcube has no equivalent of assigning
// arbitrary fixed hex colors server-side.
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

// Label text for this app's own band naming (MIR/WV/TIR1/TIR2).
const BAND_LABELS: Record<MosaicBand, string> = {
  mir: 'MIR',
  wv: 'WV',
  tir1: 'TIR1',
  tir2: 'TIR2',
}

// All 4 bands are brightness temperatures in Kelvin -- per THREDDS/EDAL's
// layerDetails `units` field for value_mir, same schema across bands.
const MOSAIC_UNITS = 'K'

interface LayerSwitcherProps {
  basemap: ToggleableLayerState
  onBasemapChange: (next: ToggleableLayerState) => void
  // User-selectable basemap with multiple openly-accessible options --
  // see BasemapLayer.tsx.
  basemapStyle: BasemapStyle
  onBasemapStyleChange: (style: BasemapStyle) => void
  coastline: ToggleableLayerState
  onCoastlineChange: (next: ToggleableLayerState) => void
  mosaicOpacity: number
  onMosaicOpacityChange: (opacity: number) => void
  band: MosaicBand
  onBandChange: (band: MosaicBand) => void
  // Not wired up yet -- reserved for the layer/control
  // interlocks: band switching must disable while the (not-yet-built)
  // animation player is running.
  bandDisabled?: boolean
  // Info button next to the band name -- opens InfoPanel.tsx.
  onInfoClick: () => void
  // xcube colormap/colorbar selection + user-customizable min/max --
  // shared across all 4 bands, see MosaicLayer.tsx.
  // Already grouped by xcube's own `/colorbars` response -- see
  // lib/xcube.ts's fetchColorbars.
  colorbarGroups: ColorbarGroup[]
  palette: string
  onPaletteChange: (palette: string) => void
  // Needed to build the xcube legend/colorbar image URL below -- xcube's
  // per-file datasets mean even a palette-gradient-only legend request
  // needs a real, currently-valid dataset id. See MosaicLayer.tsx's
  // mosaicLegendUrl.
  currentTime: string | undefined
  colorMin: number
  onColorMinChange: (value: number) => void
  colorMax: number
  onColorMaxChange: (value: number) => void
  onResetColorRange: () => void
  resettingColorRange?: boolean
  contributionMask: ToggleableLayerState
  onContributionMaskChange: (next: ToggleableLayerState) => void
  // Point-probe mode toggle -- not
  // a layer (no opacity), just an on/off click-to-probe mode, so it gets
  // its own simple checkbox row instead of reusing LayerRow.
  pointProbeMode: boolean
  onPointProbeModeChange: (next: boolean) => void
  // Measurement tools -- measure distance and
  // calculate area: three-way (not just on/off, distance vs area are
  // mutually exclusive draw modes), so a select fits better than a
  // checkbox.
  measureMode: MeasureMode
  onMeasureModeChange: (next: MeasureMode) => void
  // Device location marker (tick mark).
  deviceLocationEnabled: boolean
  onDeviceLocationEnabledChange: (next: boolean) => void
  deviceLocationError: string | null
  // Graticule (lat/lon grid) toggle.
  graticuleVisible: boolean
  onGraticuleVisibleChange: (next: boolean) => void
  // GMT/UTC vs. local time toggle -- applies everywhere at once. Global, not
  // per-widget -- see Globe.tsx's `timeDisplay` state.
  timeDisplay: TimeDisplayMode
  onTimeDisplayChange: (next: TimeDisplayMode) => void
  // Live sync + "show always recent" toggles. Sync's own
  // checkbox is never disabled (it stays enabled during animation --
  // only touching bounds doesn't conflict with
  // playback); "show always recent" is disabled while playing, same
  // interlock group as band/date controls.
  syncEnabled: boolean
  onSyncEnabledChange: (next: boolean) => void
  showAlwaysRecent: boolean
  onShowAlwaysRecentChange: (next: boolean) => void
  showAlwaysRecentDisabled?: boolean
  // On a narrow phone screen, Time (scrubber/play/speed), Date (calendar
  // + time-of-day), and Search (Nominatim place search) are all embedded
  // directly inside this "Layers" panel instead of floating as their own
  // separate panels (Time used to simply not render at all on mobile --
  // it's embedded now too, matching geosat-mosaic-openlayers' own
  // treatment) -- see this component's own
  // render below and TimeControls.tsx/CalendarControl.tsx/
  // PlaceSearch.tsx's `bare` prop. Same props those three components take
  // standalone in Globe.tsx's desktop-only right column.
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
    // Used to be pushed down to `top: 60` on a narrow phone to clear
    // Cesium's own top-right toolbar (Home/scene-mode) plus this app's
    // own Help/About buttons, which used to sit side-by-side on the same
    // `top: 12` row far enough left to collide with this panel's own
    // header below ~367px of viewport width (measured). To reclaim that
    // unused top strip on mobile, Help now stacks *below* About on a
    // narrow screen instead of beside it (see Globe.tsx's
    // `helpButtonStyle`) -- that alone moves the toolbar's effective
    // leftmost reach from ~190px in from the right edge down to ~131px,
    // re-solving the same collision arithmetic to "collides below ~260px
    // of viewport width", narrower than any real phone. `top: 12` is safe
    // again at every width this app actually needs to support.
    top: 12,
    left: 12,
    zIndex: 1,
    // `min()`, not a plain `200` -- defensive cap so this panel can never
    // overflow off the left/right edge of a genuinely tiny phone screen.
    // Used to be `min(200px, 45vw)` to avoid overlapping the right-side
    // Time/Date/Search column on a narrow screen -- now that Time/Date/
    // Search are all embedded into this panel instead on mobile (see the
    // `isNarrow &&` guards below), there's no longer a second right-side
    // panel to collide with, so the vw share is widened to `92vw`. Same
    // bug already fixed in geosat-mosaic-openlayers' own LayerSwitcher.tsx:
    // on any phone-width viewport (45vw < ~444px is every phone), this
    // panel's own content box shrank below the then-fixed-width colorbar
    // (since fixed too, see colorbarWrapStyle's own comment below) and
    // other full-width content, forcing a horizontal scrollbar --
    // `overflowY: 'auto'` alone implicitly forces `overflowX` to `auto`
    // too per the CSS Overflow spec (mixing `visible` with a non-`visible`
    // value on the other axis isn't allowed, so the browser silently
    // promotes it). `92vw` keeps this panel's width comfortably above
    // 200px content on every device, so the overflow condition itself
    // stops happening; `overflowX: 'hidden'` below is a defensive
    // backstop for the few-px-wide theoretical edge case, not the
    // primary fix.
    width: 'min(200px, 92vw)',
    // Defensive cap so this panel can never grow past the viewport,
    // however tall its content gets. `100vh - 100px` on a narrow phone,
    // not the `24px` desktop uses -- a bigger top/bottom margin on mobile
    // now that this panel holds a lot more content there (Time/Date/
    // Search all embedded).
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

// A `labelAccessory` select (e.g. the basemap-style picker) -- pushed to
// the row's right edge via `marginLeft: auto`, shrinkable/truncatable
// since the row is narrow (200px panel) and the label text already takes
// real space.
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

// Horizontal continuous colorbar: a tall/narrow xcube `/legend` image
// (see mosaicLegendUrl) rotated 90deg into a wide/short footprint --
// xcube's gradient always varies along its requested `height` axis
// regardless of aspect ratio (decoding the actual PNG pixels showed a
// landscape 200x14 request still came back with a uniform color across
// every row, varying top-to-bottom down the 14-tall axis, not
// left-to-right), same rotate-90 CSS need as THREDDS/EDAL's old
// COLORBARONLY mode this replaced, just discovered after initially
// assuming xcube didn't need it. The wrapper is sized to the *rotated*
// (final, on-screen) dimensions and centers its child via flexbox -- flex
// positions the image's own pre-rotation box before the transform is
// applied, so its center lands exactly on the wrapper's center, and
// rotating in place around that shared center then fills the wrapper
// edge-to-edge without any translate/percentage math.
//
// This used to be a fixed `width: 200` (matching the panel's old fixed
// desktop content width) -- once the panel's own width became responsive
// (`min(200px, 92vw)`, see panelStyle's own comment), a fixed-200
// colorbar no longer matched the *actual* rendered row width on a wider
// mobile panel, so it visibly fell short of (or, before that fix,
// overflowed past) where the max-value label at the row's own right edge
// actually sat. Fixed by measuring the wrap element's own rendered width
// live (`ResizeObserver`, see the component body below) and using that
// measured value both for the wrapper's own CSS (`width: '100%'`, so it
// just follows the row like every other full-width control already does)
// and for the pre-rotation image's `height` attribute + the
// `mosaicLegendUrl` request itself -- the rotated image's post-rotation
// *visual* width equals its pre-rotation `height`, so this keeps the
// gradient pixels stretched to fill exactly as much width as the wrapper
// really has, instead of a stale fixed number that can drift from it.
const DEFAULT_COLORBAR_LENGTH = 200
const COLORBAR_THICKNESS = 14

const colorbarWrapStyle: CSSProperties = {
  width: '100%',
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

// Wraps the embedded (narrow-viewport-only) CalendarControl/PlaceSearch
// sections -- a top border + padding so they read as a distinct
// sub-section of this panel, not just more rows blended into the
// surrounding toggle list.
const embeddedSectionStyle: CSSProperties = {
  marginTop: 10,
  paddingTop: 10,
  borderTop: '1px solid #444',
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
  // Rendered inside the same flex row as the checkbox/label, pushed to
  // the row's right edge -- beside the Basemap label, not below it as a
  // full-width child row like the band/style
  // selects further down. Nested inside the <label> intentionally: a
  // click on a nested form control (this select) doesn't bubble into
  // toggling the checkbox the <label> is otherwise associated with --
  // standard, safe HTML behavior, not a hack.
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

// One checkbox (on/off) + one opacity slider per layer -- the four layers,
// the switcher (checkboxes), and per-layer opacity, all four layers now
// present. Satellite mosaic's
// checkbox is intentionally non-interactive: it's the mandatory layer
// whose checkbox cannot be unchecked by the user.
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
  // This is by far the tallest/densest of the panels -- collapsed by
  // default on a phone-width screen (not a smallish desktop window; see
  // useIsNarrowViewport's own default breakpoint) so the map isn't
  // immediately covered by a long scrolling list of controls on load. A
  // one-time initial value, not a continuously-enforced one -- the user
  // can still expand it and it stays expanded, same as everywhere else
  // Panel is used.
  const isNarrow = useIsNarrowViewport()

  // Measures colorbarWrapStyle's own real rendered width live -- see that
  // const's own comment for why this needs to be measured rather than a
  // fixed number, now that the panel's own width is responsive.
  // `ResizeObserver`, not just a `resize` listener, since this needs to
  // react to the *panel* changing width (collapsing/expanding, or the
  // panel's own `min(200px, 92vw)` recomputing on rotation) too, not just
  // the window itself.
  const colorbarWrapRef = useRef<HTMLDivElement>(null)
  const [colorbarLength, setColorbarLength] = useState(DEFAULT_COLORBAR_LENGTH)
  useEffect(() => {
    const el = colorbarWrapRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width) setColorbarLength(Math.round(width))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Row order matches the Cesium stacking order bottom-to-top: Basemap
  // (index 0) -> Satellite mosaic (index 1) ->
  // Contribution mask (index 2) -- see those components' own `index`
  // props -- -> Coastline last, since it's not an imagery layer at all
  // (a GeoJsonDataSource of vector polylines, per CoastlineLayer.tsx),
  // which Cesium always renders above every imagery layer regardless of
  // any index, making it the true topmost layer no index setting could
  // otherwise express.
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
            {(Object.entries(BAND_LABELS) as [MosaicBand, string][]).map(
              ([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ),
            )}
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
        <PaletteSelect
          palette={palette}
          onPaletteChange={onPaletteChange}
          colorbarGroups={colorbarGroups}
        />
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
          <div ref={colorbarWrapRef} style={colorbarWrapStyle}>
            <img
              style={colorbarImgStyle}
              src={mosaicLegendUrl(
                band,
                palette,
                colorMin,
                colorMax,
                currentTime,
                colorbarLength,
                COLORBAR_THICKNESS,
              )}
              // Portrait, matching the pre-rotation image mosaicLegendUrl
              // actually requested from xcube -- colorbarImgStyle's
              // rotate(90deg) turns this into the colorbarLength x
              // COLORBAR_THICKNESS landscape shape the wrapper expects.
              // `colorbarLength` (state, not the old fixed constant) is
              // measured live from the wrapper's own real rendered width
              // -- see this component's own `ResizeObserver` effect.
              width={COLORBAR_THICKNESS}
              height={colorbarLength}
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
        onVisibleChange={(visible) =>
          onContributionMaskChange({ ...contributionMask, visible })
        }
        opacity={contributionMask.opacity}
        onOpacityChange={(opacity) =>
          onContributionMaskChange({ ...contributionMask, opacity })
        }
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
      <label
        style={toolRowStyle}
        title="Also jump to the newest timestamp on every sync refresh"
      >
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
