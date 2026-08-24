import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), cesium()],
  server: {
    // Vite blocks unrecognized Host headers by default (DNS-rebinding
    // protection). If you deploy this behind a reverse proxy or tunnel
    // (e.g. a Cloudflare Tunnel) with its own hostname, add that
    // hostname here -- requests will otherwise arrive with that hostname
    // instead of localhost and get rejected.
    allowedHosts: ['geosat-mosaic-cesium.your-domain.example'],
  },
  preview: {
    // Same allowedHosts requirement as `server` above, but Vite keeps
    // dev/preview as separate allowlists. Preview (`vite build` +
    // `vite preview`) serves built/minified output on its own port,
    // distinct from the dev server's port, so both can run at once
    // without colliding -- pick whatever ports are free on your machine.
    allowedHosts: ['geosat-mosaic-cesium.your-domain.example'],
    port: 4173,
  },
})
