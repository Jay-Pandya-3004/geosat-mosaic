// Real reimplementation, not a port -- geosat-mosaic-cesium's own
// lib/geometry.ts uses real Cesium types (Cartesian3/EllipsoidGeodesic/
// EllipsoidTangentPlane), not portable as-is. OpenLayers has its own
// direct equivalent (`ol/sphere`), and since this map's own coordinates
// are already plain `[lon, lat]` pairs (EPSG:4326, the view's own
// projection -- see Map.tsx's click handler), there's no Cartesian3-
// style conversion step needed at all here, unlike the Cesium version.
import { getArea, getDistance } from 'ol/sphere'
import Polygon from 'ol/geom/Polygon'

export type MeasureMode = 'off' | 'distance' | 'area'

export type LonLat = [number, number]

// Great-circle distance summed over consecutive point pairs, in meters.
// `ol/sphere`'s `getDistance` is a direct haversine great-circle
// calculation on a mean-Earth-radius sphere -- the same "great circle
// between two points" geodesic distance geosat-mosaic-cesium's
// `EllipsoidGeodesic` computes on the WGS84 ellipsoid instead; the two
// libraries' own reference shapes differ slightly (sphere vs ellipsoid),
// a real, expected, sub-0.5%-scale difference between them, not a bug.
export function totalDistanceMeters(points: LonLat[]): number {
  if (points.length < 2) return 0
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    total += getDistance(points[i], points[i + 1])
  }
  return total
}

// `ol/sphere`'s `getArea` defaults to assuming its input geometry's
// coordinates are in EPSG:3857 (Web Mercator) and transforms them to
// EPSG:4326 internally before computing -- explicitly overriding
// `projection: 'EPSG:4326'` here is load-bearing, not cosmetic: this
// map's points are already plain lon/lat, so without this override
// `getArea` would silently treat them as Web-Mercator meters and
// transform them a second time, producing a wildly wrong area.
export function polygonAreaSquareMeters(points: LonLat[]): number {
  if (points.length < 3) return 0
  const ring = [...points, points[0]]
  return getArea(new Polygon([ring]), { projection: 'EPSG:4326' })
}

export function formatDistance(meters: number): string {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`
  return `${meters.toFixed(0)} m`
}

export function formatArea(squareMeters: number): string {
  if (squareMeters >= 1_000_000) return `${(squareMeters / 1_000_000).toFixed(2)} km²`
  return `${squareMeters.toFixed(0)} m²`
}
