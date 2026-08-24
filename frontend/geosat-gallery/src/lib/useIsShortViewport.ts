import { useEffect, useState } from 'react'

// Viewport-height check, independent of useIsNarrowViewport.ts's own
// width-only check -- a phone in landscape can easily be *wide* (well
// over that hook's 560px breakpoint) while still being short (~360-430px
// tall), so width alone can't detect "landscape phone" on its own. Same
// resize-tracking shape as useIsNarrowViewport.ts (rotating the phone
// updates this live, not just on mount).
export function useIsShortViewport(breakpointPx = 500): boolean {
  const [isShort, setIsShort] = useState(() => window.innerHeight <= breakpointPx)

  useEffect(() => {
    const handleResize = () => setIsShort(window.innerHeight <= breakpointPx)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [breakpointPx])

  return isShort
}
