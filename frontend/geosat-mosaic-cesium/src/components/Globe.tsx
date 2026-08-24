import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from 'react'
import { Entity, Viewer, type CesiumComponentRef } from 'resium'
import {
  Cartesian2,
  Cartesian3,
  Cartographic,
  Color,
  EllipsoidTerrainProvider,
  Math as CesiumMath,
  PolygonHierarchy,
  ScreenSpaceEventHandler,
  ScreenSpaceEventType,
  SceneMode,
  Viewer as CesiumViewer,
} from 'cesium'
import MosaicLayer, { type MosaicBand } from './MosaicLayer'
import BasemapLayer, { type BasemapStyle } from './BasemapLayer'
import CoastlineLayer from './CoastlineLayer'
import ContributionMaskLayer from './ContributionMaskLayer'
import LayerSwitcher, { type ToggleableLayerState } from './LayerSwitcher'
import TimeControls from './TimeControls'
import CalendarControl from './CalendarControl'
import AnimationDriver from './AnimationDriver'
import InfoPanel from './InfoPanel'
import AppInfoPanel from './AppInfoPanel'
import HelpPanel from './HelpPanel'
import ScanTimeLabel from './ScanTimeLabel'
import PointProbePanel from './PointProbePanel'
// Lazy-loaded: Plotly.js is large (~4.9MB unminified bundle contribution
// per `vite build`) -- not worth paying for on every page load when most
// sessions may never open a timeseries chart.
const TimeseriesPanel = lazy(() => import('./TimeseriesPanel'))
import MeasurePanel from './MeasurePanel'
import Graticule from './Graticule'
import PlaceSearch from './PlaceSearch'
import { fetchAvailableTimestamps } from '../lib/timestamps'
import { TIMESERIES_DEFAULT_MODE, TIMESERIES_DEFAULT_WINDOW_SIZE } from '../lib/timeseriesConfig'
import { fetchColorbars, type ColorbarGroup } from '../lib/xcube'
import {
  fetchMinMax,
  fetchPointProbe,
  fetchTimeseries,
  type PointProbeResult,
  type TimeseriesResponse,
} from '../lib/metadataApi'
import type { MeasureMode } from '../lib/geometry'
import type { PlaceSearchResult } from '../lib/geocode'
import type { TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

// Home destination + shared max zoom-out cap: Home should show the whole
// globe/map centered on 0,0, and zooming out can never go further than
// that (so the globe/map can't shrink to a tiny speck). Home and the
// zoom-out cap are deliberately the *same* value in every mode -- you can
// zoom out until you reach the Home view, and no further.
//
// 3D: a plain Cartesian3 position, not a Rectangle -- a full
// -180/-90/180/90 Rectangle destination is a degenerate case for Cesium's
// rectangle-fitting flyTo: it doesn't actually center on 0,0, lands off
// toward the Americas instead. 20,000km altitude comfortably frames the
// whole disc with a little margin (Earth's radius is ~6,371km) and lands
// at exactly lon=0, lat=0, height=20,000,000.
const THREE_D_MAX_HEIGHT = 20_000_000
const GLOBAL_VIEW_DESTINATION = Cartesian3.fromDegrees(0, 0, THREE_D_MAX_HEIGHT)

// 2D and Columbus View have no equivalent "fly to a Rectangle" option
// (same degenerate-full-longitude problem as 3D), so their Home/max-zoom
// destination is a calibrated Cartesian3 height instead. The height that
// shows exactly pole-to-pole (the maximum extent any flat map projection
// can ever show -- "full map visible") differs by mode (2D is a true
// top-down orthographic projection, Columbus View is not), calibrated
// empirically rather than derived from a formula: at nadir (heading/
// pitch/roll = 0/-90/0), `camera.computeViewRectangle()`'s visible
// latitude span scales exactly linearly with
// `camera.positionCartographic.height` in both modes (doubling height
// exactly doubles the visible degree span), just with a different
// constant per mode. These are degrees-of-visible-latitude per meter of
// height, from that measurement (re-derive with the same technique --
// double the height and confirm the visible degree span exactly doubles
// -- if these ever need re-deriving):
const TWO_D_DEGREES_PER_METER = 2.090805430160996e-6
const COLUMBUS_DEGREES_PER_METER = 2.414254164846964e-6
const FULL_MAP_DEGREES = 90 // pole-to-pole

function fullMapHeight(mode: SceneMode): number {
  const degreesPerMeter =
    mode === SceneMode.SCENE2D ? TWO_D_DEGREES_PER_METER : COLUMBUS_DEGREES_PER_METER
  return FULL_MAP_DEGREES / degreesPerMeter
}

const NADIR_ORIENTATION = { heading: 0, pitch: -Math.PI / 2, roll: 0 }

// Northern/southern edge of the data, from the coverage-extent
// measurement (also duplicated as COVERAGE_RECTANGLE in
// MosaicLayer.tsx/ContributionMaskLayer.tsx for the WMS layer bounds) --
// drives the place-search ±66° 2D-mode restriction just below.
const COVERAGE_LAT_LIMIT = 66.49102

// City-level zoom for a place-search result -- close enough to actually
// see the searched location, not just re-show the full map.
const PLACE_SEARCH_HEIGHT = 500_000

// Kept near the Home button, not tucked into a bottom corner (tried both
// -- bottom-right directly overlapped Cesium's own 29x29 fullscreen
// button, bottom-left then clipped into Cesium's attribution/credits
// strip). Placed immediately to the left of Cesium's own top-right
// toolbar (now just Home + scene-mode, since navigationHelpButton is
// disabled below), same row.
//
// The Help-About gap, the About-Home gap, and Home's own gap to the
// scene-mode button should all match. Measured via getBoundingClientRect
// (1920px-wide viewport): Home sits at x:1842-1874, the scene-mode button
// right next to it at x:1880-1912 -- a `6px` gap between Cesium's own two
// toolbar buttons. Working backward from that same `6px` for this button:
// this button's right edge should land at `home.left - 6 = 1836`, i.e.
// `right: 1920 - 1836 = 84` -- solved from the two measured quantities
// above.
const appInfoButtonStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  right: 84,
  zIndex: 1,
  padding: '4px 10px',
  background: 'rgba(38, 38, 38, 0.8)',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 4,
  cursor: 'pointer',
  font: '12px sans-serif',
}

// Desktop/tablet: same row as `appInfoButtonStyle`, immediately to its
// left, same `6px` gap (see that constant's comment for the derivation)
// -- solved as `appInfoButton.left - 6`, using this button's own measured
// 46.6875px width: `right: 1920 - (1782.640625 - 6 - 46.6875) = 143`.
// Replaces Cesium's own default Navigation Instructions ("?") button
// (disabled via `navigationHelpButton={false}` on <Viewer> below) -- this
// button's content is app-specific (HelpPanel.tsx) instead of Cesium's
// generic mouse/touch camera-control popup.
//
// On a narrow phone screen this stacks *below* About instead of beside it
// (`top` pushed down by one row's worth instead of `right` pushed further
// left) -- frees up the horizontal strip these two buttons used to occupy
// together, which is what lets LayerSwitcher sit at `top: 12` again on
// mobile without colliding with this toolbar (see LayerSwitcher.tsx's own
// `panelStyle` comment for the arithmetic).
function helpButtonStyle(isNarrow: boolean): CSSProperties {
  return isNarrow
    ? { ...appInfoButtonStyle, top: 44, right: 84 }
    : { ...appInfoButtonStyle, right: 143 }
}

