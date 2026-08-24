import { useMemo } from 'react'
import { Entity } from 'resium'
import { Cartesian3, Color } from 'cesium'

// Fixed 30-degree spacing -- doc scope is "Graticule toggle (on/off)",
// not an adjustable interval, so this is a single fixed grid, not a
// user-configurable one.
const GRATICULE_STEP_DEGREES = 30
// How finely each line is sampled. Meridians are themselves great
// circles, so Cesium's straight-chord polyline segments would look
// smooth even at a coarser step; parallels other than the equator are
// small circles, which need finer sampling to not look faceted -- one
// shared step fine enough for both rather than two separate constants.
const SAMPLE_STEP_DEGREES = 5

function meridianPositions(longitude: number): Cartesian3[] {
  const positions: Cartesian3[] = []
  for (let lat = -90; lat <= 90; lat += SAMPLE_STEP_DEGREES) {
    positions.push(Cartesian3.fromDegrees(longitude, lat))
  }
  return positions
}

function parallelPositions(latitude: number): Cartesian3[] {
  const positions: Cartesian3[] = []
  for (let lon = -180; lon <= 180; lon += SAMPLE_STEP_DEGREES) {
    positions.push(Cartesian3.fromDegrees(lon, latitude))
  }
  return positions
}

const LINE_COLOR = Color.WHITE.withAlpha(0.35)

interface GraticuleProps {
  show: boolean
}

// Lat/lon grid overlay -- graticule toggle (on/off). Deliberately NOT Cesium's built-in
// GridImageryProvider: per its own docs ("draws a wireframe grid on
// every tile", "useful for... debugging terrain"), it subdivides each
// *tile* into a fixed cell count, not fixed lat/lon degree intervals, so
// its line spacing would visibly shift as you zoom rather than staying
// at consistent degree lines -- not a true geographic graticule. Built
// directly instead: one polyline per
// meridian/parallel at a fixed 30-degree spacing, sampled finely enough
// (parallels other than the equator are small circles, not great
// circles) to render smoothly.
function Graticule({ show }: GraticuleProps) {
  const meridians = useMemo(() => {
    const longitudes: number[] = []
    for (let lon = -180; lon < 180; lon += GRATICULE_STEP_DEGREES) {
      longitudes.push(lon)
    }
    return longitudes
  }, [])

  const parallels = useMemo(() => {
    const latitudes: number[] = []
    for (let lat = -60; lat <= 60; lat += GRATICULE_STEP_DEGREES) {
      latitudes.push(lat)
    }
    return latitudes
  }, [])

  if (!show) return null

  return (
    <>
      {meridians.map((longitude) => (
        <Entity
          key={`meridian-${longitude}`}
          polyline={{ positions: meridianPositions(longitude), width: 1, material: LINE_COLOR }}
        />
      ))}
      {parallels.map((latitude) => (
        <Entity
          key={`parallel-${latitude}`}
          polyline={{ positions: parallelPositions(latitude), width: 1, material: LINE_COLOR }}
        />
      ))}
    </>
  )
}

export default Graticule
