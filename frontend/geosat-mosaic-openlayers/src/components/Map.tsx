import { useCallback, useEffect, useLayoutEffect, useRef, useState, lazy, Suspense } from 'react'
import type { CSSProperties } from 'react'
import OlMap from 'ol/Map'
import View from 'ol/View'
import TileLayer from 'ol/layer/Tile'
import VectorLayer from 'ol/layer/Vector'
import XYZ from 'ol/source/XYZ'
import VectorSource from 'ol/source/Vector'
import GeoJSON from 'ol/format/GeoJSON'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import LineString from 'ol/geom/LineString'
import Polygon from 'ol/geom/Polygon'
import Style from 'ol/style/Style'
import Stroke from 'ol/style/Stroke'
import CircleStyle from 'ol/style/Circle'
import Fill from 'ol/style/Fill'
import GraticuleLayer from 'ol/layer/Graticule'
import { defaults as defaultControls, Zoom, Rotate } from 'ol/control'
import type { MapBrowserEvent } from 'ol'
import LayerSwitcher, { type ToggleableLayerState } from './LayerSwitcher'
import TimeControls from './TimeControls'
import CalendarControl from './CalendarControl'
import PointProbePanel from './PointProbePanel'
import MeasurePanel from './MeasurePanel'
import PlaceSearch from './PlaceSearch'
import InfoPanel from './InfoPanel'
import AppInfoPanel from './AppInfoPanel'
import HelpPanel from './HelpPanel'
import ScanTimeLabel from './ScanTimeLabel'
// Lazy-loaded, same reasoning as geosat-mosaic-cesium's own Globe.tsx --
// Plotly.js is a large (~4.9MB unminified) bundle contribution, not
// worth paying for on every page load when most sessions may never
// open a timeseries chart.
const TimeseriesPanel = lazy(() => import('./TimeseriesPanel'))
import { COVERAGE_EXTENT, WORLD_EXTENT } from '../lib/coverage'
import { xcubeGeographicTileGrid } from '../lib/xcubeTileGrid'
import { xcubeDatasetId, xcubeTileUrlTemplate, fetchColorbars, type ColorbarGroup } from '../lib/xcube'
import { fetchAvailableTimestamps } from '../lib/timestamps'
import { createBasemapSource, type BasemapStyle } from '../lib/basemap'
import type { MosaicBand } from '../lib/mosaic'
import { getPaletteEdgeColors } from '../lib/paletteEdgeColors'
import { createTransparentEdgesTileLoader } from '../lib/transparentEdgesTileLoader'
import { useAnimationDriver } from '../lib/useAnimationDriver'
import {
  fetchMinMax,
  fetchPointProbe,
  fetchTimeseries,
  type PointProbeResult,
  type TimeseriesResponse,
} from '../lib/metadataApi'
import { type LonLat, type MeasureMode } from '../lib/geometry'
import { TIMESERIES_DEFAULT_MODE, TIMESERIES_DEFAULT_WINDOW_SIZE } from '../lib/timeseriesConfig'
import type { PlaceSearchResult } from '../lib/geocode'
import type { TimeDisplayMode } from '../lib/time'
import { useIsNarrowViewport } from '../lib/useIsNarrowViewport'

const containerStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
}

const rightColumnStyle: CSSProperties = {
  position: 'fixed',
  // Clears BOTH top-right rows now (About/Help, then the zoom +/- row
  // below it -- see `aboutHelpRowStyle`/`zoomRowStyle`'s own comments).
  // Originally just `top: 52` to clear a single About/Help row: with both
  // at `top: 12`, this column's own TimeControls panel painted directly
  // over the Help button and visually fused with the About button's text
  // into its own header row, so a click meant for "About" collapsed the
  // Time panel instead. Pushed further down to `88` once the zoom row was
  // added below it too.
  top: 88,
  right: 12,
  zIndex: 1,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
  // `min()`, not a plain `200px` -- on a narrow phone, this column and
  // LayerSwitcher (left column, same `min()` treatment) would otherwise
  // both stay 200px wide and physically overlap. Set once here, not on
  // each of the three children -- flex's default `align-items: stretch`
  // fills this width automatically.
  width: 'min(200px, 45vw)',
}

const COASTLINE_STYLE = new Style({
  stroke: new Stroke({ color: 'rgba(255, 255, 255, 0.8)', width: 1 }),
})

// Probe-click marker -- same yellow dot look geosat-mosaic-cesium's own
// <Entity point={{...}}> uses for its probe marker, built here from a
// plain ol/style/Circle instead (no 3D billboard/entity concept in a 2D
// OpenLayers map).
const MARKER_STYLE = new Style({
  image: new CircleStyle({
    radius: 6,
    fill: new Fill({ color: '#ffff00' }),
    stroke: new Stroke({ color: '#000000', width: 1 }),
  }),
})

const SATELLITE_CMAP = 'tab10'
const SATELLITE_VMIN = 0
const SATELLITE_VMAX = 6

// Measure-tool points + line/polygon -- cyan, matching
// geosat-mosaic-cesium's own `Color.CYAN` polyline/polygon material.
const MEASURE_POINT_STYLE = new Style({
  image: new CircleStyle({ radius: 4, fill: new Fill({ color: '#00ffff' }) }),
})
const MEASURE_LINE_STYLE = new Style({
  stroke: new Stroke({ color: '#00ffff', width: 3 }),
})
const MEASURE_POLYGON_STYLE = new Style({
  stroke: new Stroke({ color: '#00ffff', width: 3 }),
  fill: new Fill({ color: 'rgba(0, 255, 255, 0.2)' }),
})

// Device location marker -- blue dot with a white outline, the standard
// "you are here" look (matches geosat-mosaic-cesium's own PointGraphics
// there: DODGERBLUE fill, WHITE outline).
const DEVICE_LOCATION_STYLE = new Style({
  image: new CircleStyle({
    radius: 7,
    fill: new Fill({ color: '#1e90ff' }),
    stroke: new Stroke({ color: '#ffffff', width: 2 }),
  }),
})

