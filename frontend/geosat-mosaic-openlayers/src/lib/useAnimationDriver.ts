import { useEffect, useRef, type RefObject } from 'react'

// Same rAF-paced, load-gated approach as geosat-mosaic's
// AnimationDriver.tsx (see that file's own comment for the reasoning: a
// naive fixed-interval timer swaps the displayed frame regardless of
// whether the *previous* frame actually finished loading, which queues
// overlapping bursts of image requests during fast playback). No Cesium
// `globe.tilesLoaded` equivalent here -- gated on `imageLoadedRef` instead,
// flipped by ImageViewer's onLoad/onError handlers. `intervalMs` still acts
// as a pacing ceiling, same as there.
export function useAnimationDriver(
  playing: boolean,
  intervalMs: number,
  imageLoadedRef: RefObject<boolean>,
  onAdvance: () => void,
) {
  const lastAdvanceRef = useRef(0)

  useEffect(() => {
    if (!playing) return
    lastAdvanceRef.current = Date.now()
    let frameId: number

    const tick = () => {
      const now = Date.now()
      if (imageLoadedRef.current && now - lastAdvanceRef.current >= intervalMs) {
        lastAdvanceRef.current = now
        onAdvance()
      }
      frameId = requestAnimationFrame(tick)
    }
    frameId = requestAnimationFrame(tick)

    return () => cancelAnimationFrame(frameId)
  }, [playing, intervalMs, imageLoadedRef, onAdvance])
}
