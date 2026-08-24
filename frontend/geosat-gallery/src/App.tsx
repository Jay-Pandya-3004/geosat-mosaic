import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import ImageViewer from './components/ImageViewer'
import AnimationControls from './components/AnimationControls'
import CalendarControl from './components/CalendarControl'
import ControlPanel from './components/ControlPanel'
import FullscreenButton from './components/FullscreenButton'
import HelpPanel from './components/HelpPanel'
import InfoPanel from './components/InfoPanel'
import Panel from './components/Panel'
import TopLabel from './components/TopLabel'
import {
  fetchGalleryCatalog,
  resolveThumbnailUrl,
  type GalleryBand,
  type GalleryCatalog,
} from './lib/gallery'
import { fetchAvailableTimestamps } from './lib/timestamps'
import type { TimeDisplayMode } from './lib/time'
import { useAnimationDriver } from './lib/useAnimationDriver'
import { useIsNarrowViewport } from './lib/useIsNarrowViewport'
import { useIsShortViewport } from './lib/useIsShortViewport'

// Date/Controls/Animation split into three separate top corners (was one
// shared left-side column) -- TopLabel (band/time + Help/Info) moved down
// to bottom-center in the same change, so nothing at the top needs the old
// narrow-viewport "push down below TopLabel" treatment anymore; all three
// can sit at a flat top:12 unconditionally.
const datePanelStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  left: 12,
  zIndex: 1,
}

// Controls + the catalog-error message (if any) stacked in one column,
// same reasoning as the old shared panelsContainerStyle for why the error
// lives next to Controls specifically -- picking a band is what triggers
// the catalog fetch that error is reporting on.
const controlPanelStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  left: '50%',
  transform: 'translateX(-50%)',
  zIndex: 1,
  display: 'flex',
  flexDirection: 'column',
  gap: 12,
}

const animationPanelStyle: CSSProperties = {
  position: 'fixed',
  top: 12,
  right: 12,
  zIndex: 1,
}

// On a narrow phone screen, Controls/Animation/Date collapse into this
// one shared panel at top-left instead of the three separate corners
// above. `left:12` unconditionally -- nothing else occupies either side
// row on mobile (TopLabel is bottom-center).
//
// `top` vs `bottom` depends on `isShort` (useIsShortViewport.ts) -- a
// short viewport (phone held in landscape, height collapses to ~360-430px
// even though width is well over useIsNarrowViewport's own breakpoint)
// moves this panel to bottom-left instead of top-left. Two independent
// axes, not one: `isNarrow` (width) decides *whether* the three panels
// combine into this one at all, `isShort` (height) only decides *which
// corner* the combined panel lands in once it does.
function mobilePanelWrapStyle(isShort: boolean): CSSProperties {
  return {
    position: 'fixed',
    top: isShort ? undefined : 12,
    bottom: isShort ? 12 : undefined,
    left: 12,
    zIndex: 1,
  }
}

// Passed as Panel's own `style` override. `maxHeight` + `overflowY:
// 'auto'` so the combined panel (three panels' worth of content) scrolls
// internally instead of running off the bottom of a short phone screen.
// `overflowX: 'hidden'` alongside it is a defensive backstop for a CSS
// Overflow-spec quirk the sibling apps in this project have hit before:
// setting `overflowY: 'auto'` alone implicitly promotes `overflowX` to
// `auto` too (mixing `visible` with a non-`visible` value on the other
// axis isn't allowed), which can produce a stray horizontal scrollbar if
// any child ever exceeds the panel's own width.
const mobileCombinedPanelStyle: CSSProperties = {
  maxHeight: 'calc(100vh - 100px)',
  overflowY: 'auto',
  overflowX: 'hidden',
}

// Divider between Controls/Animation/Date's embedded sections inside the
// combined mobile panel -- same shared style (top border + padding)
// geosat-mosaic-cesium's/geosat-mosaic-openlayers's own LayerSwitcher.tsx
// use for their equivalent embedded Time/Date/Search sections, so they
// read as distinct sub-sections rather than blending into one list.
const embeddedSectionStyle: CSSProperties = {
  marginTop: 10,
  paddingTop: 10,
  borderTop: '1px solid #444',
}

