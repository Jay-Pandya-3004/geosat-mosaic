// Nominatim place search
// with zoom/fly-to-location. Public OpenStreetMap instance, no API key --
// its usage policy (https://operations.osmfoundation.org/policies/nominatim/)
// asks for at most ~1 request/sec and a way to identify the client; this
// app only searches on explicit submit (not per-keystroke), and the
// browser's own Referer header identifies the request (a custom
// User-Agent can't be set from browser fetch -- it's a forbidden header).
const NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'

export interface PlaceSearchResult {
  displayName: string
  lat: number
  lon: number
}

interface NominatimEntry {
  display_name: string
  lat: string
  lon: string
}

export async function fetchPlaceSearch(query: string): Promise<PlaceSearchResult[]> {
  const url = new URL(NOMINATIM_URL)
  url.searchParams.set('q', query)
  url.searchParams.set('format', 'jsonv2')
  url.searchParams.set('limit', '5')

  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    throw new Error(`Place search failed (${response.status})`)
  }
  const data: NominatimEntry[] = await response.json()
  return data.map((entry) => ({
    displayName: entry.display_name,
    lat: Number(entry.lat),
    lon: Number(entry.lon),
  }))
}
