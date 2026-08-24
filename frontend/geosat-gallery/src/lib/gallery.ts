import { THREDDS_BASE_URL } from './thredds'

// Same 4 bands as GeoSat-MOSAIC (MIR/WV/TIR1/TIR2
// naming) -- this is a separate type, not imported from geosat-mosaic,
// since the two apps are deliberately separate projects: no shared package
// decided on yet.
export type GalleryBand = 'mir' | 'wv' | 'tir1' | 'tir2'

export const BAND_LABELS: Record<GalleryBand, string> = {
  mir: 'MIR',
  wv: 'WV',
  tir1: 'TIR1',
  tir2: 'TIR2',
}

export const ALL_BANDS: GalleryBand[] = ['mir', 'wv', 'tir1', 'tir2']

const GALLERY_BASE_URL = `${THREDDS_BASE_URL}/thredds/fileServer/global-mosaic-gallery`
const GALLERY_CATALOG_BASE_URL = `${THREDDS_BASE_URL}/thredds/catalog/global-mosaic-gallery`

function compactTimestamp(iso: string): string {
  return iso.replace(/[-:]/g, '').slice(0, 15)
}

// Per-band map from compact target-timestamp -> thumbnail URL, keyed off
// the `catalog.xml` datasetScan listing for that band (not per-timestamp
// calls to geosat-mosaic-api's /satellite-metadata, which GeoSat-MOSAIC's
// info button uses for the same actual-mid-timestamp need -- that backend
// does a raw netCDF4 read per call and is slow, ~13-25s for a 96-file
// /timeseries request; nowhere near fast enough to resolve a URL per
// animation frame).
// The datasetScan catalog instead lists every file directly, with both
// the target and actual-mid timestamps already embedded in the filename
// -- one ~70KB XML fetch per band gets every thumbnail URL this app could
// ever need, cached after first use. The gallery thumbnail public URL
// filename convention this parses is: "<band>_<target>_<actual-mid>.png".
export type GalleryCatalog = Map<string, string>

const FILENAME_RE = /^([a-z0-9]+)_(\d{8}T\d{6})_(\d{8}T\d{6})\.png$/

export async function fetchGalleryCatalog(band: GalleryBand): Promise<GalleryCatalog> {
  const response = await fetch(`${GALLERY_CATALOG_BASE_URL}/${band}/catalog.xml`)
  const xml = await response.text()
  const doc = new DOMParser().parseFromString(xml, 'application/xml')
  const catalog: GalleryCatalog = new Map()
  for (const dataset of Array.from(doc.getElementsByTagName('dataset'))) {
    const name = dataset.getAttribute('name')
    if (!name) continue
    const match = FILENAME_RE.exec(name)
    if (!match) continue
    const [, , target] = match
    catalog.set(target, `${GALLERY_BASE_URL}/${band}/${name}`)
  }
  return catalog
}

// Resolves a thumbnail URL for `targetTime` (one of this app's own
// authoritative ISO timestamps) out of an already-fetched catalog.
// Undefined if the gallery pipeline never wrote a PNG for this exact
// timestamp+band -- a real possibility, since the gallery PNGs and the
// WMS aggregation are two separate outputs of the same pipeline run, not
// guaranteed 1:1 forever even though counts matched at the time this was
// built (see thredds.ts's own note).
export function resolveThumbnailUrl(
  catalog: GalleryCatalog,
  targetTime: string,
): string | undefined {
  return catalog.get(compactTimestamp(targetTime))
}