function App() {
  const isNarrow = useIsNarrowViewport()
  const isShort = useIsShortViewport()
  // Either axis alone counts as "mobile layout": a narrow *or* short
  // viewport. Needed because useIsNarrowViewport.ts is width-only (560px)
  // -- a phone in landscape is well over that (iPhone SE landscape is
  // 667px wide, most modern phones are 740px+), so without also checking
  // height, a landscape phone would fall back to the desktop three-corner
  // layout instead of ever reaching the combined mobile panel this
  // `isShort`-vs-position logic exists to reposition.
  const isMobileLayout = isNarrow || isShort
  const [allTimestamps, setAllTimestamps] = useState<string[]>([])
  const [timeIndex, setTimeIndex] = useState(0)
  // `tir1`, not `mir`, is the default band on load.
  const [band, setBand] = useState<GalleryBand>('tir1')
  const [playing, setPlaying] = useState(false)
  const [intervalMs, setIntervalMs] = useState(1000)
  const [timeDisplay, setTimeDisplay] = useState<TimeDisplayMode>('utc')
  const [syncEnabled, setSyncEnabled] = useState(false)
  const [showAlwaysRecent, setShowAlwaysRecent] = useState(false)
  const [infoOpen, setInfoOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)

  // Per-band datasetScan catalog listing (target-timestamp -> thumbnail
  // URL), fetched lazily the first time each band is selected and cached
  // for the life of the app -- see lib/gallery.ts's own note on why this
  // (not a per-frame geosat-mosaic-api call) is how this app resolves URLs.
  const [catalogs, setCatalogs] = useState<Partial<Record<GalleryBand, GalleryCatalog>>>({})
  const catalogsRef = useRef(catalogs)
  catalogsRef.current = catalogs
  const [catalogError, setCatalogError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchAvailableTimestamps().then((all) => {
      if (cancelled) return
      setAllTimestamps(all)
      setTimeIndex(all.length - 1)
    })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (catalogsRef.current[band]) return
    let cancelled = false
    setCatalogError(null)
    fetchGalleryCatalog(band)
      .then((catalog) => {
        if (cancelled) return
        setCatalogs((previous) => ({ ...previous, [band]: catalog }))
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setCatalogError(error instanceof Error ? error.message : String(error))
        }
      })
    return () => {
      cancelled = true
    }
  }, [band])

  const currentTime = allTimestamps[timeIndex]
  const currentCatalog = catalogs[band]
  const thumbnailUrl = useMemo(
    () => (currentCatalog && currentTime ? resolveThumbnailUrl(currentCatalog, currentTime) : undefined),
    [currentCatalog, currentTime],
  )

  const handleAdvance = useCallback(() => {
    setTimeIndex((previous) => (previous + 1) % Math.max(allTimestamps.length, 1))
  }, [allTimestamps.length])

  const imageLoadedRef = useRef(true)
  useAnimationDriver(playing && allTimestamps.length > 0, intervalMs, imageLoadedRef, handleAdvance)

  // "Show always recent" gets disabled *and unchecked* the instant
  // animation starts -- more specific here than GeoSat-MOSAIC's "disabled
  // but stays checked" rule for the same control. Band/calendar/timestamp
  // selection deliberately stay enabled during playback (see
  // CalendarControl.tsx/ControlPanel.tsx, no `disabled` wiring for those)
  // -- only this one control is touched.
  useEffect(() => {
    if (playing) setShowAlwaysRecent(false)
  }, [playing])

  // Jump to the newest timestamp the instant "show always recent" is
  // turned on, not just on the next 5-minute poll below -- otherwise
  // checking the box does nothing visible for up to 5 minutes (whenever
  // the poll interval next happens to fire), which reads as "not working"
  // even though the poll itself is correct. "Always show recent" should
  // mean *immediately* recent, not eventually-recent.
  useEffect(() => {
    if (!showAlwaysRecent) return
    let cancelled = false
    fetchAvailableTimestamps().then((newAll) => {
      if (cancelled) return
      setAllTimestamps(newAll)
      setTimeIndex(newAll.length - 1)
    })
    return () => {
      cancelled = true
    }
  }, [showAlwaysRecent])

  // Live sync / "show always recent" 5-minute poll -- same "stable
  // listener + refs for current values" pattern as geosat-mosaic's Globe.tsx
  // equivalent, so toggling either checkbox doesn't tear down and restart
  // the timer. No cache-busting needed here (unlike geosat-mosaic's WMS
  // tiles) -- gallery PNGs are static once written, only the *list* of
  // which timestamps exist can grow.
  const syncEnabledRef = useRef(syncEnabled)
  syncEnabledRef.current = syncEnabled
  const showAlwaysRecentRef = useRef(showAlwaysRecent)
  showAlwaysRecentRef.current = showAlwaysRecent
  const currentTimeRef = useRef(currentTime)
  currentTimeRef.current = currentTime

  useEffect(() => {
    const tick = () => {
      if (!syncEnabledRef.current && !showAlwaysRecentRef.current) return
      fetchAvailableTimestamps().then((newAll) => {
        setAllTimestamps(newAll)
        setTimeIndex((previousIndex) => {
          if (showAlwaysRecentRef.current) return newAll.length - 1
          const preserved = currentTimeRef.current
          const preservedIndex = preserved ? newAll.indexOf(preserved) : -1
          return preservedIndex !== -1 ? preservedIndex : Math.min(previousIndex, newAll.length - 1)
        })
      })
    }
    const id = setInterval(tick, 5 * 60 * 1000)
    return () => clearInterval(id)
  }, [])

  return (
    <>
      <ImageViewer
        url={thumbnailUrl}
        band={band}
        catalogReady={Boolean(currentCatalog)}
        imageLoadedRef={imageLoadedRef}
      />
      <FullscreenButton />
      <TopLabel
        band={band}
        time={currentTime}
        timeDisplay={timeDisplay}
        infoOpen={infoOpen}
        onInfoClick={() => setInfoOpen((value) => !value)}
        helpOpen={helpOpen}
        onHelpClick={() => setHelpOpen((value) => !value)}
      />
      <HelpPanel open={helpOpen} onClose={() => setHelpOpen(false)} isNarrow={isMobileLayout} />
      <InfoPanel
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        band={band}
        time={currentTime}
        playing={playing}
        timeDisplay={timeDisplay}
        isNarrow={isMobileLayout}
      />
      {isMobileLayout ? (
        <div style={mobilePanelWrapStyle(isShort)}>
          <Panel title="Controls" defaultCollapsed style={mobileCombinedPanelStyle}>
            <ControlPanel
              band={band}
              onBandChange={setBand}
              syncEnabled={syncEnabled}
              onSyncEnabledChange={setSyncEnabled}
              showAlwaysRecent={showAlwaysRecent}
              onShowAlwaysRecentChange={setShowAlwaysRecent}
              showAlwaysRecentDisabled={playing}
              timeDisplay={timeDisplay}
              onTimeDisplayChange={setTimeDisplay}
              bare
            />
            {catalogError && (
              <div
                style={{
                  color: '#ff8080',
                  font: '12px sans-serif',
                  background: 'rgba(38, 38, 38, 0.9)',
                  padding: '6px 10px',
                  borderRadius: 4,
                  marginTop: 8,
                }}
              >
                {catalogError}
              </div>
            )}
            <div style={embeddedSectionStyle}>
              <AnimationControls
                timestamps={allTimestamps}
                index={timeIndex}
                onIndexChange={setTimeIndex}
                playing={playing}
                onPlayingChange={setPlaying}
                intervalMs={intervalMs}
                onIntervalMsChange={setIntervalMs}
                timeDisplay={timeDisplay}
                bare
              />
            </div>
            <div style={embeddedSectionStyle}>
              <CalendarControl
                allTimestamps={allTimestamps}
                currentTime={currentTime}
                onJump={setTimeIndex}
                timeDisplay={timeDisplay}
                bare
              />
            </div>
          </Panel>
        </div>
      ) : (
        <>
          <div style={datePanelStyle}>
            <CalendarControl
              allTimestamps={allTimestamps}
              currentTime={currentTime}
              onJump={setTimeIndex}
              timeDisplay={timeDisplay}
            />
          </div>
          <div style={controlPanelStyle}>
            <ControlPanel
              band={band}
              onBandChange={setBand}
              syncEnabled={syncEnabled}
              onSyncEnabledChange={setSyncEnabled}
              showAlwaysRecent={showAlwaysRecent}
              onShowAlwaysRecentChange={setShowAlwaysRecent}
              showAlwaysRecentDisabled={playing}
              timeDisplay={timeDisplay}
              onTimeDisplayChange={setTimeDisplay}
            />
            {catalogError && (
              <div
                style={{
                  color: '#ff8080',
                  font: '12px sans-serif',
                  background: 'rgba(38, 38, 38, 0.9)',
                  padding: '6px 10px',
                  borderRadius: 4,
                }}
              >
                {catalogError}
              </div>
            )}
          </div>
          <div style={animationPanelStyle}>
            <AnimationControls
              timestamps={allTimestamps}
              index={timeIndex}
              onIndexChange={setTimeIndex}
              playing={playing}
              onPlayingChange={setPlaying}
              intervalMs={intervalMs}
              onIntervalMsChange={setIntervalMs}
              timeDisplay={timeDisplay}
            />
          </div>
        </>
      )}
    </>
  )
}

export default App