// Shared flex column for the right-side stack (Time, then Date, then
// Search) -- replaces each of those three panels' own hardcoded pixel
// `top` offset (60/187/297) now that all three are collapsible: a
// hardcoded offset assumed a fixed rendered height for the panel(s)
// above it, which broke the instant any one of them collapsed (the ones
// below would float with a big gap, or the reverse). A flex column
// reflows automatically regardless of which panels are collapsed. `top:
// 60` (not 12) still clears Cesium's own top-right toolbar (Home/
// scene-mode/Help/About buttons), same reasoning TimeControls' own
// offset used before this change.
const rightColumnStyle: CSSProperties = {
  position: 'fixed',
  top: 60,
  right: 12,
  zIndex: 1,
  display: 'flex',
  flexDirection: 'column',
  // 17px, not a rounder number -- matches the gap these three panels
  // already had (the two gaps, Time to Date and Date to Search, were made
  // equal, measured via getBoundingClientRect at the time). Keeping the
  // same value here so this refactor doesn't itself change any visible
  // spacing.
  gap: 17,
  // `min()`, not a plain `200px` -- on a narrow phone, this column and
  // LayerSwitcher (left column, same `min()` treatment -- see
  // LayerSwitcher.tsx's own comment) would otherwise both stay 200px
  // wide and physically overlap. Set once here, not on each of the three
  // children -- flex's default `align-items: stretch` makes them fill
  // this width automatically.
  width: 'min(200px, 45vw)',
}

