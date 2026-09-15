import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// Lets the dashboard start the pipeline stream server again after it has been
// stopped.  The pipeline server itself can't do this (it's the thing being
// started), so the Vite dev server (always up on :5173) acts as the control
// channel.
function controlPlugin() {
  return {
    name: 'midway-server-control',
    configureServer(server) {
      server.middlewares.use('/__control/start', (req, res) => {
        if (req.method !== 'POST') {
          res.statusCode = 405
          res.end('POST only')
          return
        }
        try {
          const child = spawn('python', ['pipeline_stream_server.py'], {
            cwd: REPO_ROOT,
            detached: true,
            stdio: 'ignore',
            windowsHide: true,
          })
          child.unref()
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ started: true, pid: child.pid }))
        } catch (e) {
          res.statusCode = 500
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify({ error: String(e) }))
        }
      })
    },
  }
}

// The built app is served directly by pipeline_stream_server.py from web/dist,
// so we use a relative base and only need the dev proxy for `npm run dev`.
export default defineConfig({
  plugins: [react(), controlPlugin()],
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
