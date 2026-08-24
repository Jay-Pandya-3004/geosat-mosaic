import { useMemo } from 'react'
import { ImageryLayer } from 'resium'
import {
  ArcGisMapServerImageryProvider,
  buildModuleUrl,
  OpenStreetMapImageryProvider,
  Rectangle,
  TileMapServiceImageryProvider,
  WebMapTileServiceImageryProvider,
} from 'cesium'

// User-selectable basemap. All 4 are openly accessible -- no API key/Ion
// token required, same "no Ion account/token" constraint the rest of this
// app already follows (see Globe.tsx's terrain/imagery comments).
export type BasemapStyle = 'natural-earth' | 'osm' | 'night-lights' | 'aerial'

export const BASEMAP_STYLE_LABELS: Record<BasemapStyle, string> = {
  'natural-earth': 'Natural Earth',
  osm: 'OpenStreetMap',
  'night-lights': 'Night lights',
  aerial: 'Aerial imagery',
}

interface BasemapLayerProps {
  style?: BasemapStyle
  alpha?: number
  show?: boolean
}

// Base layer, offering multiple openly-accessible basemap
// options (default Natural Earth, plus OSM/night-lights/aerial). This
// used to be Viewer's own dedicated `baseLayer` prop (a Cesium read-only
// prop -- changing it recreates the *entire* Viewer, per
// the "Viewer is recreated..." console warning seen repeatedly
// whenever an unrelated prop changed and cascaded into it) --
// moved to a plain `<ImageryLayer index={0}>` like the other layers so
// switching basemaps only recreates this one layer, self-healing back to
// index 0 the same way MosaicLayer/ContributionMaskLayer already do.
function BasemapLayer({ style = 'natural-earth', alpha = 1, show = true }: BasemapLayerProps) {
  const provider = useMemo(() => {
    switch (style) {
      case 'osm':
        // The public OSM tile server -- same free, no-key endpoint OSM's
        // own website uses, subject to their usage policy (light,
        // non-bulk use only).
        return new OpenStreetMapImageryProvider({
          url: 'https://tile.openstreetmap.org/',
          credit: '© OpenStreetMap contributors',
        })
      case 'night-lights':
        // NASA EOSDIS GIBS, a free/open WMTS service, no key needed.
        // VIIRS (Visible Infrared Imaging Radiometer Suite) is a joint
        // NASA/NOAA instrument on the Suomi NPP satellite (a joint NASA-
        // NOAA-DoD mission) -- VIIRS_CityLights_2012 is GIBS' own
        // processed "Black Marble"-style night-lights composite from that
        // instrument's data, so this genuinely is NASA+NOAA-sourced, not
        // a third party.
        //
        // Alignment bug: the first version of this used
        // GIBS' EPSG4326 endpoint with Cesium's `GeographicTilingScheme`,
        // and the resulting basemap didn't line up with the coastline
        // layer. Fetching GIBS' own GetCapabilities directly showed
        // the *cause* was GIBS' EPSG4326
        // TileMatrixSet using a non-power-of-2 level progression (level
        // 0/1/2 have 2/3/5 tile columns) while Cesium's
        // `GeographicTilingScheme` always assumes exact power-of-2
        // doubling per level -- the two disagree above level 0, so tiles
        // landed at the wrong geographic bounds. Fixed by switching to
        // GIBS' EPSG3857 (Web Mercator) "GoogleMapsCompatible_Level8"
        // endpoint instead, which -- also confirmed directly from its own
        // TileMatrixSet definition -- *is* the standard slippy-map
        // power-of-2 progression (256x256 tiles, 1x1 at level 0, 2x2 at
        // level 1, etc.), exactly what Cesium's default
        // `WebMercatorTilingScheme` (used automatically here since no
        // `tilingScheme` override is passed) already assumes -- no custom
        // tiling-scheme math needed at all for this one.
        return new WebMapTileServiceImageryProvider({
          url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_CityLights_2012/default/GoogleMapsCompatible_Level8/{TileMatrix}/{TileRow}/{TileCol}.jpeg',
          layer: 'VIIRS_CityLights_2012',
          style: 'default',
          format: 'image/jpeg',
          tileMatrixSetID: 'GoogleMapsCompatible_Level8',
          maximumLevel: 8,
          // Web Mercator can't represent the poles -- same latitude
          // cap Web Mercator basemaps always use (matches this project's
          // own earlier finding when this exact bound was first derived).
          rectangle: Rectangle.fromDegrees(-180.0, -85.0511287794, 180.0, 85.0511287794),
          credit: 'NASA EOSDIS GIBS / VIIRS (NASA/NOAA)',
        })
      case 'aerial':
        // Esri's classic public ArcGIS Online REST endpoint
        // (server.arcgisonline.com) -- free, no developer account/token,
        // distinct from the newer ibasemaps-api.arcgis.com host Cesium's
        // own ArcGisBaseMapType.fromBasemapType() examples use, which
        // does require an ArcGIS access token.
        return ArcGisMapServerImageryProvider.fromUrl(
          'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer',
          { credit: 'Esri, Maxar, Earthstar Geographics' },
        )
      case 'natural-earth':
      default:
        // CesiumJS's own bundled offline imagery -- no network request at
        // all, so this is also the safe default if the other 3 (all
        // network calls to third-party services) are ever unreachable.
        return TileMapServiceImageryProvider.fromUrl(
          buildModuleUrl('Assets/Textures/NaturalEarthII'),
          { credit: 'Natural Earth, via CesiumJS' },
        )
    }
  }, [style])

  return <ImageryLayer imageryProvider={provider} alpha={alpha} show={show} index={0} />
}

export default BasemapLayer
