import { useEffect, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { BAND_LABELS, type GalleryBand } from '../lib/gallery'

interface ImageViewerProps {
  url: string | undefined
  band: GalleryBand
  catalogReady: boolean
  imageLoadedRef: RefObject<boolean>
}

const containerStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: '#111',
  overflow: 'hidden',
  userSelect: 'none',
}

const imgStyle: CSSProperties = {
  maxWidth: '100%',
  maxHeight: '100%',
  objectFit: 'contain',
}

const messageStyle: CSSProperties = {
  color: '#aaa',
  font: '14px sans-serif',
}

const MIN_ZOOM = 1
const MAX_ZOOM = 6
// Multiplicative step per wheel tick, not additive -- feels consistent at
// both ends of the zoom range (a fixed +0.5 step feels huge at 1x and
// barely noticeable at 5x).
const ZOOM_STEP = 1.15

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

interface Point {
  x: number
  y: number
}

// Keeps the image from being dragged/zoomed so far that its edges leave
// a big blank gap on both sides -- a rough approximation (assumes the
// rendered image roughly fills the container on at least one axis, true
// for objectFit:contain against this app's landscape mosaic PNGs against
// a landscape-ish viewport), not pixel-exact against the real letterboxed
// image rect. Good enough to stop panning into empty space indefinitely
// without needing to measure the actual rendered image rect on every
// drag frame.
function clampPan(pan: Point, zoom: number, rect: DOMRect): Point {
  const maxX = ((zoom - 1) / 2) * rect.width
  const maxY = ((zoom - 1) / 2) * rect.height
  return { x: clamp(pan.x, -maxX, maxX), y: clamp(pan.y, -maxY, maxY) }
}

// Full-viewport image -- kept deliberately simple, just an image web
// viewer: no Cesium globe, no live WMS/WMTS tiles, the pre-rendered
// gallery PNG *is* the entire display. The band+time caption used to live
// here (bottom-left) -- moved to TopLabel.tsx (top-center, big text), not
// duplicated here.
interface ViewState {
  zoom: number
  pan: Point
}

const RESET_VIEW: ViewState = { zoom: 1, pan: { x: 0, y: 0 } }