// Natural Earth II — CesiumJS's own bundled offline imagery, no Ion
// account/token. Ellipsoid terrain (not Cesium World Terrain, an Ion
// asset) for the same reason.
function Globe() {
  // Drives a whole cluster of mobile-only layout decisions below: Date/
  // Search embedded into the Layers panel instead of their own panels,
  // the animation Time panel hidden outright (heavy continuous tile
  // reloads during playback aren't a good fit for a mobile connection/
  // GPU -- see the `!isNarrow &&` guard around `rightColumnStyle`
  // below), and the Help button stacking under About instead of beside
  // it.
  const isNarrow = useIsNarrowViewport()
  const viewerRef = useRef<CesiumComponentRef<CesiumViewer>>(null)
  // Bug (2026-08-15, found via repeated Lighthouse mobile-throttled runs,
  // ~1/14 reproduction rate): if <Viewer> mounts while its container
  // still measures 0 width -- a rare race between this app's CSS actually
  // being applied and Cesium's canvas creation, more likely under mobile
  // CPU/network conditions -- Cesium's own render loop throws
  // `DeveloperError: Expected width to be greater than 0` inside its
  // first frame and, per Cesium's own source (`startRenderLoop`'s catch
  // block sets `_useDefaultRenderLoop = false` and never reschedules
  // `requestAnimationFrame`), the render loop dies *permanently* -- no
  // auto-recovery, just Cesium's own default "Rendering has stopped"
  // error panel covering the screen until a full page reload. Not tied to
  // headless/software GPU rendering (reproduced identically with GPU
  // acceleration) or a cold Vite dev-server start (reproduced neither
  // more nor less on a deliberately cold server) -- just an occasional
  // race. Fixed by not mounting <Viewer> at all until a ResizeObserver
  // confirms its container has nonzero dimensions, regardless of *why* it
  // might not yet (CSS timing, slow layout, anything) -- see
  // containerRef/containerReady below and the wrapping div in the return.
  const containerRef = useRef<HTMLDivElement>(null)
  const [containerReady, setContainerReady] = useState(false)
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver((entries) => {
      const { width, height } = entries[0].contentRect
      if (width > 0 && height > 0) {
        setContainerReady(true)
        observer.disconnect()
      }
    })
    observer.observe(container)
    return () => observer.disconnect()
  }, [])
  // Shared by the "recenter if nothing visible" safety net below and
  // handleSelectPlace's fly-to further down: computeViewRectangle() can
  // transiently read as "nothing visible" for a moment mid-flight during
  // ANY camera.flyTo, not just the Home button's own recovery flight it
  // was originally written to guard -- this was the cause of a bug where
  // a place-search fly-to would zoom in then immediately get overridden
  // back out to the full map view, because the postRender-driven
  // visibility check had no way to know a *different* flyTo was already
  // in deliberate progress. Set true immediately before any programmatic
  // camera.flyTo call, cleared via that flyTo's own complete/cancel
  // callback.
  const suppressRecenterRef = useRef(false)

  const terrainProvider = useMemo(() => new EllipsoidTerrainProvider(), [])

  const [basemap, setBasemap] = useState<ToggleableLayerState>({
    visible: true,
    opacity: 1,
  })
  // User-selectable basemap with multiple openly-accessible options --
  // see BasemapLayer.tsx. Defaults to Aerial imagery (was Natural Earth,
  // the original offline/no-network-request option).
  const [basemapStyle, setBasemapStyle] = useState<BasemapStyle>('aerial')
  const [coastline, setCoastline] = useState<ToggleableLayerState>({
    visible: true,
    opacity: 0.8,
  })
  // 0.81, not 1, by default.
  const [mosaicOpacity, setMosaicOpacity] = useState(0.81)
  // Defaults to `tir1`, not `mir`, on load.
  const [band, setBand] = useState<MosaicBand>('tir1')
  // xcube colormap/colorbar selection + user-customizable min/max -- one
  // shared palette/range across all 4 bands, same pattern as
  // mosaicOpacity above. `colorMin`/`colorMax` still match
  // MosaicLayer.tsx's previous hardcoded values. `palette` defaults to
  // grayscale (`Greys`) -- matplotlib's `Greys` (not `gray`/`grey`) maps
  // low value -> white, high value -> black, which is cold=white/
  // warm=black, the standard meteorological IR-imagery convention --
  // matches xcube's tile output after the 2026-08-15 switch from
  // THREDDS's `seq-Greys` (same convention, different name space -- see
  // lib/xcube.ts's fetchColorbars).
  const [colorbarGroups, setColorbarGroups] = useState<ColorbarGroup[]>([])
  const [palette, setPalette] = useState('Greys')
  const [colorMin, setColorMin] = useState(180)
  const [colorMax, setColorMax] = useState(330)
  // Off by default, overriding the earlier "all four visible by default"
  // default (an earlier pass already special-cased this one to start at
  // zero opacity for the same "don't show it unasked" reason; this goes
  // further). This isn't just a rendering no-op: `show={false}` on
  // ContributionMaskLayer's <ImageryLayer> stops Cesium from requesting
  // `satellite_id_<band>` WMS tiles at all -- a separate network cost
  // from the visible mosaic layer, paid on every timestamp change/
  // animation frame regardless of opacity, since alpha is a render-time
  // blend, not a fetch-gating condition. Opacity now defaults to a
  // visible value (not 0) since checking the box is the only remaining
  // step to see it. Opacity 0.07, not 0.6.
  const [contributionMask, setContributionMask] = useState<ToggleableLayerState>({
    visible: false,
    opacity: 0.07,
  })

  // Single shared "current timestamp" state -- the animation player, the
  // calendar/time picker, and the displayed layer all read/write
  // `timeIndex` (relative to the loaded window below), not independent
  // copies.
  //
  // `allTimestamps` is the full fetched list (the available range, wider
  // than what's loaded for animation -- see "Default animation range"
  // below); `windowStart` is where the loaded animation window begins
  // within it. `timeIndex` stays relative to the windowed slice so
  // TimeControls/AnimationDriver are unchanged by this -- CalendarControl
  // jumps by *absolute* index via `handleJumpToAbsoluteIndex`, which
  // extends `windowStart` backward if the jump target is older than the
  // current window -- calendar date change snaps to the nearest available
  // timestamp, bounded by the available range.
  const [allTimestamps, setAllTimestamps] = useState<string[]>([])
  const [windowStart, setWindowStart] = useState(0)
  const [timeIndex, setTimeIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [infoPanelOpen, setInfoPanelOpen] = useState(false)
  const [appInfoOpen, setAppInfoOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  // Point-probe: click a location
  // (while enabled) to see that pixel's band value + contributing
  // satellite(s) + their scan times, for the currently displayed band and
  // timestamp. `probeMarker` is the clicked Cartesian3 (drawn as a map pin
  // via <Entity>); `probeResult`/`probeLoading`/`probeError` back
  // PointProbePanel.tsx.
  //
  // Defaults to `true` (checked) on load, unconditionally -- not gated by
  // `isNarrow` or any other viewport check, same on desktop and mobile.
  // Just the initial value; the user can still uncheck it in the Layers
  // panel same as before.
  const [pointProbeMode, setPointProbeMode] = useState(true)
  const [probeMarker, setProbeMarker] = useState<Cartesian3 | null>(null)
  // The clicked lat/lon, kept separately from `probeResult` -- the open
  // panel re-probes this same location whenever the shared
  // current-timestamp changes for *any* reason (date picker, calendar
  // time-of-day list, animation playback, or the scrubber slider), not
  // just on a fresh click. Needs its own state (not just reading
  // `probeResult.lat/.lon`) since it has to exist *before* the first
  // fetch resolves too.
  const [probeLocation, setProbeLocation] = useState<{ lat: number; lon: number } | null>(
    null,
  )
  const [probeResult, setProbeResult] = useState<PointProbeResult | null>(null)
  const [probeLoading, setProbeLoading] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)
  // Timeseries chart, using Plotly.js for interactive
  // timeseries plots -- extends point-probe (see PointProbePanel.tsx's
  // "View timeseries" button): re-probes the same lat/lon across every
  // timestamp in the currently-loaded animation window (`timestamps`,
  // same default-range rule as the map animation, and it grows for free if the
  // user extends the animation window via the calendar, no separate
  // "extend range" control needed for this feature specifically).
  const [timeseriesOpen, setTimeseriesOpen] = useState(false)
  const [timeseriesData, setTimeseriesData] = useState<TimeseriesResponse | null>(null)
  const [timeseriesLoading, setTimeseriesLoading] = useState(false)
  const [timeseriesError, setTimeseriesError] = useState<string | null>(null)
  // Measurement tools -- measure distance and
  // calculate area: `measurePoints` accumulates one Cartesian3 per
  // click while `measureMode` isn't 'off'; MeasurePanel.tsx does the
  // actual distance/area math (lib/geometry.ts) off this same array.
  // Mutually exclusive with point-probe (both bind LEFT_CLICK to
  // different meanings) -- enforced in the two mode setters below, not
  // just left as a UI suggestion.
  const [measureMode, setMeasureMode] = useState<MeasureMode>('off')
  const [measurePoints, setMeasurePoints] = useState<Cartesian3[]>([])
  // Device location marker (tick mark) -- standard browser Geolocation API, no backend involvement.
  const [deviceLocationEnabled, setDeviceLocationEnabled] = useState(false)
  const [devicePosition, setDevicePosition] = useState<Cartesian3 | null>(null)
  const [deviceLocationError, setDeviceLocationError] = useState<string | null>(null)
  // Graticule (lat/lon grid) toggle.
  const [graticuleVisible, setGraticuleVisible] = useState(false)
  // GMT/UTC vs. local time toggle -- applies everywhere at once. One shared toggle
  // state threaded to every timestamp display in the app, not a separate
  // one per widget. Defaults to UTC -- same "off/default unless the user
  // asks otherwise" philosophy as this app's other global toggles (sync,
  // graticule, contribution mask), and UTC is the raw value actually
  // stored in the files.
  const [timeDisplay, setTimeDisplay] = useState<TimeDisplayMode>('utc')
  // Live sync + "show always recent" toggles. Both off by default (same
  // philosophy as every other global toggle here). Sync re-fetches the
  // authoritative timestamps list every 5 minutes and merges it in,
  // extending bounds/the animation's live edge, but (as of the 2026-08-10
  // design pass) never moves what the user is currently looking at.
  // "Show always recent" is the one that also jumps the current selection
  // to the newest timestamp on each refresh -- see the interval effect
  // below (after bandRef/refreshColorRange are defined) for both.
  const [syncEnabled, setSyncEnabled] = useState(false)
  const [showAlwaysRecent, setShowAlwaysRecent] = useState(false)
  // Cache-busting token for WMS GetMap tile URLs -- tied to sync/show-always-
  // recent toggle state rather than a per-timestamp mutability flag.
  // Bumped once per successful poll (see the interval effect below); only
  // actually threaded into MosaicLayer/ContributionMaskLayer while one of
  // the two toggles is on (see `activeCacheBust` near the JSX below) --
  // once both are off, normal caching resumes untouched.
  const [cacheBustToken, setCacheBustToken] = useState(0)
  // Speed control ceiling for the animation player
  // controls. Actual pacing is gated by AnimationDriver (see that file
  // for why) -- this is a maximum rate, not a fixed one: a WMS tile at
  // realistic size loads in ~0.85s regardless of how old the timestamp is
  // (measured directly via curl, not a caching effect), so at most of
  // these speeds AnimationDriver will typically be limited by intervalMs,
  // not by load time -- except 4x (250ms), which can outrun a ~0.85s load
  // and will fall back to the load-limited pace instead of free-running
  // or overlapping requests.
  const [intervalMs, setIntervalMs] = useState(1000)

  useEffect(() => {
    let cancelled = false
    fetchAvailableTimestamps().then((all) => {
      if (cancelled) return
      // Default range: last 96 timestamps, extendable -- now
      // extendable via CalendarControl jumping to an older date, see
      // `handleJumpToAbsoluteIndex` below.
      const start = Math.max(0, all.length - 96)
      setAllTimestamps(all)
      setWindowStart(start)
      setTimeIndex(all.length - 1 - start)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    fetchColorbars().then((groups) => {
      if (!cancelled) setColorbarGroups(groups)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const timestamps = useMemo(
    () => allTimestamps.slice(windowStart),
    [allTimestamps, windowStart],
  )

  const currentTime = timestamps[timeIndex]

  // What MosaicLayer/ContributionMaskLayer actually render -- deliberately
  // separate from `band`/`currentTime` above (which drive every other
  // display: scrubber position, date panel, contributing-satellites label,
  // point-probe, etc., and stay instant). Bug found 2026-08-15: `band`/
  // `time` are baked into the xcube tile URL alongside `colorMin`/
  // `colorMax`, so when a timestamp/band change fires `refreshColorRange`
  // (below) as a *separate* async follow-up, the imagery layer recreated
  // twice -- once immediately with the new band/time but the OLD color
  // range, then again moments later when the corrected range arrived --
  // fetching every visible tile twice (two full waves of identical tile
  // coordinates in the network tab, differing only in `vmin`/`vmax`).
  // `tileBand`/`tileTime` are updated in exactly one place together with
  // `colorMin`/`colorMax` (refreshColorRange's `.then()` below) for every
  // path that refreshes the color range, so the two arrive in a single
  // MosaicLayer re-render/tile-fetch instead of two. Starts at
  // `undefined`, not `currentTime` -- MosaicLayer/ContributionMaskLayer
  // already render nothing for an undefined `time`, so this also removes
  // the old wrong-default-range flash on initial load (previously: tiles
  // rendered once with the 180/330 fallback, then again with the real
  // range once it arrived).
  const [tileBand, setTileBand] = useState<MosaicBand>(band)
  const [tileTime, setTileTime] = useState<string | undefined>(undefined)

  const handleAdvance = useCallback(() => {
    setTimeIndex((previous) => (previous + 1) % timestamps.length)
  }, [timestamps.length])

  // Animation playback never calls refreshColorRange (see its own comment
  // below for why -- keeping a stable color scale across frames is the
  // point), so there's no corrected range for tileTime to wait for here:
  // sync it straight to currentTime on every tick, same as the old
  // behavior before tileBand/tileTime existed.
  useEffect(() => {
    if (!playing) return
    setTileTime(currentTime)
  }, [playing, currentTime])

  const [resettingColorRange, setResettingColorRange] = useState(false)
  // Live min/max for a given band+timestamp (fetchMinMax), not a fixed
  // constant -- shared by the reset button, band switches, and manual
  // timestamp changes below. See lib/metadataApi.ts's fetchMinMax for why
  // this hits geosat-mosaic-api, not THREDDS, as of 2026-08-15.
  //
  // Deliberately NOT re-run on every timestamp change -- band switches and
  // manual timestamp changes (scrubber drag, calendar jump) call this
  // explicitly, but `handleAdvance` above (animation playback) does not:
  // calling it on every animation frame (as fast as every 250ms at 4x)
  // would spam the server, lag behind frame advance, and make the color
  // scale visibly shift frame-to-frame -- the opposite of what a stable
  // animated comparison needs. Playback keeps whatever range was already
  // showing.
  const refreshColorRange = useCallback((forBand: MosaicBand, forTime: string | undefined) => {
    if (!forTime) return
    setResettingColorRange(true)
    fetchMinMax(forBand, forTime)
      .then(({ min, max }) => {
        setColorMin(Math.round(min * 10) / 10)
        setColorMax(Math.round(max * 10) / 10)
        // Applied together with the color range, not before it -- see
        // tileBand/tileTime's own comment above for why.
        setTileBand(forBand)
        setTileTime(forTime)
      })
      .catch(() => {
        // Failure mode (2026-08-15): tileBand/tileTime are the only thing
        // that lets MosaicLayer/ContributionMaskLayer render at all -- if
        // this fetch never resolves successfully (a transient 503, a
        // network blip), tiles would otherwise stay blocked forever with
        // no automatic recovery, even though tile serving itself (xcube)
        // is completely unaffected by a metadata-API hiccup. Apply the
        // band/time change anyway, keeping whatever colorMin/colorMax was
        // already showing instead of the fetched range -- same "keep
        // showing what we had" fallback animation playback already uses
        // when it skips this fetch entirely.
        setTileBand(forBand)
        setTileTime(forTime)
      })
      .finally(() => setResettingColorRange(false))
  }, [])

  // Jumps to an absolute index into `allTimestamps` (from CalendarControl),
  // as opposed to `setTimeIndex`'s window-relative index (from
  // TimeControls' scrubber). Extends the loaded window backward if the
  // target predates it -- see the `allTimestamps`/`windowStart` comment
  // above. A manual timestamp change, so refreshes the color range too.
  const handleJumpToAbsoluteIndex = useCallback(
    (absoluteIndex: number) => {
      setWindowStart((previousStart) => {
        const nextStart = Math.min(previousStart, absoluteIndex)
        setTimeIndex(absoluteIndex - nextStart)
        return nextStart
      })
      refreshColorRange(band, allTimestamps[absoluteIndex])
    },
    [band, allTimestamps, refreshColorRange],
  )

  // Scrubber drag (TimeControls), the other manual-timestamp-change path
  // besides CalendarControl above -- also refreshes the color range.
  const handleManualIndexChange = useCallback(
    (nextIndex: number) => {
      setTimeIndex(nextIndex)
      refreshColorRange(band, timestamps[nextIndex])
    },
    [band, timestamps, refreshColorRange],
  )

  const handleBandChange = useCallback(
    (nextBand: MosaicBand) => {
      setBand(nextBand)
      refreshColorRange(nextBand, currentTime)
    },
    [currentTime, refreshColorRange],
  )

  // Reset-to-auto-range button next to the colorbar.
  const handleResetColorRange = useCallback(() => {
    refreshColorRange(band, currentTime)
  }, [band, currentTime, refreshColorRange])

  // Populates the color range with the band+timestamp min/max on first
  // load too, instead of leaving the fixed 180/330 fallback showing until
  // the user does something -- runs once, as soon as the timestamp list
  // finishes loading and a `currentTime` exists (guarded by the ref so it
  // doesn't refire on every later timestamp change, including animation
  // ticks).
  const initialColorRangeFetched = useRef(false)
  useEffect(() => {
    if (initialColorRangeFetched.current || !currentTime) return
    initialColorRangeFetched.current = true
    refreshColorRange(band, currentTime)
  }, [currentTime, band, refreshColorRange])

  // Home button + max zoom-out cap + recenter safety net. Pieces, all
  // sharing the same per-mode "full globe/map" destination:
  //
  // 1. Home: cancels Cesium's own default and flies to lon=0/lat=0 at
  //    THREE_D_MAX_HEIGHT (3D) or fullMapHeight(mode) (2D/Columbus) with
  //    a nadir orientation -- the whole globe/map, centered.
  // 2. Max zoom-out cap: `screenSpaceCameraController.maximumZoomDistance`
  //    set to that same height, updated on `scene.morphComplete` since it
  //    differs per mode. Reliable and sufficient on its own for 3D and 2D
  //    (visible extent scales exactly linearly with height at nadir, and
  //    2D structurally can't leave nadir). Columbus View can drift off
  //    nadir during ordinary interactive use though (the user can
  //    free-tilt there, unlike 2D), so height alone doesn't reliably
  //    predict visible extent there (camera height stayed under the
  //    calibrated cap while the visible rectangle still hit a pole).
  //    Deliberately *not* patched with a second, reactive "clamp to the
  //    bound" check the way 3D/2D's cap alone already is -- tried exactly
  //    that (compare `computeViewRectangle()`'s north/south against
  //    +/-90 degrees every `postRender` tick, fly back to the full-map
  //    view on overshoot) and it can make the whole app appear to freeze:
  //    once most of the view is off-map in Columbus View,
  //    `computeViewRectangle()` itself can return a degenerate/wrong
  //    rectangle (a debug counter showed the correction re-firing dozens
  //    of times a second, even immediately after "successfully" flying
  //    back to the full-map view, which itself got flagged as still out
  //    of bounds), so the correction fights itself in an unwinnable loop
  //    and every drag input gets silently overridden by the
  //    perpetually-restarting flight. Columbus View's zoom-out cap is
  //    therefore best-effort via `maximumZoomDistance` only, same as if
  //    this were 2D -- imprecise under heavy tilt, but never gets stuck.
  //
  // Also folds in the already-shipped "recenter if nothing visible at
  // all" safety net (unrelated root cause -- e.g. a fresh page load can
  // start with the camera pointed at empty space before any interaction;
  // also the recovery path for Columbus View panning itself completely
  // off-map, per point 2 above) into the same reactive check, since both
  // ultimately call the same `triggerFullView`. This one *is* safe to
  // leave in as an ongoing check (not just first-render): unlike the
  // reactive bound-check that was removed, "nothing visible at all" is
  // unambiguous -- `computeViewRectangle()` returning strictly undefined,
  // not a merely-wrong-but-still-defined rectangle -- so it doesn't have
  // the same self-retriggering failure mode.
  useEffect(() => {
    let cancelled = false
    let cleanup: (() => void) | undefined

    const trySetup = () => {
      if (cancelled) return
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) {
        requestAnimationFrame(trySetup)
        return
      }

      // Black 3D globe on a physical Android phone (Qualcomm Adreno 660)
      // -- basemap/mosaic didn't render, CoastlineLayer's vector
      // polylines did, 2D/Columbus worked, no JS console error.
      // Root-caused via bisection on the actual affected phone (remote
      // debugging over adb/chrome://inspect, toggling Scene/globe
      // properties one at a time through a temporary
      // window.__cesiumViewer hook until the globe visibly came back):
      // the culprit is `globe.showGroundAtmosphere` -- a per-fragment
      // haze/scattering effect blended directly onto the globe surface
      // based on view angle. On this GPU it was apparently computing as
      // fully opaque black instead of a subtle tint, painting over an
      // otherwise-correctly-rendered globe underneath -- exactly matching
      // the symptom (globe surface obscured, vector overlays drawn in a
      // separate pass unaffected, no error since this is a silently-wrong
      // shader *result*, not an API failure). Two other candidates were
      // tested and ruled out first: `logarithmicDepthBuffer` (see below,
      // left disabled but confirmed not the fix) and forcing a WebGL1
      // context (reverted -- caused a different shader-compile failure on
      // this same GPU, see git history). Disabled globally, not
      // device-sniffed -- same reasoning as logarithmicDepthBuffer below:
      // no reliable way to enumerate every affected Adreno/driver
      // combination from JS, and this is a minor cosmetic effect (a
      // subtle limb haze), not worth the correctness risk across more
      // mobile GPUs.
      viewer.scene.globe.showGroundAtmosphere = false

      // Tried as an earlier candidate for the bug documented above -- on
      // the actual affected phone this did NOT fix it,
      // showGroundAtmosphere above was the actual cause. Left disabled
      // anyway: harmless either way, and this app's terrain is always a
      // flat EllipsoidTerrainProvider (no real elevation data), so the
      // feature's actual benefit (fewer frustum splits under high depth
      // complexity) was always minimal here.
      viewer.scene.logarithmicDepthBuffer = false

      // Default initial view on page load: lat/lon 0,0 centered, camera
      // untilted -- not Cesium's own raw default view, which is
      // off-center/oblique (the existing "recenter if nothing visible"
      // safety net further below only catches the camera pointing at
      // empty space, not merely an uncentered/tilted-but-still-showing-
      // something default). Same destination as the Home button/zoom-out
      // cap's "full globe" view (`GLOBAL_VIEW_DESTINATION`), set
      // instantly via `camera.setView` rather than animated via `flyTo`
      // -- this is the very first frame the user ever sees, not a
      // transition, so it should just already look right, not fly there.
      // No explicit `orientation` needed: Cesium's `setView` defaults
      // orientation to heading/pitch/roll = 0/-90/0 (straight down) for a
      // plain Cartesian3 destination, the same fact already relied on for
      // 3D's Home destination below.
      viewer.camera.setView({ destination: GLOBAL_VIEW_DESTINATION })

      // Cesium's Viewer binds its own default LEFT_DOUBLE_CLICK handler
      // (independent of selectionIndicator/infoBox above, and independent
      // of our own point-probe click handler below): double-clicking any
      // Entity flies the camera in close and tracks it. Surfaced by
      // double-clicking the point-probe marker -- the camera zoomed in
      // tight and got stuck following the marker, not just a leftover
      // selection-indicator visual. Removed once, up front, for any
      // entity (not just the probe marker -- CoastlineLayer renders
      // GeoJsonDataSource entities too), since this app always wants its
      // own custom picking behavior, never Cesium's default.
      viewer.screenSpaceEventHandler.removeInputAction(ScreenSpaceEventType.LEFT_DOUBLE_CLICK)

      const flyToFullView = () => {
        const mode = viewer.scene.mode
        suppressRecenterRef.current = true
        const stopSuppressing = () => {
          suppressRecenterRef.current = false
        }
        if (mode === SceneMode.SCENE3D) {
          viewer.camera.flyTo({
            destination: GLOBAL_VIEW_DESTINATION,
            complete: stopSuppressing,
            cancel: stopSuppressing,
          })
        } else {
          viewer.camera.flyTo({
            destination: Cartesian3.fromDegrees(0, 0, fullMapHeight(mode)),
            orientation: NADIR_ORIENTATION,
            complete: stopSuppressing,
            cancel: stopSuppressing,
          })
        }
      }

      // Guards against re-triggering while a just-started recovery flight
      // is still mid-transit (computeViewRectangle can still read as
      // "nothing visible" for a moment after the flight begins) --
      // generously longer than the flight duration.
      let recentering = false
      const triggerFullView = () => {
        if (recentering) return
        recentering = true
        flyToFullView()
        setTimeout(() => {
          recentering = false
        }, 3500)
      }

      const removeHomeListener = viewer.homeButton.viewModel.command.beforeExecute.addEventListener(
        (commandInfo) => {
          commandInfo.cancel = true
          triggerFullView()
        },
      )

      const controller = viewer.scene.screenSpaceCameraController
      const defaultMaximumZoomDistance = controller.maximumZoomDistance
      const updateMaxZoomDistance = () => {
        const mode = viewer.scene.mode
        controller.maximumZoomDistance =
          mode === SceneMode.SCENE3D ? THREE_D_MAX_HEIGHT : fullMapHeight(mode)
      }
      updateMaxZoomDistance()
      viewer.scene.morphComplete.addEventListener(updateMaxZoomDistance)

      const checkVisibility = () => {
        if (suppressRecenterRef.current) return
        // computeViewRectangle() doesn't just return undefined when it
        // can't tell what's visible -- it can actually *throw*
        // ("DeveloperError: cartesian has a NaN component") when called
        // while the camera is mid-scene-mode-morph (2D <-> Columbus View),
        // which crashed Cesium's entire render loop ("Rendering has
        // stopped") since this runs inside a postRender listener with
        // nothing upstream to catch it. Surfaced by switching scene mode
        // shortly after a place-search fly-to left the camera at an
        // off-center, zoomed-in position. Treat a throw the same as
        // "can't tell, don't recenter" rather than let it propagate.
        let rectangle
        try {
          rectangle = viewer.camera.computeViewRectangle()
        } catch {
          return
        }
        if (rectangle) return
        triggerFullView()
      }
      viewer.scene.postRender.addEventListener(checkVisibility)

      cleanup = () => {
        removeHomeListener()
        viewer.scene.morphComplete.removeEventListener(updateMaxZoomDistance)
        controller.maximumZoomDistance = defaultMaximumZoomDistance
        viewer.scene.postRender.removeEventListener(checkVisibility)
      }
    }
    trySetup()

    return () => {
      cancelled = true
      cleanup?.()
    }
  }, [])

  // Point-probe click handler. Attached/removed whenever `pointProbeMode`
  // toggles, not re-attached on every band/timestamp change (which would
  // include every animation frame) -- the click handler itself no longer
  // needs to read band/time at all (it only sets `probeLocation`; the
  // fetch effect below reads current band/time directly since *it*
  // deliberately re-runs on every band/time change). `bandRef` is still
  // used by the sync-poll effect further down.
  const bandRef = useRef(band)
  bandRef.current = band

  // Live sync / "show always recent" 5-minute poll. One interval, set up
  // once, gated by refs (not the effect's own deps) so toggling either
  // checkbox doesn't tear down and restart the timer -- same "stable
  // listener + refs for current values" pattern as the point-probe handler
  // above. Skips the fetch entirely when neither toggle is on, so this is
  // a no-op (no network traffic at all) for the common off-by-default case.
  const syncEnabledRef = useRef(syncEnabled)
  syncEnabledRef.current = syncEnabled
  const showAlwaysRecentRef = useRef(showAlwaysRecent)
  showAlwaysRecentRef.current = showAlwaysRecent
  const playingRef = useRef(playing)
  playingRef.current = playing

  useEffect(() => {
    const tick = () => {
      if (!syncEnabledRef.current && !showAlwaysRecentRef.current) return
      fetchAvailableTimestamps().then((newAll) => {
        setAllTimestamps(newAll)
        setCacheBustToken(Date.now())
        // "Show always recent" is the one that also follows live -- moves
        // the current selection to the newest timestamp. The animation
        // interlock keeps it paused (not just
        // visually disabled) while playing, so a poll landing mid-animation
        // has no effect on the current selection at all -- it just still
        // extended `allTimestamps`/bounds via the sync-shared refresh
        // above, same as sync alone would have.
        if (showAlwaysRecentRef.current && !playingRef.current) {
          const absoluteIndex = newAll.length - 1
          setWindowStart((previousStart) => {
            const nextStart = Math.min(previousStart, absoluteIndex)
            setTimeIndex(absoluteIndex - nextStart)
            return nextStart
          })
          refreshColorRange(bandRef.current, newAll[absoluteIndex])
        }
      })
    }
    const id = setInterval(tick, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [refreshColorRange])

  useEffect(() => {
    if (!pointProbeMode) return
    let cancelled = false
    let handler: ScreenSpaceEventHandler | undefined

    const trySetup = () => {
      if (cancelled) return
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) {
        requestAnimationFrame(trySetup)
        return
      }

      handler = new ScreenSpaceEventHandler(viewer.scene.canvas)
      handler.setInputAction((click: { position: Cartesian2 }) => {
        const cartesian = viewer.camera.pickEllipsoid(
          click.position,
          viewer.scene.globe.ellipsoid,
        )
        if (!cartesian) return
        const cartographic = Cartographic.fromCartesian(cartesian)
        const lat = CesiumMath.toDegrees(cartographic.latitude)
        const lon = CesiumMath.toDegrees(cartographic.longitude)

        // Clicks poleward of the data (COVERAGE_LAT_LIMIT, same bound
        // MosaicLayer's own COVERAGE_RECTANGLE already uses to stop tiles
        // rendering there) used to still fire a probe -- the backend
        // doesn't error for an out-of-range lat, it silently clamps to
        // the nearest valid pixel and returns real data *for that clamped
        // point*, which looked like genuine data for wherever was
        // actually clicked. Ignore the click entirely here instead,
        // matching what the tile layer already does visually.
        if (Math.abs(lat) > COVERAGE_LAT_LIMIT) return

        setProbeMarker(cartesian)
        setProbeResult(null)
        setProbeError(null)
        // A new probe click invalidates any timeseries chart from a
        // previous location -- close it rather than leaving a stale chart
        // up that no longer matches what's currently probed.
        setTimeseriesOpen(false)
        // Setting this (rather than fetching directly here) is what
        // actually triggers the fetch -- see the dedicated probe-fetch
        // effect below, which reacts to this changing exactly the same
        // way it reacts to the timestamp changing later.
        setProbeLocation({ lat, lon })
      }, ScreenSpaceEventType.LEFT_CLICK)
    }
    trySetup()

    return () => {
      cancelled = true
      handler?.destroy()
    }
  }, [pointProbeMode])

  // Re-probes `probeLocation` whenever the shared current-timestamp state
  // changes for *any* reason -- calendar date jump, the time-of-day
  // dropdown, animation playback, or the scrubber slider -- so an open
  // probe panel tracks "what's actually at this location right now"
  // instead of freezing at whatever was true when it was first clicked.
  // Also re-probes on a band change for the same reason (showing a WV
  // value while every other control has already moved on to MIR would be
  // actively misleading, not just stale). `probeLocation` itself changing
  // (a fresh click) is what makes this effect double as the *initial*
  // fetch too -- see the click handler above, which no longer fetches
  // directly.
  useEffect(() => {
    if (!pointProbeMode || !probeLocation || !currentTime) return
    let cancelled = false
    setProbeLoading(true)
    setProbeError(null)
    fetchPointProbe(band, currentTime, probeLocation.lat, probeLocation.lon)
      .then((result) => {
        if (!cancelled) setProbeResult(result)
      })
      .catch((err: unknown) => {
        if (!cancelled) setProbeError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setProbeLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [pointProbeMode, probeLocation, currentTime, band])

  // The exact time range behind whatever's currently shown/loading in
  // TimeseriesPanel -- kept so a band change while the panel is open (see
  // the effect below) can redo the identical request against the new band
  // instead of falling back to some new default range.
  const [timeseriesTimes, setTimeseriesTimes] = useState<string[] | null>(null)

  // Shared by handleViewTimeseries (currently-loaded window) and
  // handleLoadFullTimeseriesHistory (every available timestamp, see that
  // callback's own comment for why the two are separate entry points).
  // Sequential on the backend (see geosat-mosaic-api/main.py's own notes:
  // a threaded version was tried and found to be unsafe, up to and
  // including silently crashing the whole server process), so this can
  // take a while for a long range -- TimeseriesPanel shows a loading
  // state rather than assuming it's fast.
  const runTimeseriesFetch = useCallback(
    (times: string[]) => {
      if (!probeResult) return
      setTimeseriesTimes(times)
      setTimeseriesLoading(true)
      setTimeseriesError(null)
      fetchTimeseries(band, probeResult.lat, probeResult.lon, times)
        .then((result) => setTimeseriesData(result))
        .catch((err: unknown) =>
          setTimeseriesError(err instanceof Error ? err.message : String(err)),
        )
        .finally(() => setTimeseriesLoading(false))
    },
    [probeResult, band],
  )

  // TimeseriesPanel's title reads `band` live (so it updated immediately
  // on a band switch), but the plotted data was whatever was last fetched
  // -- so switching bands while the panel was open showed the NEW band's
  // name over the OLD band's chart, until the next point-probe click
  // happened to refresh it. Re-runs the same `timeseriesTimes` range
  // against the new band whenever it changes while the panel is open.
  // Deliberately keyed on `band` alone, not `runTimeseriesFetch`/
  // `timeseriesTimes` (both change for reasons unrelated to a band switch
  // too -- `runTimeseriesFetch`'s identity also changes whenever
  // `probeResult` does, which happens on every animation frame via the
  // always-live point-probe effect above -- including those would refetch
  // on every frame instead of only on an actual band change).
  useEffect(() => {
    if (timeseriesOpen && timeseriesTimes) {
      runTimeseriesFetch(timeseriesTimes)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [band])

  // Same category of bug as the band effect above, but for the calendar
  // date-jump: if the panel is open with the default windowed range loaded
  // (see timeseriesConfig.ts) and the user jumps to an OLDER calendar date
  // outside it, the chart used to just keep showing the old window,
  // excluding the very date just navigated to. Extends the loaded range
  // backward to include it -- same one-way "extend, never shrink" pattern
  // already used for the map's OWN animation window (`windowStart` state
  // above, a different thing despite the similar name -- this effect only
  // touches `timeseriesTimes`). `timeseriesTimes` always ends at the
  // newest available timestamp in every code path that sets it (the
  // default window, "load full history", and this extension itself all
  // anchor there), so "does `currentTime` need including" reduces to "is
  // its index older than the window's current start index" -- cheap to
  // check on every render this effect fires, and does nothing once
  // already showing full history (the comparison can never trigger once
  // the window already starts at index 0). Deliberately keyed on
  // `currentTime` alone, same rationale as the band effect's own comment
  // on why `runTimeseriesFetch`/`timeseriesTimes`/`allTimestamps` are
  // excluded -- this still runs on every animation frame (currentTime
  // changes every tick), but the body is a cheap index comparison that
  // only actually calls `runTimeseriesFetch` on a genuine backward jump,
  // which normal forward-only playback never produces.
  useEffect(() => {
    if (!timeseriesOpen || !timeseriesTimes || !currentTime) return
    const currentIndex = allTimestamps.indexOf(currentTime)
    const timeseriesStartIndex = allTimestamps.indexOf(timeseriesTimes[0])
    if (currentIndex !== -1 && timeseriesStartIndex !== -1 && currentIndex < timeseriesStartIndex) {
      runTimeseriesFetch(allTimestamps.slice(currentIndex))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentTime])

  // "View timeseries" from PointProbePanel -- re-probes the same lat/lon
  // as the last point-probe click. Deliberately keyed off `allTimestamps`
  // + timeseriesConfig.ts, NOT off `timestamps` (the map's own animation
  // window, which defaults to the last 96 and only grows one-way as the
  // user jumps to older calendar dates) -- reusing that window used to
  // make the timeseries default silently depend on prior calendar
  // navigation (a "partial-window confusion" problem), which is confusing on its
  // own even though it was documented. This way the default is always the
  // same fixed size/mode, independent of anything else you've clicked
  // around in first. handleLoadFullTimeseriesHistory below is still the
  // explicit opt-in for the complete range regardless of this default.
  const handleViewTimeseries = useCallback(() => {
    if (!probeResult) return
    setTimeseriesOpen(true)
    setTimeseriesData(null)
    const defaultTimes =
      TIMESERIES_DEFAULT_MODE === 'full'
        ? allTimestamps
        : allTimestamps.slice(Math.max(0, allTimestamps.length - TIMESERIES_DEFAULT_WINDOW_SIZE))
    runTimeseriesFetch(defaultTimes)
  }, [probeResult, allTimestamps, runTimeseriesFetch])

  // Explicit "load full history" action from TimeseriesPanel -- re-fetches
  // across `allTimestamps` (the complete available range) rather than just
  // the currently-loaded animation window. Kept as a separate opt-in
  // rather than the default because it can be meaningfully slower than the
  // default 96-timestamp window (same sequential-backend cost as above,
  // just scaled to however many files actually exist).
  const handleLoadFullTimeseriesHistory = useCallback(() => {
    runTimeseriesFetch(allTimestamps)
  }, [allTimestamps, runTimeseriesFetch])

  // Measurement tools click handler -- adds one point per click while
  // `measureMode` isn't 'off'. Same attach/remove-on-mode-toggle pattern
  // as the point-probe handler above; unlike that one there's no backend
  // call to make per click, just accumulating points for
  // lib/geometry.ts's distance/area math to run over (done in
  // MeasurePanel.tsx, recomputed from the full array on every render --
  // cheap, these are small point counts, no need to memoize/incrementally
  // update).
  useEffect(() => {
    if (measureMode === 'off') return
    let cancelled = false
    let handler: ScreenSpaceEventHandler | undefined

    const trySetup = () => {
      if (cancelled) return
      const viewer = viewerRef.current?.cesiumElement
      if (!viewer) {
        requestAnimationFrame(trySetup)
        return
      }

      handler = new ScreenSpaceEventHandler(viewer.scene.canvas)
      handler.setInputAction((click: { position: Cartesian2 }) => {
        const cartesian = viewer.camera.pickEllipsoid(
          click.position,
          viewer.scene.globe.ellipsoid,
        )
        if (!cartesian) return
        setMeasurePoints((previous) => [...previous, cartesian])
      }, ScreenSpaceEventType.LEFT_CLICK)
    }
    trySetup()

    return () => {
      cancelled = true
      handler?.destroy()
    }
  }, [measureMode])

  // Device location marker (tick mark). `watchPosition` (not a one-shot `getCurrentPosition`) so the
  // marker actually tracks the user if they move, matching "tick mark"
  // showing where they *are*, not just where they were when they enabled
  // it; cheap to keep running since it's just a callback, not polling.
  useEffect(() => {
    if (!deviceLocationEnabled) return
    if (!navigator.geolocation) {
      setDeviceLocationError('Geolocation is not available in this browser')
      return
    }
    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        setDeviceLocationError(null)
        setDevicePosition(
          Cartesian3.fromDegrees(position.coords.longitude, position.coords.latitude),
        )
      },
      (error) => {
        setDeviceLocationError(error.message)
      },
    )
    return () => {
      navigator.geolocation.clearWatch(watchId)
    }
  }, [deviceLocationEnabled])

  // Place-search fly-to -- Nominatim place search
  // with zoom/fly-to-location, subject to the ±66° 2D-mode restriction:
  // the restriction applies only in the two 2D modes, not the 3D globe
  // (same asymmetry as the Home/zoom-cap effect above). Returns a warning
  // string (displayed by PlaceSearch itself) instead of flying when a
  // result falls outside the data extent in 2D/Columbus View.
  const handleSelectPlace = useCallback((result: PlaceSearchResult): string | null => {
    const viewer = viewerRef.current?.cesiumElement
    if (!viewer) return null

    const mode = viewer.scene.mode
    if (mode !== SceneMode.SCENE3D && Math.abs(result.lat) > COVERAGE_LAT_LIMIT) {
      return `"${result.displayName}" is outside this project's data extent (±${COVERAGE_LAT_LIMIT}°) and can't be shown in 2D mode. Switch to 3D globe view to visit it.`
    }

    const destination = Cartesian3.fromDegrees(result.lon, result.lat, PLACE_SEARCH_HEIGHT)
    // Suppress the Home/zoom-cap effect's "recenter if nothing visible"
    // check for the duration of this flight -- computeViewRectangle() can
    // transiently read as empty mid-flight, which without this would
    // immediately override this fly-to back out to the full map view
    // (search would zoom in then auto-zoom back out). See
    // suppressRecenterRef's own comment above.
    suppressRecenterRef.current = true
    const stopSuppressing = () => {
      suppressRecenterRef.current = false
    }
    if (mode === SceneMode.SCENE3D) {
      viewer.camera.flyTo({ destination, complete: stopSuppressing, cancel: stopSuppressing })
    } else {
      viewer.camera.flyTo({
        destination,
        orientation: NADIR_ORIENTATION,
        complete: stopSuppressing,
        cancel: stopSuppressing,
      })
    }
    return null
  }, [])

  // Only threaded into the tile layers while sync/show-always-recent is
  // actually on -- `undefined` here means "add no extra param," so normal
  // caching resumes the moment both toggles are off. See the state
  // declaration above and the interval effect for how this actually gets
  // a new value.
  const activeCacheBust = syncEnabled || showAlwaysRecent ? cacheBustToken : undefined

  return (
    <div ref={containerRef} style={{ position: 'fixed', inset: 0 }}>
      {containerReady && (
      <Viewer
        ref={viewerRef}
        full
        // No default Ion/Bing base layer -- BasemapLayer.tsx (a plain
        // child <ImageryLayer index={0}>, not this read-only prop) drives
        // the actual basemap now, precisely so switching basemaps only
        // recreates that one layer instead of the whole Viewer.
        baseLayer={false}
        baseLayerPicker={false}
        geocoder={false}
        terrainProvider={terrainProvider}
        animation={false}
        timeline={false}
        // Replaced with HelpPanel.tsx (app-specific usage/features/
        // purpose/limitation content) -- see helpButtonStyle's comment.
        navigationHelpButton={false}
        // Cesium's own default entity-picking UI (a green bracket
        // "selection indicator", plus an infoBox popup and double-click
        // fly-to-and-track) fires independently of our own click handler
        // whenever a click/double-click lands on any Entity -- including
        // the point-probe marker below, which is exactly what surfaced
        // this: double-clicking to probe showed Cesium's own selection
        // indicator on top of PointProbePanel's own UI. This app always
        // drives its own custom panels off picked coordinates, never
        // wants Cesium's built-in entity-selection chrome.
        selectionIndicator={false}
        infoBox={false}
      >
        <BasemapLayer style={basemapStyle} alpha={basemap.opacity} show={basemap.visible} />
        <MosaicLayer
          band={tileBand}
          time={tileTime}
          alpha={mosaicOpacity}
          palette={palette}
          colorMin={colorMin}
          colorMax={colorMax}
          cacheBust={activeCacheBust}
        />
        <ContributionMaskLayer
          band={tileBand}
          time={tileTime}
          show={contributionMask.visible}
          alpha={contributionMask.opacity}
          cacheBust={activeCacheBust}
        />
        <CoastlineLayer show={coastline.visible} opacity={coastline.opacity} />
        <Graticule show={graticuleVisible} />
        <AnimationDriver
          playing={playing && timestamps.length > 0}
          intervalMs={intervalMs}
          onAdvance={handleAdvance}
        />
        {probeMarker && (
          <Entity
            position={probeMarker}
            point={{ pixelSize: 10, color: Color.YELLOW, outlineColor: Color.BLACK, outlineWidth: 2 }}
          />
        )}
        {measurePoints.map((point, index) => (
          <Entity
            key={index}
            position={point}
            point={{ pixelSize: 8, color: Color.CYAN, outlineColor: Color.BLACK, outlineWidth: 1 }}
          />
        ))}
        {measureMode === 'distance' && measurePoints.length >= 2 && (
          <Entity polyline={{ positions: measurePoints, width: 3, material: Color.CYAN }} />
        )}
        {measureMode === 'area' && measurePoints.length >= 3 && (
          <Entity
            polygon={{
              hierarchy: new PolygonHierarchy(measurePoints),
              material: Color.CYAN.withAlpha(0.3),
              outline: true,
              outlineColor: Color.CYAN,
              // Cesium warns "Entity geometry outlines are unsupported on
              // terrain" and silently drops the outline otherwise, since a
              // polygon with no explicit height defaults to
              // terrain-clamped. height: 0 (this app's terrain is a plain
              // EllipsoidTerrainProvider anyway, with no elevation data to
              // clamp to) opts out of clamping and makes the outline
              // actually render.
              height: 0,
            }}
          />
        )}
        {devicePosition && (
          <Entity
            position={devicePosition}
            point={{
              pixelSize: 12,
              color: Color.DODGERBLUE,
              outlineColor: Color.WHITE,
              outlineWidth: 2,
            }}
          />
        )}
      </Viewer>
      )}
      <LayerSwitcher
        basemap={basemap}
        onBasemapChange={setBasemap}
        basemapStyle={basemapStyle}
        onBasemapStyleChange={setBasemapStyle}
        coastline={coastline}
        onCoastlineChange={setCoastline}
        mosaicOpacity={mosaicOpacity}
        onMosaicOpacityChange={setMosaicOpacity}
        band={band}
        onBandChange={handleBandChange}
        onInfoClick={() => setInfoPanelOpen(true)}
        colorbarGroups={colorbarGroups}
        palette={palette}
        onPaletteChange={setPalette}
        currentTime={currentTime}
        colorMin={colorMin}
        onColorMinChange={setColorMin}
        colorMax={colorMax}
        onColorMaxChange={setColorMax}
        onResetColorRange={handleResetColorRange}
        resettingColorRange={resettingColorRange}
        contributionMask={contributionMask}
        onContributionMaskChange={setContributionMask}
        pointProbeMode={pointProbeMode}
        onPointProbeModeChange={(next) => {
          setPointProbeMode(next)
          if (next) {
            setMeasureMode('off')
            setMeasurePoints([])
          }
          if (!next) {
            // Turning the mode off also clears any stale result/marker --
            // leaving them would show an old probe as if it were still
            // live once probing is re-enabled elsewhere on the map.
            // `probeLocation` too -- otherwise the re-probe effect above
            // would keep silently re-fetching in the background on every
            // timestamp change even with probing off and nothing shown.
            // `probeLoading` too -- see PointProbePanel's own onClose just
            // below for the stuck-loading bug this also fixes.
            setProbeMarker(null)
            setProbeLocation(null)
            setProbeResult(null)
            setProbeError(null)
            setProbeLoading(false)
          }
        }}
        measureMode={measureMode}
        onMeasureModeChange={(next) => {
          setMeasureMode(next)
          setMeasurePoints([])
          if (next !== 'off') {
            setPointProbeMode(false)
            setProbeMarker(null)
            setProbeLocation(null)
            setProbeResult(null)
            setProbeError(null)
            setProbeLoading(false)
          }
        }}
        deviceLocationEnabled={deviceLocationEnabled}
        onDeviceLocationEnabledChange={(next) => {
          setDeviceLocationEnabled(next)
          if (!next) {
            setDevicePosition(null)
            setDeviceLocationError(null)
          }
        }}
        deviceLocationError={deviceLocationError}
        graticuleVisible={graticuleVisible}
        onGraticuleVisibleChange={setGraticuleVisible}
        timeDisplay={timeDisplay}
        onTimeDisplayChange={setTimeDisplay}
        syncEnabled={syncEnabled}
        onSyncEnabledChange={setSyncEnabled}
        showAlwaysRecent={showAlwaysRecent}
        onShowAlwaysRecentChange={setShowAlwaysRecent}
        // Layer/control interlocks: band switching disables while the
        // animation is playing. "Show always recent" is in that same interlock
        // group (see its own state comment above).
        bandDisabled={playing}
        showAlwaysRecentDisabled={playing}
        // On a narrow phone screen, Time, Date, and Search are rendered
        // inline inside this panel instead of standing alone below (see
        // the `!isNarrow &&` guard on `rightColumnStyle` right below) --
        // same props TimeControls/CalendarControl/PlaceSearch take
        // standalone, just handed to LayerSwitcher instead so it can
        // embed them.
        timestamps={timestamps}
        timeIndex={timeIndex}
        onIndexChange={handleManualIndexChange}
        playing={playing}
        onPlayingChange={setPlaying}
        intervalMs={intervalMs}
        onIntervalMsChange={setIntervalMs}
        allTimestamps={allTimestamps}
        onJump={handleJumpToAbsoluteIndex}
        dateControlsDisabled={playing}
        onSelectPlace={handleSelectPlace}
      />
      {!isNarrow && (
        <div style={rightColumnStyle}>
          <TimeControls
            timestamps={timestamps}
            index={timeIndex}
            onIndexChange={handleManualIndexChange}
            playing={playing}
            onPlayingChange={setPlaying}
            intervalMs={intervalMs}
            onIntervalMsChange={setIntervalMs}
            timeDisplay={timeDisplay}
          />
          <CalendarControl
            allTimestamps={allTimestamps}
            currentTime={currentTime}
            onJump={handleJumpToAbsoluteIndex}
            // Layer/control interlocks: date/timestamp controls disable while
            // the animation is playing, same rule as `bandDisabled` above.
            disabled={playing}
            timeDisplay={timeDisplay}
          />
          <PlaceSearch onSelect={handleSelectPlace} />
        </div>
      )}
      <InfoPanel
        open={infoPanelOpen}
        onClose={() => setInfoPanelOpen(false)}
        band={band}
        time={currentTime}
        playing={playing}
        timeDisplay={timeDisplay}
      />
      <ScanTimeLabel band={band} time={currentTime} timeDisplay={timeDisplay} />
      <button style={appInfoButtonStyle} onClick={() => setAppInfoOpen(true)}>
        About
      </button>
      <AppInfoPanel open={appInfoOpen} onClose={() => setAppInfoOpen(false)} />
      <button style={helpButtonStyle(isNarrow)} onClick={() => setHelpOpen(true)}>
        Help
      </button>
      <HelpPanel open={helpOpen} onClose={() => setHelpOpen(false)} />
      {pointProbeMode && (
        <PointProbePanel
          loading={probeLoading}
          error={probeError}
          result={probeResult}
          onClose={() => {
            // `probeLocation` too -- see onPointProbeModeChange's own
            // comment above for why (stops the background re-probe
            // effect from continuing after the panel is explicitly
            // closed, not just hiding its output).
            //
            // `probeLoading` too -- bug found on mobile: once
            // `probeLocation` goes null here, the probe-fetch effect's
            // guard clause (`if (!pointProbeMode || !probeLocation ||
            // !currentTime) return`) returns early on its next run
            // *without* ever setting `probeLoading` back to
            // false -- and the in-flight request's own `.finally()`
            // (from the *previous* effect run, now cancelled) also skips
            // it, gated behind that same effect's `cancelled` flag. If
            // the user closes the panel while a fetch is still in
            // flight, `probeLoading` was left stuck `true` forever with
            // nothing left to ever flip it back -- and since
            // PointProbePanel's own null-check is `if (!loading &&
            // !error && !result) return null`, a stuck-`true` loading
            // flag means the panel can never actually unmount, no matter
            // how many times the user taps × or the backdrop. Explicitly
            // resetting it here (and in onPointProbeModeChange's
            // pointProbeMode-off branch, and mode-switching-to-measure
            // below, both of which clear the same location/result state)
            // is the actual fix.
            setProbeMarker(null)
            setProbeLocation(null)
            setProbeResult(null)
            setProbeError(null)
            setProbeLoading(false)
          }}
          onViewTimeseries={
            probeResult?.value !== null && probeResult?.primary ? handleViewTimeseries : undefined
          }
          timeDisplay={timeDisplay}
        />
      )}
      {measureMode !== 'off' && (
        <MeasurePanel
          mode={measureMode}
          points={measurePoints}
          onClear={() => setMeasurePoints([])}
        />
      )}
      {timeseriesOpen && (
        <Suspense fallback={null}>
          <TimeseriesPanel
            open={timeseriesOpen}
            onClose={() => setTimeseriesOpen(false)}
            loading={timeseriesLoading}
            error={timeseriesError}
            data={timeseriesData}
            band={band}
            timeDisplay={timeDisplay}
            totalAvailable={allTimestamps.length}
            onLoadFullHistory={handleLoadFullTimeseriesHistory}
          />
        </Suspense>
      )}
    </div>
  )
}

export default Globe
