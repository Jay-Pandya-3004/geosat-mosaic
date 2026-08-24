import { useEffect, useRef, useState } from 'react'
import { Color, ColorMaterialProperty, type GeoJsonDataSource as CesiumGeoJsonDataSource } from 'cesium'
import { GeoJsonDataSource } from 'resium'

// Natural Earth 50m coastline, self-hosted -- same Natural Earth lineage the backend's
// own gallery-thumbnail previews already use via cartopy, just consumed as
// GeoJSON here instead of rasterized. Coastlines only, no political
// boundaries, matching the backend's own rationale.
const COASTLINE_URL = '/assets/ne_50m_coastline.geojson'

// Module-scope, not inline in JSX -- `stroke`/`fill` are read-only Cesium
// props to resium's GeoJsonDataSource, so passing a freshly-constructed
// Color object every render (e.g. `Color.WHITE.withAlpha(0.8)` inline)
// would look like a changed prop on every re-render and force resium to
// dispose and reload the entire ~1400-feature dataset each time. A stable
// module-level reference means it's only ever passed once.
const DEFAULT_STROKE = Color.WHITE.withAlpha(0.8)

interface CoastlineLayerProps {
  show?: boolean
  opacity?: number
}

// Coastline vector overlay -- second of the three (now four) base layers in the switcher.
function CoastlineLayer({ show = true, opacity = 0.8 }: CoastlineLayerProps) {
  const dataSourceRef = useRef<CesiumGeoJsonDataSource | null>(null)
  const [loaded, setLoaded] = useState(false)

  // Opacity is applied by rewriting each entity's polyline material
  // in-place rather than by threading it through the `stroke` prop --
  // `stroke` is read-only (see DEFAULT_STROKE comment above), so changing
  // it on every slider drag would reload the whole dataset instead of just
  // recoloring it.
  useEffect(() => {
    const dataSource = dataSourceRef.current
    if (!dataSource) return
    const color = Color.WHITE.withAlpha(opacity)
    for (const entity of dataSource.entities.values) {
      if (entity.polyline) {
        entity.polyline.material = new ColorMaterialProperty(color)
      }
    }
  }, [opacity, loaded])

  return (
    <GeoJsonDataSource
      data={COASTLINE_URL}
      show={show}
      stroke={DEFAULT_STROKE}
      strokeWidth={1}
      fill={Color.TRANSPARENT}
      clampToGround={false}
      onLoad={(dataSource) => {
        dataSourceRef.current = dataSource
        setLoaded(true)
      }}
    />
  )
}

export default CoastlineLayer
