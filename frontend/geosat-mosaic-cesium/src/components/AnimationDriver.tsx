import { useEffect, useRef } from 'react'
import { useCesium } from 'resium'

interface AnimationDriverProps {
  playing: boolean
  intervalMs: number
  onAdvance: () => void
}

// Drives animation stepping from inside the Cesium scene graph (needs
// useCesium's context, so this has to be a child of <Viewer>, not a plain
// setInterval in Globe.tsx). Renders nothing -- pure side effect.
//
// Why this exists: a naive fixed-interval timer (the original
// implementation) swaps MosaicLayer/ContributionMaskLayer's WMS provider
// every tick regardless of whether the *previous* frame's tiles have
// finished loading. A single realistic (256x256, quarter-globe) GetMap
// tile is consistently fast on its own (~0.85s via curl, no meaningful
// cold/warm difference) -- the multi-second blank-globe effect
// during fast playback wasn't backend latency, it was overlapping bursts
// of tile requests queueing behind the browser's per-origin connection
// limit because a new burst fired before the last one settled. Gating
// advancement on `globe.tilesLoaded` keeps requests from overlapping;
// `intervalMs` (the speed control) still acts as a ceiling on pace, so a
// fast backend/cache-warm run still respects the user's chosen speed
// instead of free-running as fast as tiles happen to load.
function AnimationDriver({ playing, intervalMs, onAdvance }: AnimationDriverProps) {
  const { globe } = useCesium()
  const lastAdvanceRef = useRef(0)

  useEffect(() => {
    if (!playing) return
    lastAdvanceRef.current = Date.now()
    let frameId: number

    const tick = () => {
      const now = Date.now()
      const tilesReady = globe ? globe.tilesLoaded : true
      if (tilesReady && now - lastAdvanceRef.current >= intervalMs) {
        lastAdvanceRef.current = now
        onAdvance()
      }
      frameId = requestAnimationFrame(tick)
    }
    frameId = requestAnimationFrame(tick)

    return () => cancelAnimationFrame(frameId)
  }, [playing, intervalMs, globe, onAdvance])

  return null
}

export default AnimationDriver