function ImageViewer({ url, band, catalogReady, imageLoadedRef }: ImageViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Zoom and pan live in one state object, always updated together via
  // React's functional setState form (`setView(prev => ...)`), not read
  // from a ref -- a `scroll` gesture (or any burst of synchronous wheel
  // events) can dispatch several native `wheel` events before React gets
  // a chance to re-render and refresh a ref, so a ref-based "read current
  // zoom, compute next zoom" approach silently applies only the LAST
  // event's effect instead of compounding across all of them (a 6-tick
  // scroll gesture only zoomed by one step). The functional updater form
  // doesn't have this problem -- React applies each queued update against
  // the true latest state in order, however many arrive before the next
  // paint.
  const [view, setView] = useState<ViewState>(RESET_VIEW)
  const [isDragging, setIsDragging] = useState(false)

  // Only used for the mousedown "should a drag even start" gate below --
  // a single point-in-time read, not part of a compounding calculation,
  // so the same staleness concern above doesn't apply here.
  const zoomRef = useRef(view.zoom)
  zoomRef.current = view.zoom

  useEffect(() => {
    // A new frame is requested (url changed) -- gate the animation driver
    // again until this one actually finishes loading, same rationale as
    // geosat-mosaic's AnimationDriver.tsx tilesLoaded gating. When there's
    // no URL to load (catalog still loading, or a genuine gap for this
    // timestamp), there's nothing to wait for -- mark "ready" immediately
    // so the driver doesn't stall.
    imageLoadedRef.current = url === undefined
    // Reset zoom/pan on every new frame -- staying zoomed/panned into a
    // spot on the OLD frame after the timestamp/band changes would show
    // the wrong region on the new image, not the same real-world spot.
    setView(RESET_VIEW)
  }, [url, imageLoadedRef])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    // Scroll to zoom in, scroll out to zoom out, toward wherever the
    // cursor currently is. Standard "zoom toward point" math: solve for
    // the new pan that keeps the image point
    // currently under the cursor in the same screen position after the
    // scale changes (pan is applied post-scale in screen pixels, per
    // `transform: translate(pan) scale(zoom)`'s left-to-right function
    // composition, so a plain 1:1 pixel adjustment works here without
    // needing to divide by zoom).
    const handleWheel = (event: WheelEvent) => {
      event.preventDefault()
      const rect = container.getBoundingClientRect()
      const mx = event.clientX - rect.left - rect.width / 2
      const my = event.clientY - rect.top - rect.height / 2
      setView((prev) => {
        // Scaled by the actual magnitude of `deltaY`, not just its sign --
        // a single trackpad/mouse-wheel tick is usually close to ±100,
        // which this reduces to very nearly the plain `prev.zoom *
        // ZOOM_STEP` step used before, but a momentum-scroll or a
        // synthetic/automated wheel event can report one native `wheel`
        // event covering several ticks' worth of `deltaY` at once (a
        // 6-tick scroll gesture arrives as one event with `deltaY: -600`)
        // -- sign-only handling silently applied just one step's worth of
        // zoom regardless of how far the user actually scrolled.
        const factor = ZOOM_STEP ** (-event.deltaY / 100)
        const nextZoom = clamp(prev.zoom * factor, MIN_ZOOM, MAX_ZOOM)
        const nextPan = {
          x: mx - (nextZoom / prev.zoom) * (mx - prev.pan.x),
          y: my - (nextZoom / prev.zoom) * (my - prev.pan.y),
        }
        return { zoom: nextZoom, pan: clampPan(nextPan, nextZoom, rect) }
      })
    }
    // Wired as a real native listener with `{ passive: false }`, not
    // React's synthetic onWheel -- preventDefault is needed here to stop
    // the browser's own scroll/zoom gesture from fighting this, and
    // React's wheel handler isn't reliably able to do that across
    // browsers.
    container.addEventListener('wheel', handleWheel, { passive: false })
    return () => container.removeEventListener('wheel', handleWheel)
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    let dragging = false
    let last: Point = { x: 0, y: 0 }

    // Click-and-drag to pan, only once zoomed in (nothing to explore at
    // 1x, and it would fight normal clicks elsewhere in the app
    // otherwise). `mousemove`/`mouseup` are on
    // `window`, not the container, so a drag that continues past the
    // container's edge -- or ends there -- still tracks and releases
    // correctly.
    const handleMouseDown = (event: MouseEvent) => {
      if (zoomRef.current <= 1) return
      dragging = true
      last = { x: event.clientX, y: event.clientY }
      setIsDragging(true)
    }
    const handleMouseMove = (event: MouseEvent) => {
      if (!dragging) return
      const dx = event.clientX - last.x
      const dy = event.clientY - last.y
      last = { x: event.clientX, y: event.clientY }
      const rect = container.getBoundingClientRect()
      setView((prev) => ({
        zoom: prev.zoom,
        pan: clampPan({ x: prev.pan.x + dx, y: prev.pan.y + dy }, prev.zoom, rect),
      }))
    }
    const handleMouseUp = () => {
      dragging = false
      setIsDragging(false)
    }

    container.addEventListener('mousedown', handleMouseDown)
    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      container.removeEventListener('mousedown', handleMouseDown)
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  function resetView() {
    setView(RESET_VIEW)
  }

  const { zoom, pan } = view
  let cursor: CSSProperties['cursor'] = 'zoom-in'
  if (zoom > 1) cursor = isDragging ? 'grabbing' : 'grab'

  return (
    <div ref={containerRef} style={containerStyle}>
      {url ? (
        <img
          // No `key={url}` here -- that forced a full unmount/remount of
          // this element on every timestamp change (including every step
          // of a slider drag), which blanks the container for a moment
          // since the old frame's pixels disappear immediately rather
          // than staying on screen until the new one is ready. Keeping
          // the same DOM node and just swapping `src` lets the browser's
          // normal image-swap behavior keep showing the previous frame
          // until the new one finishes loading, which turns slider
          // scrubbing from a flicker/blank flash into a smooth
          // transition. onLoad/onError still fire correctly on a plain
          // `src` change without needing `key`.
          src={url}
          alt={`${BAND_LABELS[band]} mosaic thumbnail`}
          draggable={false}
          style={{
            ...imgStyle,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            cursor,
          }}
          onLoad={() => {
            imageLoadedRef.current = true
          }}
          onError={() => {
            // A single missing/broken frame shouldn't stall animation
            // forever waiting for a load event that will never come.
            imageLoadedRef.current = true
          }}
          // Quick reset, a standard image-viewer convention -- otherwise
          // the only way back to 1x is scrolling out the same number of
          // ticks used to zoom in (or dragging back to center by hand).
          onDoubleClick={resetView}
        />
      ) : (
        <div style={messageStyle}>
          {catalogReady ? 'No thumbnail available for this timestamp' : 'Loading gallery catalog…'}
        </div>
      )}
    </div>
  )
}

export default ImageViewer
