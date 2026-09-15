import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The built app is served directly by pipeline_stream_server.py from web/dist,
// so we use a relative base and only need the dev proxy for `npm run dev`.
export default defineConfig({
  plugins: [react()],
  base: './',
  build: { outDir: 'dist' },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/health': 'http://127.0.0.1:8765',
    },
  },
})
