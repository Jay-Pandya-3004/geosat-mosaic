// Default scope for the "View timeseries" button's initial fetch. Kept in
// its own file (not inline in Globe.tsx) specifically so it's a single
// obvious place to flip -- edit the two values below to change the
// default behavior here AND in the sibling GeoSat-MOSAIC-OpenLayers app's
// own copy of this file (no shared package between these apps -- see
// lib/gallery.ts's own note on why GeoSat-Gallery stays separate too, same
// reasoning applies between these two).
//
// This only controls the TIMESERIES request's default range, not the map's
// own animation/scrubber window (that stays its own separate 96-timestamp
// default with calendar-driven backward extension, unrelated to this).
//
// 'window': fetch only the most recent TIMESERIES_DEFAULT_WINDOW_SIZE
// timestamps by default -- fast (~13-25s for 96 files on this backend's
// sequential /timeseries endpoint). The user can still opt into the full
// range via TimeseriesPanel's "Load full history" button.
// 'full': fetch every available timestamp by default -- can take
// significantly longer as the real retention window grows, since the
// backend processes each file sequentially (see geosat-mosaic-api/main.py's
// own notes on why it can't be threaded).
export const TIMESERIES_DEFAULT_MODE: 'window' | 'full' = 'window'

// Only used when TIMESERIES_DEFAULT_MODE is 'window'.
export const TIMESERIES_DEFAULT_WINDOW_SIZE = 96
