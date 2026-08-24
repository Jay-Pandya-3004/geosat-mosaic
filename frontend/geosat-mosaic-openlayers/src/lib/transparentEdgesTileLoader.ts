import TileState from 'ol/TileState'
import type Tile from 'ol/Tile'
import type ImageTile from 'ol/ImageTile'
import type { RGB } from './paletteEdgeColors'

function matchesColor(data: Uint8ClampedArray, offset: number, color: RGB): boolean {
  return data[offset] === color[0] && data[offset + 1] === color[1] && data[offset + 2] === color[2]
}

// Accepted limitation (same spirit as this project's other documented
// tradeoffs, not hidden): a genuine data pixel that lands exactly at
// colorMin/colorMax -- most likely right after the reset-range button,
// which sets them to the actual live min/max -- renders the identical
// color to a clipped one and gets made transparent too, indistinguishable
// from the outside. Accepted because there's no raw-value data in a
// rendered PNG tile to tell the two apart, only pixel color.
//
// Per OL's own documented custom-tileLoadFunction pattern (see ol/Tile.js's
// module doc): fetch the tile as a blob, decode it, rewrite matching
// pixels' alpha to 0, then set `tile.getImage().src` to the result --
// image `load`/`error` listeners already wired up by ImageTile itself
// (see ImageTile.js's own `load()`), no manual `setState(LOADED)` needed
// on the success path, only on failure.
export function createTransparentEdgesTileLoader(
  getEdgeColors: () => Promise<[RGB, RGB]> | undefined,
) {
  return (tile: Tile, src: string) => {
    const image = (tile as ImageTile).getImage() as HTMLImageElement
    const edgeColorsPromise = getEdgeColors()
    if (!edgeColorsPromise) {
      // Palette list hasn't loaded yet (brief startup window) -- fall back
      // to xcube's own opaque-clip rendering rather than blocking the tile
      // forever.
      image.src = src
      return
    }
    Promise.all([fetch(src).then((response) => response.blob()), edgeColorsPromise])
      .then(([blob, edgeColors]) => Promise.all([createImageBitmap(blob), edgeColors] as const))
      .then(([bitmap, [colorA, colorB]]) => {
        const canvas = document.createElement('canvas')
        canvas.width = bitmap.width
        canvas.height = bitmap.height
        const ctx = canvas.getContext('2d')
        if (!ctx) {
          image.src = src
          return
        }
        ctx.drawImage(bitmap, 0, 0)
        const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height)
        const data = imageData.data
        for (let i = 0; i < data.length; i += 4) {
          if (matchesColor(data, i, colorA) || matchesColor(data, i, colorB)) {
            // Zero RGB too, not just alpha -- see geosat-mosaic-cesium's
            // own version of this fix for why (a GPU-texture-filtering
            // color-fringe bug there): a transparent texel that still
            // carries the palette's opaque edge color
            // can bleed that color into neighboring visible pixels under
            // magnification. OL's 2D canvas compositing doesn't show this
            // the same way Cesium's WebGL texture sampling does, but
            // zeroing RGB here too is strictly more correct and costs
            // nothing.
            data[i] = 0
            data[i + 1] = 0
            data[i + 2] = 0
            data[i + 3] = 0
          }
        }
        ctx.putImageData(imageData, 0, 0)
        canvas.toBlob((outBlob) => {
          if (!outBlob) {
            image.src = src
            return
          }
          const objectUrl = URL.createObjectURL(outBlob)
          image.src = objectUrl
          setTimeout(() => URL.revokeObjectURL(objectUrl), 5000)
        })
      })
      .catch(() => {
        tile.setState(TileState.ERROR)
      })
  }
}
