import TileGrid from 'ol/tilegrid/TileGrid'
import { WORLD_EXTENT } from './coverage'
import { XCUBE_MAX_TILE_LEVEL } from './xcube'

const TILE_SIZE = 256

// xcube's tile endpoint (crs=CRS84, see lib/xcube.ts's xcubeTileUrlTemplate)
// follows the standard "geographic"/TMS-geographic tiling convention --
// the same one geosat-mosaic-cesium's Cesium GeographicTilingScheme
// already assumes for this exact backend (2 tiles wide x 1 tile tall at
// z=0, doubling each level, origin at the NW corner, y increasing
// downward -- the same y-down convention ordinary XYZ/slippy tiles use,
// not TMS's y-up). OpenLayers has no built-in helper for this scheme
// (`ol/tilegrid.createXYZ` is Web-Mercator-only), so it's built by hand
// here: at level z there are `2 * 2^z` tile columns spanning 360deg, so
// each tile is `360 / (2 * 2^z)` degrees wide, and the resolution
// (degrees per pixel) is that width divided by the tile size.
let cachedGrid: TileGrid | null = null

export function xcubeGeographicTileGrid(): TileGrid {
  if (cachedGrid) return cachedGrid
  const resolutions: number[] = []
  for (let z = 0; z <= XCUBE_MAX_TILE_LEVEL; z++) {
    resolutions.push(360 / (2 * 2 ** z * TILE_SIZE))
  }
  cachedGrid = new TileGrid({
    extent: WORLD_EXTENT,
    origin: [WORLD_EXTENT[0], WORLD_EXTENT[3]],
    resolutions,
    tileSize: TILE_SIZE,
  })
  return cachedGrid
}