// Fixed 30deg spacing, matching geosat-mosaic-cesium's own custom-built
// Graticule.tsx (that one had to build its own since Cesium's built-in
// GridImageryProvider is a per-tile debug grid, not fixed-degree lat/
// lon lines -- see that file's own comment). OpenLayers, unlike Cesium,
// *does* have a real geographic graticule layer built in
// (`ol/layer/Graticule`), so no custom implementation is needed here --
// `intervals: [30]` pins it to the same single fixed spacing rather
// than its own default zoom-adaptive behavior, matching the doc's own
// "toggle (on/off)" scope, not an adjustable interval.
const GRATICULE_STROKE = new Stroke({ color: 'rgba(255, 255, 255, 0.35)', width: 1 })

// About/Help buttons, their own horizontal row anchored top-right.
const aboutHelpRowStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  right: 12,
  zIndex: 1,
  display: 'flex',
  flexDirection: 'row',
  alignItems: 'center',
  // Small, deliberate gap to tighten the space between About and Help
  // (previously 72px apart via separate `right: 84`/`right: 12` offsets)
  // now that both live in a flex row instead of being individually
  // positioned.
  gap: 6,
}

const topRightButtonStyle: CSSProperties = {
  padding: '4px 10px',
  background: 'rgba(38, 38, 38, 0.8)',
  color: '#edeeef',
  border: '1px solid #555',
  borderRadius: 4,
  cursor: 'pointer',
  font: '12px sans-serif',
}

// Zoom control (OL's default +/- buttons) -- its own row below About/Help,
// not sharing their row. Used to sit at OL's own default position
// (top-left, `.ol-zoom`'s built-in `top: .5em; left: .5em`) -- directly
// behind LayerSwitcher's `panelStyle` (also `top: 12, left: 12`), which
// paints over it since it's a later sibling in the DOM. Moved here
// instead via the `Zoom` control's own `target` option (renders into
// `zoomControlContainerRef`'s div rather than OL's default overlay
// container), with `.app-zoom-slot`'s CSS (index.css) overriding its
// normal absolute top-left positioning and vertical +/- stack to lay out
// horizontally inside this row instead. `top: 48`, not 12 -- clears the
// About/Help row directly above it.
const zoomRowStyle: CSSProperties = {
  position: 'fixed',
  top: 48,
  right: 12,
  zIndex: 1,
  display: 'flex',
  flexDirection: 'row',
  alignItems: 'center',
}

// Rotate/north-reset control (OL's compass button, only visible once the
// map is actually rotated -- `autoHide`, its own default) -- moved from
// OL's own default top-right position (`.ol-rotate`'s built-in `top:
// .5em; right: .5em`, directly behind the About/Help/zoom cluster now
// occupying that corner) down to bottom-right instead. Same
// `target`-option technique as the zoom control above; `.app-rotate-slot`'s
// CSS (index.css) overrides its normal absolute positioning to just flow
// inside this div instead.
//
// `bottom: 44` (not OL's usual small offset) is deliberate: OL's own
// default Attribution control ("i" icon) sits at its own default
// bottom-right corner (`bottom: .5em; right: .5em` from ol.css, ~8-32px
// off the bottom edge) and is never repositioned in this app, so a
// smaller offset here put this control's ~30px box directly on top of
// it -- getBoundingClientRect on both showed them overlapping almost
// exactly. That was a bug, not just visual: the empty `.app-rotate-slot`
// container div (rendered even when the compass button inside it is
// autoHide-invisible) sat on top in the stacking order and silently
// absorbed clicks meant for the attribution icon (document.elementFromPoint
// at the icon's coordinates always resolved to this div, never
// `.ol-attribution button`), and once actually rotated, the two icons
// visually overlapped instead of the compass appearing above the
// attribution icon. 44px clears the attribution control's box with a
// small gap.
const rotateRowStyle: CSSProperties = {
  position: 'fixed',
  bottom: 44,
  right: 12,
  zIndex: 1,
}

// Dimmed backdrop behind OL's native Attribution control's expanded
// list, shown only on narrow/touch viewports (mirrors AppInfoPanel.tsx/
// InfoPanel.tsx's own `mobileBackdropStyle`). A React-rendered element
// rather than a CSS `::before`/`:has()` trick -- that was tried first
// and, despite computed style showing the pseudo-element had the right
// position/z-index/background, it never actually painted above the
// app's other fixed-position panels in this browser (document.elementFromPoint
// kept returning the panel underneath, not the map container). Not worth
// chasing further given a plain element is just as easy and matches this
// app's own established pattern anyway.
const attributionBackdropStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  zIndex: 9,
  background: 'rgba(0, 0, 0, 0.7)',
}

