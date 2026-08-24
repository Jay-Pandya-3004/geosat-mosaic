// Backend base URLs -- fixed Cloudflare Tunnel + custom-domain hostnames.
// Browser-trusted HTTPS certs via Cloudflare's edge. This replaces both:
//   - the old self-signed-cert xcube HTTP/2 proxy (port 8443) -- the
//     origin bug this domain was bought to fix: mobile browsers
//     silently failed to load xcube tiles/legend images over that
//     self-signed cert, with no "click to trust" prompt the way desktop
//     got.
//   - the direct-LAN-IP `backend-config.json` runtime-fetch mechanism
//     this file used to implement -- switched away from 2026-08-20 to
//     fix a second bug: a page loaded from this app's own public
//     domain making requests straight to a private LAN IP
//     (192.168.1.x) triggers Chrome's Private Network Access permission
//     prompt ("Access other device on your network"), confusing to a
//     visitor and silently breaking the app if they click Block.
//     Domain-to-domain avoids this entirely (Chrome sees public-to-public
//     traffic, no prompt).
// The RHEL-side IP-sync listener + backend-config.json are still running
// (not torn down), just no longer consumed by these apps -- a LAN-direct
// path could be wired back in later if ever wanted.
//
// A concurrent-tile-load browser test (30 simultaneous xcube tile
// fetches, fresh/uncached both sides) found no meaningful speed
// difference vs. the direct-LAN path (~11.2s vs ~10.8s) -- xcube's own
// bottleneck is its own server-side worker concurrency, not the
// network path, so this switch has no meaningful performance cost for
// tile-heavy usage. Single-shot calls (point-probe, timeseries) are
// meaningfully slower in relative terms through the tunnel (~70-130ms
// fixed edge overhead vs. single-digit-to-low-double-digit ms direct),
// though still well under human-perceptible thresholds for a one-off
// click.

export function getThreddsBaseUrl(): string {
  return 'https://thredds.your-domain.example'
}

export function getXcubeBaseUrl(): string {
  return 'https://xcube.your-domain.example'
}

export function getMetadataApiUrl(): string {
  return 'https://geosat-mosaic-api.your-domain.example'
}
