import type { ColorbarGroup } from './xcube'

export type RGB = [number, number, number]

// Each `previewPng` (from a /colorbars response, e.g. `gray`) is a small,
// fixed-size single-axis gradient raster (256x1 for `gray`) running from
// the colormap's t=0 color to its t=1 color.
// Sampling opposite corners covers both orientations without assuming
// which axis varies -- for a 1px-tall strip, (0,0) and (width-1,height-1)
// are just the left/right pixels; for a 1px-wide strip they'd be the top/
// bottom pixels, same idea either way.
function decodeEdgeColors(previewPngBase64: string): Promise<[RGB, RGB]> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      const canvas = document.createElement('canvas')
      canvas.width = image.naturalWidth
      canvas.height = image.naturalHeight
      const ctx = canvas.getContext('2d')
      if (!ctx) {
        reject(new Error('2D canvas context unavailable'))
        return
      }
      ctx.drawImage(image, 0, 0)
      const start = ctx.getImageData(0, 0, 1, 1).data
      const end = ctx.getImageData(canvas.width - 1, canvas.height - 1, 1, 1).data
      resolve([
        [start[0], start[1], start[2]],
        [end[0], end[1], end[2]],
      ])
    }
    image.onerror = () => reject(new Error('failed to decode colorbar preview'))
    image.src = `data:image/png;base64,${previewPngBase64}`
  })
}

// Memoized per palette name -- the two edge colors depend only on which
// colormap is selected (matplotlib always maps t=0/t=1 to the same colors
// regardless of vmin/vmax), so this is safe to cache indefinitely across
// band/range/timestamp changes, not just within one render.
const edgeColorCache = new Map<string, Promise<[RGB, RGB]>>()

// Used by transparentEdgesTileLoader.ts to know which two exact RGB
// values in a rendered mosaic tile represent "clipped to below vmin" /
// "clipped to above vmax" (xcube's tile endpoint has no query param for
// transparent-instead-of-opaque clipping, confirmed against its real
// OpenAPI spec -- see that file's own comment for the full reasoning).
// Returns `undefined` if `palette` isn't found in `colorbarGroups` yet
// (e.g. the one-time fetch hasn't resolved on first paint).
export function getPaletteEdgeColors(
  palette: string,
  colorbarGroups: ColorbarGroup[],
): Promise<[RGB, RGB]> | undefined {
  const cached = edgeColorCache.get(palette)
  if (cached) return cached
  for (const [, entries] of colorbarGroups) {
    const entry = entries.find((candidate) => candidate.name === palette)
    if (entry) {
      const promise = decodeEdgeColors(entry.previewPng)
      edgeColorCache.set(palette, promise)
      return promise
    }
  }
  return undefined
}
