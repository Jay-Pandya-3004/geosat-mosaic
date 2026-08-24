// Pure geodesic math for the measurement tools -- measure distance and
// calculate area: standard Cesium
// geometry/math, no backend involvement.
import { Cartesian3, Cartographic, Ellipsoid, EllipsoidGeodesic, EllipsoidTangentPlane } from 'cesium'

export type MeasureMode = 'off' | 'distance' | 'area'

// Great-circle distance summed over consecutive point pairs, in meters.
// `EllipsoidGeodesic` (not a straight-line Cartesian3.distance, which
// would cut through the earth) is Cesium's own geodesic-surface-distance
// primitive -- exactly what "great-circle distance between points" means.
export function totalDistanceMeters(points: Cartesian3[]): number {
  if (points.length < 2) return 0
  let total = 0
  for (let i = 0; i < points.length - 1; i++) {
    const start = Cartographic.fromCartesian(points[i])
    const end = Cartographic.fromCartesian(points[i + 1])
    total += new EllipsoidGeodesic(start, end).surfaceDistance
  }
  return total
}

// Approximate geodesic polygon area in square meters, via the standard
// Cesium technique: project onto the polygon's own local tangent plane
// (flattens the small curvature error for a polygon of reasonable size),
// then a plain 2-D shoelace formula on the projected points. Cesium has
// no built-in geodesic polygon area function -- this is the same
// approach used in Cesium's own Sandcastle measurement examples.
export function polygonAreaSquareMeters(points: Cartesian3[]): number {
  if (points.length < 3) return 0
  const tangentPlane = EllipsoidTangentPlane.fromPoints(points, Ellipsoid.WGS84)
  const projected = tangentPlane.projectPointsOntoPlane(points)
  let area = 0
  for (let i = 0; i < projected.length; i++) {
    const p1 = projected[i]
    const p2 = projected[(i + 1) % projected.length]
    area += p1.x * p2.y - p2.x * p1.y
  }
  return Math.abs(area) / 2
}

export function formatDistance(meters: number): string {
  if (meters >= 1000) return `${(meters / 1000).toFixed(2)} km`
  return `${meters.toFixed(0)} m`
}

export function formatArea(squareMeters: number): string {
  if (squareMeters >= 1_000_000) return `${(squareMeters / 1_000_000).toFixed(2)} km²`
  return `${squareMeters.toFixed(0)} m²`
}
