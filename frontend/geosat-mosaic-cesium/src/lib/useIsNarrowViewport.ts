import { useEffect, useState } from 'react'

// A viewport-width check (not just CSS) -- needed anywhere a panel's
// *default state*, not just its size, should differ on a narrow screen
// (plain CSS min()/vw handles sizing fine, but can't change a React
// default-collapsed prop). Tracks `resize`, not just mount, so rotating a
// phone or resizing a desktop window updates it live. Same pattern
// already used in the sibling GeoSat-Gallery project
// (useIsNarrowViewport.ts there).
//
// Two signals combined, not just width:
// - `min(innerWidth, innerHeight)` instead of `innerWidth` alone -- a
//   phone's *short* side stays roughly constant across rotation (e.g.
//   ~400px either way), but its width alone doesn't: rotating to
//   landscape pushes width well past the breakpoint while height drops
//   by the same amount, which made this hook flip to "not narrow" on
//   rotation even though the actual usable screen is exactly as small,
//   just reshaped.
// - `(pointer: coarse)` (touch-primary input) -- the width/height check
//   alone can't tell "a real small phone" apart from "a desktop window
//   the user happened to resize narrow", and this app's own panels are
//   explicitly meant to only change behavior for the former (see e.g.
//   LayerSwitcher.tsx's own comment: "a real phone-width screen, not a
//   smallish desktop window"). A touch-primary pointer is a device-
//   capability signal that doesn't depend on current window size/
//   orientation at all, so combining it with the size check (AND, not
//   OR) means neither a narrow-but-mouse-driven desktop window nor a
//   wide-but-touch phone-in-landscape gets misclassified.
function computeIsNarrow(breakpointPx: number): boolean {
  const shortSide = Math.min(window.innerWidth, window.innerHeight)
  const isTouchPrimary = window.matchMedia('(pointer: coarse)').matches
  return isTouchPrimary && shortSide <= breakpointPx
}

export function useIsNarrowViewport(breakpointPx = 480): boolean {
  const [isNarrow, setIsNarrow] = useState(() => computeIsNarrow(breakpointPx))

  useEffect(() => {
    const recompute = () => setIsNarrow(computeIsNarrow(breakpointPx))
    window.addEventListener('resize', recompute)
    // Covers the (rare, but real -- e.g. a mouse plugged into a tablet)
    // case where pointer capability itself changes without a resize.
    const pointerQuery = window.matchMedia('(pointer: coarse)')
    pointerQuery.addEventListener('change', recompute)
    return () => {
      window.removeEventListener('resize', recompute)
      pointerQuery.removeEventListener('change', recompute)
    }
  }, [breakpointPx])

  return isNarrow
}
