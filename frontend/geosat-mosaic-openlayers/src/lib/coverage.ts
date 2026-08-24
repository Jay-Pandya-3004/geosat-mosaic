// Northern/southern edge of every mosaic file. Nothing exists past this, so
// don't let the mosaic tile layer request tiles there. Same value as
// geosat-mosaic-cesium's own COVERAGE_RECTANGLE (MosaicLayer.tsx).
export const COVERAGE_EXTENT: [number, number, number, number] = [
  -180, -66.49102, 180, 66.49102,
]

// Full-globe extent, EPSG:4326 (lon, lat).
export const WORLD_EXTENT: [number, number, number, number] = [-180, -90, 180, 90]