// Pass 2: layer switcher (basemap/mosaic/contribution-mask/coastline,
// all toggle+opacity+band/palette/range controls) + time controls
// (scrubber/play/speed + calendar date-jump), on top of Pass 1's plain
// 3-layer foundation. Point-probe/timeseries/measure/search/
// remaining panels are later passes, not this one.
function Map() {
  const containerRef = useRef<HTMLDivElement>(null)
  const zoomControlContainerRef = useRef<HTMLDivElement>(null)
  const rotateControlContainerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<OlMap | null>(null)
  const basemapLayerRef = useRef<TileLayer | null>(null)
  const mosaicLayerRef = useRef<TileLayer<XYZ> | null>(null)
  const contributionMaskLayerRef = useRef<TileLayer<XYZ> | null>(null)
  const coastlineLayerRef = useRef<VectorLayer | null>(null)
  const markerLayerRef = useRef<VectorLayer | null>(null)
  const measureLayerRef = useRef<VectorLayer | null>(null)
  const deviceLocationLayerRef = useRef<VectorLayer | null>(null)
  const graticuleLayerRef = useRef<GraticuleLayer | null>(null)
  // Mirrors geosat-mosaic-cesium's AnimationDriver.tsx gating advancement
  // on `globe.tilesLoaded` -- OL's own equivalent is the Map's aggregate
  // 'loadstart'/'loadend' events (fire whenever ANY layer's tile queue
  // becomes non-empty/empty), tracked here the same way geosat-gallery's
  // useAnimationDriver expects (a ref flipped by load events, not a
  // per-tick poll of some live object).
  const mapLoadedRef = useRef(true)
  const attributionPortalRef = useRef<HTMLDivElement>(null)

  const [attributionExpanded, setAttributionExpanded] = useState(false)
  const [allTimestamps, setAllTimestamps] = useState<string[]>([])
  const [timeIndex, setTimeIndex] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [intervalMs, setIntervalMs] = useState(1000)
  const currentTime = allTimestamps[timeIndex]

  // `tir1`, not `mir` -- default band on load.
  const [band, setBand] = useState<MosaicBand>('tir1')
  // `Greys`, not `viridis` -- plain grayscale default on load.
  const [palette, setPalette] = useState('Greys')
  const [colorMin, setColorMin] = useState(180)
  const [colorMax, setColorMax] = useState(330)
  const [colorbarGroups, setColorbarGroups] = useState<ColorbarGroup[]>([])
  // The band/time actually baked into the rendered mosaic/mask tile
  // URLs, decoupled from `band`/`currentTime` (which drive every other
  // display) -- same fix geosat-mosaic-cesium's Globe.tsx uses: without
  // this, a band/timestamp change fires the tile source update
  // immediately with the OLD color range, then again moments later once
  // refreshColorRange's min/max arrives, fetching every visible tile
  // twice. Updated in exactly one place
  // (refreshColorRange's `.then()`/`.catch()` below) together with
  // `colorMin`/`colorMax`, so the two land in a single tile-source
  // update instead of two. Starts `undefined`, not `currentTime` --
  // removes the old wrong-default-range flash on initial load too.
  const [tileBand, setTileBand] = useState<MosaicBand>(band)
  const [tileTime, setTileTime] = useState<string | undefined>(undefined)
  const [resettingColorRange, setResettingColorRange] = useState(false)

  const [basemap, setBasemap] = useState<ToggleableLayerState>({ visible: true, opacity: 1 })
  // 'aerial', not 'osm' -- matches geosat-mosaic-cesium's own default
  // (commit ab8d8d3).
  const [basemapStyle, setBasemapStyle] = useState<BasemapStyle>('aerial')
  // 0.81 default opacity, not 1.
  const [mosaicOpacity, setMosaicOpacity] = useState(0.81)
  const [contributionMask, setContributionMask] = useState<ToggleableLayerState>({
    visible: false,
    // 0.07 default opacity, not 0.6.
    opacity: 0.07,
  })
  const [coastline, setCoastline] = useState<ToggleableLayerState>({ visible: true, opacity: 0.8 })
  const [graticuleVisible, setGraticuleVisible] = useState(false)
  const [timeDisplay, setTimeDisplay] = useState<TimeDisplayMode>('utc')

  // Live sync / "show always recent", per geosat-mosaic-cesium's own
  // equivalent state. `cacheBustToken` is threaded into the mosaic/mask
  // tile URLs as an extra `_sync` param whenever either toggle is on --
  // see the sync-poll effect below for why (a "partial" file that gets
  // revised later can't get stuck showing a stale cached tile while the
  // user has explicitly asked for freshness).
  const [syncEnabled, setSyncEnabled] = useState(false)
  const [showAlwaysRecent, setShowAlwaysRecent] = useState(false)
  const [cacheBustToken, setCacheBustToken] = useState<number | undefined>(undefined)
  const activeCacheBust = syncEnabled || showAlwaysRecent ? cacheBustToken : undefined

  const [infoPanelOpen, setInfoPanelOpen] = useState(false)
  const [appInfoOpen, setAppInfoOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)

  // Point-probe, mirrors geosat-mosaic-cesium's Globe.tsx state shape --
  // defaults to checked (`true`), matching that app's own default.
  // `probeLocation` (not just
  // `probeResult`) is kept separately since the fetch effect below needs
  // to exist before the first fetch resolves, and the marker/re-probe
  // effect reacts to it changing for *any* reason (a fresh click, or the
  // shared current-timestamp/band changing under an already-open probe).
  const [pointProbeMode, setPointProbeMode] = useState(true)
  const [probeLocation, setProbeLocation] = useState<{ lat: number; lon: number } | null>(null)
  const [probeResult, setProbeResult] = useState<PointProbeResult | null>(null)
  const [probeLoading, setProbeLoading] = useState(false)
  const [probeError, setProbeError] = useState<string | null>(null)

  const [timeseriesOpen, setTimeseriesOpen] = useState(false)
  const [timeseriesData, setTimeseriesData] = useState<TimeseriesResponse | null>(null)
  const [timeseriesLoading, setTimeseriesLoading] = useState(false)
  const [timeseriesError, setTimeseriesError] = useState<string | null>(null)

  // Measurement tools, mirrors geosat-mosaic-cesium's Globe.tsx state
  // shape -- `measurePoints` accumulates one `[lon, lat]` per click while
  // `measureMode` isn't 'off'; MeasurePanel.tsx does the actual distance/
  // area math (lib/geometry.ts) off this same array. Mutually exclusive
  // with point-probe (both bind the map's own `click` event to different
  // meanings) -- enforced in the two mode setters below, not here.
  const [measureMode, setMeasureMode] = useState<MeasureMode>('off')
  const [measurePoints, setMeasurePoints] = useState<LonLat[]>([])

  // Device location marker. `watchPosition` (not a one-shot
  // `getCurrentPosition`) so the marker actually tracks the user if they
  // move, matching "tick mark showing where they *are*", not just where
  // they were when they enabled it -- same reasoning as
  // geosat-mosaic-cesium's own equivalent effect.
  const [deviceLocationEnabled, setDeviceLocationEnabled] = useState(false)
  const [devicePosition, setDevicePosition] = useState<LonLat | null>(null)
  const [deviceLocationError, setDeviceLocationError] = useState<string | null>(null)

  useEffect(() => {
    fetchAvailableTimestamps().then((timestamps) => {
      setAllTimestamps(timestamps)
      setTimeIndex(timestamps.length - 1)
    })
    fetchColorbars().then(setColorbarGroups)
  }, [])

  // Map + all 4 layers created once, in their final stacking order
  // (basemap -> mosaic -> contribution mask -> coastline) -- unlike
  // geosat-mosaic-cesium's resium-based layers, plain `ol/layer/Tile`
  // objects have no "read-only prop forces full recreation" problem, so
  // band/palette/range/basemap-style changes below just call
  // `.setSource()`/`.setOpacity()`/`.setVisible()` on these same,
  // never-reordered layer instances instead of needing the explicit
  // `index` self-healing dance that file's own comments describe.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return

    // Initial values here match the `basemapStyle`/`mosaicOpacity`/
    // `contributionMask` state defaults above -- these layers exist for
    // one render before that state-driven effect below overwrites them,
    // and for the basemap specifically a mismatched initial *source*
    // would mean a wasted initial tile fetch (opacity has no such cost
    // either way, since it's compositing-only, but kept in sync here too
    // for consistency/tidiness).
    const basemapLayer = new TileLayer({ source: createBasemapSource('aerial') })
    const mosaicLayer = new TileLayer<XYZ>({ opacity: 0.81 })
    const contributionMaskLayer = new TileLayer<XYZ>({ opacity: 0.07, visible: false })
    const coastlineLayer = new VectorLayer({
      source: new VectorSource({
        url: '/assets/ne_50m_coastline.geojson',
        format: new GeoJSON(),
        attributions: 'Natural Earth, 50m coastline',
      }),
      style: COASTLINE_STYLE,
    })
    const markerLayer = new VectorLayer({ source: new VectorSource(), style: MARKER_STYLE })
    const measureLayer = new VectorLayer({ source: new VectorSource() })
    const deviceLocationLayer = new VectorLayer({
      source: new VectorSource(),
      style: DEVICE_LOCATION_STYLE,
    })
    const graticuleLayer = new GraticuleLayer({
      strokeStyle: GRATICULE_STROKE,
      intervals: [30],
      visible: false,
    })
    basemapLayerRef.current = basemapLayer
    mosaicLayerRef.current = mosaicLayer
    contributionMaskLayerRef.current = contributionMaskLayer
    coastlineLayerRef.current = coastlineLayer
    markerLayerRef.current = markerLayer
    measureLayerRef.current = measureLayer
    deviceLocationLayerRef.current = deviceLocationLayer
    graticuleLayerRef.current = graticuleLayer

    const map = new OlMap({
      target: containerRef.current,
      // Default Zoom control redirected to render inside
      // `zoomControlContainerRef`'s div (its own row below About/Help)
      // instead of OL's own default top-left overlay position -- see
      // `zoomRowStyle`'s own comment for why. Same for Rotate, redirected
      // to `rotateControlContainerRef`'s div (bottom-right) instead of
      // OL's own default top-right position -- see `rotateRowStyle`'s
      // own comment for why.
      //
      // Rotate used to be omitted entirely on mobile (isNarrow) -- the
      // concern was its compass button appearing (autoHide only hides it
      // while unrotated) after an accidental pinch-rotate gesture, with
      // no easy way back to north on a touch device, and it sharing the
      // same corner as the attribution icon. Re-enabled for narrow
      // viewports too now that the attribution-icon overlap in this
      // corner is fixed (see `rotateRowStyle`'s own comment) -- tapping
      // the compass is exactly the "easy way back to north" that was
      // originally missing, so leaving it out isn't clearly better
      // anymore either.
      controls: defaultControls({ zoom: false, rotate: false }).extend([
        new Zoom({ target: zoomControlContainerRef.current ?? undefined }),
        new Rotate({ target: rotateControlContainerRef.current ?? undefined }),
      ]),
      view: new View({
        projection: 'EPSG:4326',
        center: [0, 0],
        zoom: 2,
        minZoom: 1,
        maxZoom: 8,
        extent: WORLD_EXTENT,
      }),
      layers: [
        basemapLayer,
        mosaicLayer,
        contributionMaskLayer,
        coastlineLayer,
        graticuleLayer,
        markerLayer,
        measureLayer,
        deviceLocationLayer,
      ],
    })
    map.on('loadstart', () => {
      mapLoadedRef.current = false
    })
    map.on('loadend', () => {
      mapLoadedRef.current = true
    })
    mapRef.current = map

    return () => {
      // `map.dispose()`, not just `setTarget(undefined)` -- under React
      // StrictMode's dev-only double-invoke of this effect,
      // `setTarget(undefined)` detaches the map's own viewport but never
      // calls `setMap(null)` on each control, so the Zoom control's
      // button element (rendered into `zoomControlContainerRef`'s div via
      // its `target` option, not the map's own viewport) never gets
      // removed -- the second mount's fresh Zoom control just appended a
      // second, duplicate +/- pair into that same still-populated div.
      // `dispose()` clears the controls collection first (see Map.js's own
      // `disposeInternal`), which triggers each control's actual removal.
      map.dispose()
      mapRef.current = null
    }
  }, [])

  // Tracks OL's native Attribution control's own expand/collapse toggle
  // (its button, its state) purely so a backdrop can be rendered behind
  // it on narrow viewports -- see `attributionBackdropStyle`'s own
  // comment for why this isn't done as a plain CSS overlay. Delegated on
  // `document`, not `containerRef` -- the reparenting effect below moves
  // `.ol-attribution` out of `containerRef`'s tree entirely while
  // expanded (into `attributionPortalRef`), so a listener scoped to
  // `containerRef` would stop seeing its clicks the moment it expands,
  // getting `attributionExpanded` permanently stuck `true` after the
  // very first collapse click: state never flipped back, backdrop never
  // unmounted, node never returned to its original OL-managed parent.
  // `document` is an ancestor of the button regardless
  // of which container currently holds it. Not attached directly to the
  // button itself since the button is created by OL and this avoids any
  // dependency on exactly when in the control's lifecycle its DOM
  // element exists. Does not interfere with OL's own click handling --
  // this only observes the same click via bubbling, it doesn't call
  // preventDefault/stopPropagation.
  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if ((event.target as HTMLElement).closest('.ol-attribution button')) {
        setAttributionExpanded((expanded) => !expanded)
      }
    }
    document.addEventListener('click', handleClick)
    return () => document.removeEventListener('click', handleClick)
  }, [])

  // Explicit close path for the backdrop and the Android back button --
  // NOT relying solely on the toggle-based listener above (which
  // requires exactly one matching click on the real button per open/
  // close, easy to get out of sync with any double-firing or gesture
  // quirks on touch devices -- surfaced as "backdrop leaves an imposed
  // black screen stuck" after hard-refresh fixed the earlier stacking
  // bug). This sets state directly (idempotent, no toggle) and
  // separately syncs OL's own `ol-collapsed` class by clicking its real
  // button, but ONLY when it isn't already collapsed, so this can never
  // accidentally re-open it.
  const closeAttribution = useCallback(() => {
    const attr = document.querySelector('.ol-attribution')
    if (attr && !attr.classList.contains('ol-collapsed')) {
      attr.querySelector<HTMLElement>('button')?.click()
    }
    setAttributionExpanded(false)
  }, [])

  // Android's back gesture/button doesn't fire a click on anything, so
  // it never reached the listener above -- reported alongside the
  // stuck-backdrop bug ("back button ... leaves the imposed black
  // screen"). Standard SPA-modal pattern: push a history entry the
  // instant this opens, and treat back navigation while it's open as a
  // close instead of letting it navigate away/do nothing. Only pushes
  // while actually expanded (effect keyed on `attributionExpanded`), so
  // this never adds more than one entry per open.
  useEffect(() => {
    if (!attributionExpanded) return
    history.pushState({ attributionModal: true }, '')
    // Goes through the same `closeAttribution` as the backdrop, not a
    // bare `setAttributionExpanded(false)` -- a bare state flip left OL's
    // own `ol-collapsed` class out of sync (still showing "expanded"
    // since nothing ever clicked the real button), so the *next* open
    // would reparent+show the backdrop while OL's own CSS still had the
    // credit list hidden underneath (`.ol-collapsed ul`).
    window.addEventListener('popstate', closeAttribution)
    return () => window.removeEventListener('popstate', closeAttribution)
  }, [attributionExpanded, closeAttribution])

  // Physically relocates OL's native `.ol-attribution` DOM node out to
  // `attributionPortalRef` (a plain top-level sibling, see its own JSX)
  // while expanded, restoring it to its original spot in the OL-managed
  // tree on collapse/unmount. This isn't cosmetic -- it's the actual fix
  // for the z-index-vs-Layers-panel bug: `.ol-viewport` (an ancestor of
  // `.ol-attribution`) has `overflow: hidden`, and elementFromPoint plus
  // an actual mouse click that closed the popup through it showed
  // Chromium traps a `position: fixed` descendant's effective stacking
  // to that ancestor's box regardless of z-index -- even z-index: 99999
  // directly on the element itself still lost to a plain z-index: 9
  // sibling. No z-index value escapes that trap; moving the node out of
  // the clipped subtree does. `attributionPortalRef` carries
  // `.app-map-narrow` itself (mirroring the map container's class) so
  // the existing `.app-map-narrow .ol-attribution` CSS still matches
  // after the move.
  //
  // `useLayoutEffect`, not `useEffect` -- this fixes the remaining bug
  // after the fix above first shipped ("still opens behind the layers
  // panel"). Plain `useEffect` runs AFTER the browser paints, so for one
  // frame the browser paints `.ol-attribution` in its original, still-
  // clipped/trapped location -- visibly behind the Layers panel -- before
  // this move happens on the next frame. `useLayoutEffect` runs
  // synchronously before paint, so the move is already done by the time
  // anything is drawn; there's no flash.
  useLayoutEffect(() => {
    const attr = containerRef.current?.querySelector<HTMLElement>('.ol-attribution')
    const portal = attributionPortalRef.current
    if (!attr || !portal || !attributionExpanded) return
    const originalParent = attr.parentElement
    const nextSibling = attr.nextSibling
    portal.appendChild(attr)
    return () => {
      if (nextSibling) originalParent?.insertBefore(attr, nextSibling)
      else originalParent?.appendChild(attr)
    }
  }, [attributionExpanded])

  // Basemap style/visibility/opacity.
  useEffect(() => {
    const layer = basemapLayerRef.current
    if (!layer) return
    layer.setSource(createBasemapSource(basemapStyle))
    layer.setVisible(basemap.visible)
    layer.setOpacity(basemap.opacity)
  }, [basemapStyle, basemap])

  // Satellite mosaic source (band/palette/range/time) + opacity. Reads
  // `tileBand`/`tileTime` (see their own comment above), not `band`/
  // `currentTime` directly -- keeps this in sync with
  // `refreshColorRange`'s corrected min/max instead of racing ahead of
  // it and double-fetching every tile.
  useEffect(() => {
    const layer = mosaicLayerRef.current
    if (!layer || !tileTime) return
    const params: Record<string, string> = {
      cmap: palette,
      vmin: String(colorMin),
      vmax: String(colorMax),
      norm: 'lin',
    }
    if (activeCacheBust !== undefined) params._sync = String(activeCacheBust)
    layer.setSource(
      new XYZ({
        url: xcubeTileUrlTemplate(xcubeDatasetId(tileTime), `value_${tileBand}`, params),
        projection: 'EPSG:4326',
        tileGrid: xcubeGeographicTileGrid(),
        // Makes below-vmin/above-vmax pixels transparent -- xcube's tile
        // endpoint has no query param for this (its OpenAPI spec only
        // lists crs/vmin/vmax/cmap/norm/time/format/retina, and its
        // rendering always uses `matplotlib.colors.Normalize(...,
        // clip=True)`, opaque-clipping out-of-range values to the
        // palette's own edge color). See lib/transparentEdgesTileLoader.ts
        // for the client-side fix and its one accepted limitation.
        tileLoadFunction: createTransparentEdgesTileLoader(() =>
          getPaletteEdgeColors(palette, colorbarGroups),
        ),
        // Surfaces the source satellites in OL's own native attribution
        // control (bottom-right), same as basemap.ts's per-style
        // `attributions` -- previously this layer had no attribution at
        // all, so the native control never mentioned satellite data
        // regardless of the active basemap.
        attributions: 'GOES-18/19 (NOAA), Meteosat-9/10 (EUMETSAT), INSAT-3DR/3DS (ISRO/SAC/MOSDAC), Himawari-9 (JMA)',
      }),
    )
  }, [tileBand, palette, colorMin, colorMax, tileTime, activeCacheBust, colorbarGroups])

  useEffect(() => {
    mosaicLayerRef.current?.setOpacity(mosaicOpacity)
  }, [mosaicOpacity])

  // Contribution mask source (band/time) + visibility/opacity -- same
  // `tileBand`/`tileTime` reasoning as the mosaic layer above.
  useEffect(() => {
    const layer = contributionMaskLayerRef.current
    if (!layer || !tileTime) return
    const params: Record<string, string> = {
      cmap: SATELLITE_CMAP,
      vmin: String(SATELLITE_VMIN),
      vmax: String(SATELLITE_VMAX),
      norm: 'cat',
    }
    if (activeCacheBust !== undefined) params._sync = String(activeCacheBust)
    layer.setSource(
      new XYZ({
        url: xcubeTileUrlTemplate(xcubeDatasetId(tileTime), `satellite_id_${tileBand}`, params),
        projection: 'EPSG:4326',
        tileGrid: xcubeGeographicTileGrid(),
      }),
    )
  }, [tileBand, tileTime, activeCacheBust])

  useEffect(() => {
    const layer = contributionMaskLayerRef.current
    if (!layer) return
    layer.setVisible(contributionMask.visible)
    layer.setOpacity(contributionMask.opacity)
  }, [contributionMask])

  // Coastline visibility/opacity -- rewrites the shared Style's stroke
  // color/alpha and re-applies it, same "mutate in place, don't replace
  // the read-only prop" reasoning geosat-mosaic-cesium's
  // CoastlineLayer.tsx uses for the same layer.
  useEffect(() => {
    const layer = coastlineLayerRef.current
    if (!layer) return
    layer.setVisible(coastline.visible)
    layer.setStyle(
      new Style({ stroke: new Stroke({ color: `rgba(255, 255, 255, ${coastline.opacity})`, width: 1 }) }),
    )
  }, [coastline])

  useEffect(() => {
    graticuleLayerRef.current?.setVisible(graticuleVisible)
  }, [graticuleVisible])

  // Point-probe click handler. Unlike geosat-mosaic-cesium's version
  // (needs ScreenSpaceEventHandler + camera.pickEllipsoid +
  // Cartographic conversion, since it's picking a 3D globe), this map's
  // own view projection is already EPSG:4326 -- `event.coordinate` *is*
  // `[lon, lat]` directly, no conversion needed.
  useEffect(() => {
    const map = mapRef.current
    if (!map || !pointProbeMode) return
    const handleClick = (event: MapBrowserEvent) => {
      const [lon, lat] = event.coordinate
      // Clicks poleward of the actual data (COVERAGE_EXTENT's lat bound,
      // same one the mosaic tile layer already uses to stop tiles
      // rendering there) used to still fire a probe -- the backend
      // doesn't error for an out-of-range lat, it silently clamps to the
      // nearest valid pixel and returns real data *for that clamped
      // point*, which looked like genuine data for wherever was actually
      // clicked. Ignore the click entirely here instead, matching what
      // the tile layer already does visually.
      if (Math.abs(lat) > COVERAGE_EXTENT[3]) return
      setProbeResult(null)
      setProbeError(null)
      // A new probe click invalidates any timeseries chart from a
      // previous location -- same reasoning as geosat-mosaic-cesium's
      // own click handler.
      setTimeseriesOpen(false)
      setProbeLocation({ lat, lon })
    }
    map.on('click', handleClick)
    return () => {
      map.un('click', handleClick)
    }
  }, [pointProbeMode])

  // Marker feature at the probed location -- rebuilt (not mutated) on
  // every `probeLocation` change, matching the "just set a fresh
  // feature" simplicity plain OL vector sources afford (no read-only-
  // prop recreation concern the way an <Entity> would have).
  useEffect(() => {
    const source = markerLayerRef.current?.getSource()
    if (!source) return
    source.clear()
    if (probeLocation) {
      source.addFeature(new Feature(new Point([probeLocation.lon, probeLocation.lat])))
    }
  }, [probeLocation])

  // Re-probes `probeLocation` whenever the shared current-timestamp
  // state or band changes for *any* reason, same reasoning as
  // geosat-mosaic-cesium's own equivalent effect -- an open probe panel
  // should track "what's actually here right now", not freeze at
  // whatever was true on the original click. `probeLocation` itself
  // changing (a fresh click) is what makes this effect double as the
  // *initial* fetch too.
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

  // Shared by handleViewTimeseries (config-driven default range) and
  // handleLoadFullTimeseriesHistory (every available timestamp).
  // Sequential on the backend (same reason geosat-mosaic-cesium's own
  // runTimeseriesFetch notes -- a threaded version proved unsafe against
  // this backend), so this can take a while; TimeseriesPanel shows a
  // loading state rather than assuming it's fast.
  const runTimeseriesFetch = useCallback(
    (times: string[]) => {
      if (!probeResult) return
      setTimeseriesTimes(times)
      setTimeseriesLoading(true)
      setTimeseriesError(null)
      fetchTimeseries(band, probeResult.lat, probeResult.lon, times)
        .then(setTimeseriesData)
        .catch((err: unknown) => setTimeseriesError(err instanceof Error ? err.message : String(err)))
        .finally(() => setTimeseriesLoading(false))
    },
    [probeResult, band],
  )

  // TimeseriesPanel's title reads `band` live (so it updated immediately
  // on a band switch), but the plotted data was whatever was last
  // fetched -- so switching bands while the panel was open showed the
  // NEW band's name over the OLD band's chart, until the next point-probe
  // click happened to refresh it. Re-runs the same `timeseriesTimes` range
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
  // backward to include it. `timeseriesTimes` always ends at the newest
  // available timestamp in every code path that sets it (the default
  // window, "load full history", and this extension itself all anchor
  // there), so "does `currentTime` need including" reduces to "is its
  // index older than the window's current start index" -- cheap to check
  // on every render this effect fires, and does nothing once already
  // showing full history (the comparison can never trigger once the
  // window already starts at index 0). Deliberately keyed on `currentTime`
  // alone, same rationale as the band effect's own comment on why
  // `runTimeseriesFetch`/`timeseriesTimes`/`allTimestamps` are excluded --
  // this still runs on every animation frame (currentTime changes every
  // tick), but the body is a cheap index comparison that only actually
  // calls `runTimeseriesFetch` on a genuine backward jump, which normal
  // forward-only playback never produces.
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
  // as the last point-probe click. Default range is governed by
  // timeseriesConfig.ts (shared, edit-in-both-apps pattern -- see that
  // file's own comment), not hardcoded here: 'window' takes the most
  // recent TIMESERIES_DEFAULT_WINDOW_SIZE of `allTimestamps`, 'full' takes
  // all of it. handleLoadFullTimeseriesHistory below is still the explicit
  // opt-in for the complete range regardless of this default.
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
  // across every available timestamp rather than just the config-driven
  // default range.
  const handleLoadFullTimeseriesHistory = useCallback(() => {
    runTimeseriesFetch(allTimestamps)
  }, [allTimestamps, runTimeseriesFetch])

  // Measurement tools click handler -- adds one point per click while
  // `measureMode` isn't 'off'. Same simplification over
  // geosat-mosaic-cesium's version as the point-probe handler above
  // (`event.coordinate` is already `[lon, lat]`, no picking/conversion
  // needed), and no backend call to make per click here either, just
  // accumulating points for lib/geometry.ts's distance/area math to run
  // over (done in MeasurePanel.tsx).
  useEffect(() => {
    const map = mapRef.current
    if (!map || measureMode === 'off') return
    const handleClick = (event: MapBrowserEvent) => {
      const [lon, lat] = event.coordinate
      setMeasurePoints((previous) => [...previous, [lon, lat]])
    }
    map.on('click', handleClick)
    return () => {
      map.un('click', handleClick)
    }
  }, [measureMode])

  // Measure points + connecting line/polygon, rebuilt on every
  // `measurePoints`/`measureMode` change -- cheap, these are small point
  // counts.
  useEffect(() => {
    const source = measureLayerRef.current?.getSource()
    if (!source) return
    source.clear()
    const features: Feature[] = measurePoints.map((point) => {
      const feature = new Feature(new Point(point))
      feature.setStyle(MEASURE_POINT_STYLE)
      return feature
    })
    if (measureMode === 'distance' && measurePoints.length >= 2) {
      const line = new Feature(new LineString(measurePoints))
      line.setStyle(MEASURE_LINE_STYLE)
      features.push(line)
    }
    if (measureMode === 'area' && measurePoints.length >= 3) {
      const polygon = new Feature(new Polygon([[...measurePoints, measurePoints[0]]]))
      polygon.setStyle(MEASURE_POLYGON_STYLE)
      features.push(polygon)
    }
    source.addFeatures(features)
  }, [measurePoints, measureMode])

  // Device location marker feature -- rebuilt on every `devicePosition`
  // change, same "just set a fresh feature" pattern as the probe marker.
  useEffect(() => {
    const source = deviceLocationLayerRef.current?.getSource()
    if (!source) return
    source.clear()
    if (devicePosition) {
      source.addFeature(new Feature(new Point(devicePosition)))
    }
  }, [devicePosition])

  useEffect(() => {
    if (!deviceLocationEnabled) return
    if (!navigator.geolocation) {
      setDeviceLocationError('Geolocation is not available in this browser')
      return
    }
    const watchId = navigator.geolocation.watchPosition(
      (position) => {
        setDeviceLocationError(null)
        setDevicePosition([position.coords.longitude, position.coords.latitude])
      },
      (error) => {
        setDeviceLocationError(error.message)
      },
    )
    return () => {
      navigator.geolocation.clearWatch(watchId)
    }
  }, [deviceLocationEnabled])

  // Place-search fly-to. Genuinely simpler than geosat-mosaic-cesium's
  // own handleSelectPlace: that one has a ±66° 2D-mode restriction
  // (Cesium's 2D/Columbus View project data differently near the poles),
  // which doesn't apply here at all -- this map has no separate 3D mode
  // to fall back to and no such projection concern (plain EPSG:4326
  // throughout), so a place outside the real data coverage band just
  // shows basemap-only there, a perfectly normal thing for a 2D map to
  // do. Always flies, never refuses.
  const handleSelectPlace = useCallback((result: PlaceSearchResult): string | null => {
    mapRef.current?.getView().animate({ center: [result.lon, result.lat], zoom: 6 })
    return null
  }, [])

  // Live min/max for a given band+timestamp (fetchMinMax), not a fixed
  // constant -- shared by the reset button, band switches, and manual
  // timestamp changes below. Deliberately NOT re-run on every animation
  // tick (see the dedicated playing-sync effect further down) -- calling
  // it as fast as every 250ms at 4x would spam the server, lag behind
  // actual frame advance, and make the color scale visibly shift
  // frame-to-frame, the opposite of what a stable animated comparison
  // needs.
  const refreshColorRange = useCallback((forBand: MosaicBand, forTime: string | undefined) => {
    if (!forTime) return
    setResettingColorRange(true)
    fetchMinMax(forBand, forTime)
      .then(({ min, max }) => {
        setColorMin(Math.round(min * 10) / 10)
        setColorMax(Math.round(max * 10) / 10)
        setTileBand(forBand)
        setTileTime(forTime)
      })
      .catch(() => {
        // A failure mode seen on geosat-mosaic-cesium too (a transient
        // 503, a network blip): `tileBand`/`tileTime` are the only thing
        // that lets the mosaic/mask layers render at all -- if this fetch
        // never resolves successfully, tiles would otherwise stay
        // blocked forever with no automatic recovery, even though tile
        // serving itself (xcube) is completely unaffected by a
        // metadata-API hiccup. Apply the band/time change anyway, keeping
        // whatever colorMin/colorMax was already showing instead of the
        // fetched range.
        setTileBand(forBand)
        setTileTime(forTime)
      })
      .finally(() => setResettingColorRange(false))
  }, [])

  // Populates the color range with the actual band+timestamp min/max on
  // first load too, instead of leaving the fixed 180/330 fallback
  // showing until the user does something -- runs once, as soon as the
  // timestamp list finishes loading and `currentTime` exists.
  const initialColorRangeFetched = useRef(false)
  useEffect(() => {
    if (initialColorRangeFetched.current || !currentTime) return
    initialColorRangeFetched.current = true
    refreshColorRange(band, currentTime)
  }, [currentTime, band, refreshColorRange])

  // Animation playback never calls refreshColorRange (keeping a stable
  // color scale across frames is the point) -- sync tileTime straight to
  // currentTime on every tick instead, same band/range already showing.
  useEffect(() => {
    if (!playing) return
    setTileTime(currentTime)
  }, [playing, currentTime])

  const handleBandChange = useCallback(
    (nextBand: MosaicBand) => {
      setBand(nextBand)
      refreshColorRange(nextBand, currentTime)
    },
    [currentTime, refreshColorRange],
  )

  // Shared by both TimeControls' scrubber and CalendarControl's date-
  // jump -- either way, a manual timestamp change refreshes the color
  // range (unlike animation playback above).
  const handleManualIndexChange = useCallback(
    (nextIndex: number) => {
      setTimeIndex(nextIndex)
      refreshColorRange(band, allTimestamps[nextIndex])
    },
    [band, allTimestamps, refreshColorRange],
  )

  const handleResetColorRange = useCallback(() => {
    refreshColorRange(band, currentTime)
  }, [band, currentTime, refreshColorRange])

  // Live sync / "show always recent" 5-minute poll. One interval, set up
  // once, gated by refs (not the effect's own deps) so toggling either
  // checkbox doesn't tear down and restart the timer -- same "stable
  // listener + refs for current values" pattern as the point-probe/
  // measure click handlers. Skips the fetch entirely when neither toggle
  // is on, so this is a no-op (no network traffic at all) for the
  // common off-by-default case.
  const bandRef = useRef(band)
  bandRef.current = band
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
        // "Show always recent" is the one that also follows live --
        // moves the current selection to the newest timestamp. Paused
        // (not just visually disabled) while playing, so a poll landing
        // mid-animation has no effect on the current selection at all --
        // it just still extended `allTimestamps` via the sync-shared
        // refresh above, same as sync alone would have.
        if (showAlwaysRecentRef.current && !playingRef.current) {
          const absoluteIndex = newAll.length - 1
          setTimeIndex(absoluteIndex)
          refreshColorRange(bandRef.current, newAll[absoluteIndex])
        }
      })
    }
    const id = setInterval(tick, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [refreshColorRange])

  const handleAdvance = useCallback(() => {
    setTimeIndex((index) => (allTimestamps.length === 0 ? index : (index + 1) % allTimestamps.length))
  }, [allTimestamps.length])
  useAnimationDriver(playing, intervalMs, mapLoadedRef, handleAdvance)

  // On a narrow phone screen, Time/Date/Search are embedded inside
  // LayerSwitcher's own "Layers" panel instead of standing alone in the
  // right-side column below -- consolidates the mobile layout down to
  // one panel. See LayerSwitcher.tsx's own `isNarrow &&` guards for where
  // they actually render in that case.
  const isNarrow = useIsNarrowViewport()

  return (
    <>
      <div
        ref={containerRef}
        style={containerStyle}
        className={isNarrow ? 'app-map-container app-map-narrow' : 'app-map-container'}
      />
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
        bandDisabled={playing}
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
            // Mutually exclusive with measure mode -- both bind the
            // map's own `click` event to a different meaning.
            setMeasureMode('off')
            setMeasurePoints([])
          }
          if (!next) {
            // Turning the mode off also clears any stale
            // location/result/marker -- and `probeLoading`, same
            // stuck-loading-forever bug already fixed in
            // geosat-mosaic-cesium (see that app's PointProbePanel
            // onClose comment): once `probeLocation` goes null, the
            // probe-fetch effect's guard clause returns early without
            // ever resetting `probeLoading`, and the in-flight request's
            // own `.finally()` is gated behind that same effect's
            // `cancelled` closure -- so without this, closing mid-fetch
            // would leave the panel stuck showing "Loading..." forever.
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
            // Mutually exclusive with point-probe -- same reasoning as
            // above, the other direction.
            setPointProbeMode(false)
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
        showAlwaysRecentDisabled={playing}
        timestamps={allTimestamps}
        timeIndex={timeIndex}
        onIndexChange={handleManualIndexChange}
        playing={playing}
        onPlayingChange={setPlaying}
        intervalMs={intervalMs}
        onIntervalMsChange={setIntervalMs}
        allTimestamps={allTimestamps}
        onJump={handleManualIndexChange}
        dateControlsDisabled={playing}
        onSelectPlace={handleSelectPlace}
      />
      {measureMode !== 'off' && (
        <MeasurePanel mode={measureMode} points={measurePoints} onClear={() => setMeasurePoints([])} />
      )}
      {pointProbeMode && (
        <PointProbePanel
          loading={probeLoading}
          error={probeError}
          result={probeResult}
          onClose={() => {
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
      <InfoPanel
        open={infoPanelOpen}
        onClose={() => setInfoPanelOpen(false)}
        band={band}
        time={currentTime}
        playing={playing}
        timeDisplay={timeDisplay}
      />
      <AppInfoPanel open={appInfoOpen} onClose={() => setAppInfoOpen(false)} />
      <HelpPanel open={helpOpen} onClose={() => setHelpOpen(false)} />
      <div style={aboutHelpRowStyle}>
        <button style={topRightButtonStyle} onClick={() => setAppInfoOpen((open) => !open)}>
          About
        </button>
        <button style={topRightButtonStyle} onClick={() => setHelpOpen((open) => !open)}>
          Help
        </button>
      </div>
      <div style={zoomRowStyle}>
        <div ref={zoomControlContainerRef} className="app-zoom-slot" />
      </div>
      <div ref={rotateControlContainerRef} className="app-rotate-slot" style={rotateRowStyle} />
      {isNarrow && attributionExpanded && (
        <div
          style={attributionBackdropStyle}
          onClick={(event) => {
            // Stops here rather than reaching the map underneath (which
            // has its own click handler -- point-probe, on by default --
            // that could otherwise fire from the same tap and open an
            // unrelated popup on top of/alongside this one).
            event.stopPropagation()
            closeAttribution()
          }}
        />
      )}
      {/* Always rendered (not just while expanded) so the reparenting
          effect above always has somewhere to move `.ol-attribution`
          into the instant it expands. Carries `app-map-narrow` itself so
          the existing `.app-map-narrow .ol-attribution` CSS still
          matches once the node lives here instead of inside the map. */}
      <div ref={attributionPortalRef} className={isNarrow ? 'app-map-narrow' : undefined} />
      <ScanTimeLabel band={band} time={currentTime} timeDisplay={timeDisplay} />
      {!isNarrow && (
        <div style={rightColumnStyle}>
          <TimeControls
            timestamps={allTimestamps}
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
            onJump={handleManualIndexChange}
            disabled={playing}
            timeDisplay={timeDisplay}
          />
          <PlaceSearch onSelect={handleSelectPlace} />
        </div>
      )}
    </>
  )
}

export default Map
