// Backend base URLs -- fixed Cloudflare Tunnel + custom-domain hostnames.
// Browser-trusted HTTPS certs via Cloudflare's edge. This replaces the
// direct-LAN-IP `backend-config.json` runtime-fetch mechanism this file
// used to implement -- switched away from on 2026-08-20 to fix a bug: a
// page loaded from this app's own public domain making requests straight
// to a private LAN IP (192.168.1.x) triggers Chrome's Private Network
// Access permission prompt ("Access other device on your network"),
// confusing to visitors and silently breaking the app if they click
// Block. Domain-to-domain avoids this entirely (Chrome sees
// public-to-public traffic, no prompt).
// The RHEL-side IP-sync listener + backend-config.json are still running
// (not torn down), just no longer consumed by these apps -- a LAN-direct
// path could be wired back in later if ever wanted.
//
// No xcube entry here -- this app never calls xcube (see thredds.ts /
// .env history), unlike the Cesium/OpenLayers sibling apps.

export function getThreddsBaseUrl(): string {
  return 'https://thredds.your-domain.example'
}

export function getMetadataApiUrl(): string {
  return 'https://geosat-mosaic-api.your-domain.example'
}
