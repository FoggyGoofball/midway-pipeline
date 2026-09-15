import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { spawn, execFile } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

// Ollama lives on the Steam Deck (LAN), not on the machine running the server.
// Read the same constant the Python side uses so both probes stay in sync.
function readOllamaHost() {
  try {
    const cfg = fs.readFileSync(path.join(REPO_ROOT, 'ollama_config.py'), 'utf8')
    const m = cfg.match(/OLLAMA_HOST[^\n]*["']([^"']+)["']/)
    if (m) return m[1]
  } catch { /* fall through */ }
  return process.env.OLLAMA_HOST || 'http://127.0.0.1:11434'
}
const OLLAMA_HOST = readOllamaHost()

function sendJson(res, code, obj) {
  res.statusCode = code
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify(obj))
}

function methodOnly(req, res, method) {
  if (req.method !== method) {
    sendJson(res, 405, { error: `${method} only` })
    return false
  }
  return true
}

async function jsonFetch(url, ms = 2500) {
  const ctrl = new AbortController()
  const to = setTimeout(() => ctrl.abort(), ms)
  try {
    const r = await fetch(url, { signal: ctrl.signal })
    if (!r.ok) throw new Error(`HTTP ${r.status}`)
    return await r.json()
  } finally {
    clearTimeout(to)
  }
}

function shortReason(e) {
  return String((e && e.message) || e).slice(0, 300)
}

// Mirrors server_status.probe_ollama(): /api/version then /api/ps, never
// throws, bounded by a timeout so a sleeping Steam Deck can't wedge the page.
async function probeOllama() {
  const result = {
    host: OLLAMA_HOST,
    reachable: false,
    version: null,
    models: [],
    error: null,
    models_error: null,
  }
  try {
    const v = await jsonFetch(`${OLLAMA_HOST}/api/version`)
    result.version = v.version
    result.reachable = true
  } catch (e) {
    result.error = shortReason(e)
    return result
  }
  try {
    const ps = await jsonFetch(`${OLLAMA_HOST}/api/ps`)
    const now = Date.now()
    result.models = (ps.models || []).map((m) => {
      let ttl_s = null
      if (m.expires_at) {
        const dt = Date.parse(m.expires_at)
        if (!Number.isNaN(dt)) ttl_s = Math.round((dt - now) / 1000)
      }
      return { name: m.name || '?', size: m.size, size_vram: m.size_vram, ttl_s }
    })
  } catch (e) {
    result.models_error = shortReason(e)
  }
  return result
}

// Find the PID listening on :port via `netstat -ano` (Windows). Null when
// nothing is listening.
function findPidOnPort(port) {
  return new Promise((resolve) => {
    execFile('netstat', ['-ano'], { windowsHide: true }, (err, stdout) => {
      if (err || !stdout) return resolve(null)
      for (const line of stdout.split(/\r?\n/)) {
        const t = line.trim().split(/\s+/)
        if (t[0] !== 'TCP' || !t.includes('LISTENING')) continue
        if ((t[1] || '').endsWith(':' + port)) return resolve(t[t.length - 1])
      }
      resolve(null)
    })
  })
}

function killByPort(port) {
  return findPidOnPort(port).then((pid) => {
    if (!pid) return { killed: false, reason: `no listener on :${port}` }
    return new Promise((resolve) => {
      execFile('taskkill', ['/PID', pid, '/T', '/F'], { windowsHide: true }, (err, stdout) => {
        resolve({ killed: !err, pid, out: String(stdout || '').trim().slice(0, 300) })
      })
    })
  })
}

// The Vite dev server is the ALWAYS-UP control plane: it stays running while
// the pipeline server is started, stopped and restarted beneath it.  (The
// pipeline server can't restart itself, so this is the only channel that can
// bring it back — and it can also force-kill it and probe Ollama even while
// the pipeline is down.)
function controlPlugin() {
  return {
    name: 'midway-server-control',
    configureServer(server) {
      server.middlewares.use('/__control/start', (req, res) => {
        if (!methodOnly(req, res, 'POST')) return
        findPidOnPort(8765).then((pid) => {
          if (pid) {
            sendJson(res, 409, { started: false, error: 'already running', pid })
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
            sendJson(res, 200, { started: true, pid: child.pid })
          } catch (e) {
            sendJson(res, 500, { error: String(e) })
          }
        })
      })

      server.middlewares.use('/__control/kill', (req, res) => {
        if (!methodOnly(req, res, 'POST')) return
        killByPort(8765).then((result) => sendJson(res, 200, result))
      })

      server.middlewares.use('/__control/ollama', (req, res) => {
        if (!methodOnly(req, res, 'GET')) return
        probeOllama().then((result) => sendJson(res, 200, result))
      })
    },
  }
}

// The built app is served directly by pipeline_stream_server.py from web/dist,
// so we use a relative base and only need the dev proxy for `npm run dev`.
// `host: true` binds 0.0.0.0 so a phone on the LAN can open the control panel.
export default defineConfig({
  plugins: [react(), controlPlugin()],
  base: './',
  build: { outDir: 'dist' },
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
      '/health': 'http://127.0.0.1:8765',
    },
  },
})
