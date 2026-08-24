import type TileSource from 'ol/source/Tile'
import OSM from 'ol/source/OSM'
import XYZ from 'ol/source/XYZ'
import TileImage from 'ol/source/TileImage'
import TileGrid from 'ol/tilegrid/TileGrid'

// Same 4 openly-accessible basemap options geosat-mosaic-cesium's
// BasemapLayer.tsx offers. 'natural-earth' was originally skipped here --
// it was Cesium's own bundled offline imagery
// (`buildModuleUrl('Assets/Textures/NaturalEarthII')`), a Cesium-specific
// asset with no OpenLayers equivalent to port -- added later once the
// tiles were copied over locally (see below). OSM stays the default
// (unchanged, and it's what Pass 1 already used) -- this only adds the
// option, it doesn't change what's selected on load.
export type BasemapStyle = 'osm' | 'night-lights' | 'aerial' | 'natural-earth'

export const BASEMAP_STYLE_LABELS: Record<BasemapStyle, string> = {
  osm: 'OpenStreetMap',
  'night-lights': 'Night lights',
  aerial: 'Aerial imagery',
  'natural-earth': 'Natural Earth',
}

// The actual tile files (43 .jpg tiles, 536KB total) are copied verbatim
// from geosat-mosaic-cesium's own `node_modules/cesium/Source/Assets/
// Textures/NaturalEarthII` into this app's `public/assets/natural-earth-
// ii/` -- same duplicate-not-share convention this project already uses
// for everything else (lib files, coastline geojson, favicon), just for a
// binary asset this time instead of source code. It's CesiumJS's own
// bundled Natural Earth II basemap (public-domain source data, no
// license/attribution restriction), served as a plain TMS (TileMapService)
// pyramid -- see the copied `tilemapresource.xml` alongside it for the
// original metadata this was derived from.
//
// TMS's "geodetic" profile (confirmed from that XML: SRS=EPSG:4326,
// Origin -90/-180, TileFormat 256x256) starts at 2 columns x 1 row at
// zoom 0 (not 1x1 like a standard XYZ/EPSG:3857 pyramid) -- the same
// north/south-pole-avoiding 2:1 aspect doubling Cesium's own
// GeographicTilingScheme uses by default. `ol/tilegrid.createXYZ`'s
// default resolution math doesn't reproduce this (it picks a resolution
// dominated by the extent's *width*, which yields 1 column at z0 for a
// 360x180 extent, not 2) -- built manually instead, with resolutions
// taken directly from the tilemapresource.xml's own three
// `units-per-pixel` values so the z0/1/2 tile boundaries line up exactly
// with the real files on disk.
const NATURAL_EARTH_RESOLUTIONS = [0.703125, 0.3515625, 0.17578125]

const naturalEarthTileGrid = new TileGrid({
  extent: [-180, -90, 180, 90],
  origin: [-180, 90],
  resolutions: NATURAL_EARTH_RESOLUTIONS,
  tileSize: 256,
})

// TMS numbers tile rows south-to-north (row 0 = southernmost), but
// OpenLayers' own `TileGrid`/`tileUrlFunction` always numbers rows
// north-to-south regardless of which extent corner is passed as `origin`
// (per `TileGrid.js`'s own `getTileCoordForXYAndResolution_`, which
// computes `(origin[1] - y) / resolution / tileSize[1]` -- that's
// monotonically *decreasing* in y i.e. always north-at-row-0, no matter
// what origin you supply). So the row index has to be flipped by hand to
// find the matching file on disk; the column index needs no such flip
// since both conventions count west-to-east from the same edge. Row count
// at each zoom is `2^z` (1/2/4 for z 0/1/2, matching the directory
// listing: z0 has y files 0 only, z1 has 0-1, z2 has 0-3).
function naturalEarthTileUrlFunction(tileCoord: number[]): string {
  const [z, x, y] = tileCoord
  const rowCount = 2 ** z
  const tmsY = rowCount - 1 - y
  return `/assets/natural-earth-ii/${z}/${x}/${tmsY}.jpg`
}

function createNaturalEarthSource(): TileSource {
  return new TileImage({
    projection: 'EPSG:4326',
    tileGrid: naturalEarthTileGrid,
    tileUrlFunction: naturalEarthTileUrlFunction,
    attributions: 'Natural Earth, via CesiumJS',
  })
}

export function createBasemapSource(style: BasemapStyle): TileSource {
  switch (style) {
    case 'night-lights':
      // NASA EOSDIS GIBS, same VIIRS_CityLights_2012 layer/endpoint
      // geosat-mosaic-cesium's BasemapLayer.tsx uses -- EPSG:3857
      // "GoogleMapsCompatible_Level8" (an ordinary XYZ progression, see
      // that file's own comment on why the *EPSG4326* GIBS endpoint
      // was tried first and rejected: its TileMatrixSet isn't a clean
      // power-of-2 doubling, so it didn't line up under a tiling scheme
      // that assumes one). `{z}/{y}/{x}` here is GIBS' own
      // `{TileMatrix}/{TileRow}/{TileCol}`.
      return new XYZ({
        url: 'https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_CityLights_2012/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg',
        maxZoom: 8,
        attributions: 'NASA EOSDIS GIBS / VIIRS (NASA/NOAA)',
      })
    case 'aerial':
      // Esri's classic public ArcGIS Online REST endpoint (same free,
      // no-token host geosat-mosaic-cesium's BasemapLayer.tsx uses) --
      // ArcGIS's own standard tiled-MapServer URL pattern
      // (`.../MapServer/tile/{z}/{y}/{x}`), no separate provider class
      // needed the way Cesium's ArcGisMapServerImageryProvider.fromUrl
      // required (that fetches the service's own tile-scheme metadata
      // first; this REST pattern is documented/stable enough to build
      // directly).
      return new XYZ({
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attributions: 'Esri, Maxar, Earthstar Geographics',
      })
    case 'natural-earth':
      return createNaturalEarthSource()
    case 'osm':
    default:
      return new OSM()
  }
}
