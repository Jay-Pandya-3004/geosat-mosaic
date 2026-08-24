import { useEffect, useState } from 'react'

// A JS viewport-width check, not just CSS -- needed anywhere a fixed-
// position element's *offset*, not just its size, has to change on a
// narrow screen (plain CSS clamp()/vw handles sizing fine, but can't move
// a sibling element down to make room). Tracks `resize`, not just mount,
// so rotating a phone or resizing a desktop window updates it live.
export function useIsNarrowViewport(breakpointPx = 560): boolean {
  const [isNarrow, setIsNarrow] = useState(() => window.innerWidth <= breakpointPx)

  useEffect(() => {
    const handleResize = () => setIsNarrow(window.innerWidth <= breakpointPx)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [breakpointPx])

  return isNarrow
}
