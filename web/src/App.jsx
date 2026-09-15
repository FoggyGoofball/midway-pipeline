import { useCallback, useEffect, useRef, useState } from 'react'

function fmtElapsed(s) {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const sec = Math.floor(s % 60)
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`
}

function fmtTtl(ttl) {
  if (ttl == null) return '—'
  if (ttl < 0) return 'expired'
  const m = Math.floor(ttl / 60)
  const s = ttl % 60
  return m > 0 ? `${m}m ${s}s` : `${s}s`
}

// A leading wall-clock timestamp, then the rest of the line.
const TS_RE = /^(\s*\[\d{2}:\d{2}:\d{2}\]\s*)(.*)$/
// The telemetry print format:
//   [Telemetry] <persona> (<model>) — TTFT: X.XXs | Speed: X.X tok/s (X TPM) | Effective: X.X tok/s
const TELE_RE = /^(\s*)\[Telemetry\]\s+(.+?)\s+\((.+?)\)\s+[\u2014\u2013-]\s+TTFT:\s+([\d.]+s)(.*?)\|\s+Speed:\s+([\d.]+ tok\/s)\s+\(([\d.]+ TPM)\)\s+\|\s+Effective:\s+([\d.]+ tok\/s)(.*)$/

function renderTags(text, keyPrefix) {
  const parts = text.split(/(\[[^\]\n]*\])/g)
  return parts.map((part, i) => {
    const key = `${keyPrefix}-${i}`
    if (/^\[\d{2}:\d{2}:\d{2}\]$/.test(part)) return <span key={key} className="ts">{part}</span>
    if (/^\[(?:START|END)\]$/.test(part)) return <span key={key} className="ok">{part}</span>
    if (/^\[[^\]]+\]$/.test(part)) return <span key={key} className="tag">{part}</span>
    return <span key={key}>{part}</span>
  })
}

function renderLogLine(line, i) {
  let ts = null
  let rest = line
  const tm = line.match(TS_RE)
  if (tm) {
    ts = <span className="ts">{tm[1]}</span>
    rest = tm[2]
  }
  const tele = rest.match(TELE_RE)
  if (tele) {
    return (
      <div className="logline" key={i}>
        {ts}
        {tele[1]}
        <span className="tag">[Telemetry]</span>{' '}
        <span className="persona">{tele[2]}</span>{' '}
        <span className="dim">(</span><span className="model">{tele[3]}</span><span className="dim">)</span>
        <span className="dim"> {'\u2014'} </span>
        <span className="dim">TTFT: </span><span className="ttft">{tele[4]}</span>
        {tele[5].trim() ? <span className="warn">{tele[5]}</span> : null}
        <span className="dim"> | Speed: </span><span className="tps">{tele[6]}</span>
        <span className="dim"> ({tele[7]}) | Effective: </span>
        <span className="eff">{tele[8]}</span>
        {tele[9].trim() ? <span className="warn">{tele[9]}</span> : null}
      </div>
    )
  }
  const isErr = /⛔|ERROR|❌|✗/.test(rest)
  return (
    <div className={`logline${isErr ? ' err' : ''}`} key={i}>
      {ts}
      {renderTags(rest, i)}
    </div>
  )
}

export default function App() {
  const [status, setStatus] = useState(null)
  const [ollama, setOllama] = useState(null)
  const [prompt, setPrompt] = useState('refer to the GDD and build the strongman striker')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [follow, setFollow] = useState(true)
  const logRef = useRef(null)

  const refreshStatus = useCallback(async () => {
    try {
      const r = await fetch('/api/status')
      setStatus(await r.json())
    } catch {
      /* server unreachable — keep last known state */
    }
  }, [])

  const refreshOllama = useCallback(async () => {
    try {
      const r = await fetch('/api/ollama')
      setOllama(await r.json())
    } catch {
      /* server unreachable */
    }
  }, [])

  useEffect(() => {
    refreshStatus()
    refreshOllama()
    const t1 = setInterval(refreshStatus, 2000)
    const t2 = setInterval(refreshOllama, 4000)
    return () => {
      clearInterval(t1)
      clearInterval(t2)
    }
  }, [refreshStatus, refreshOllama])

  useEffect(() => {
    if (follow && logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [status?.logs, follow])

  const onLogScroll = () => {
    const el = logRef.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setFollow(nearBottom)
  }

  const run = async () => {
    if (!prompt.trim() || busy) return
    setBusy(true)
    setNotice('')
    try {
      const r = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.trim() }),
      })
      const j = await r.json().catch(() => ({}))
      if (r.status === 409) {
        setNotice('A run is already active.')
      } else if (r.ok) {
        setNotice('Run started.')
        refreshStatus()
      } else {
        setNotice(j.error || `HTTP ${r.status}`)
      }
    } catch {
      setNotice('Could not reach the pipeline server.')
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    if (busy) return
    setBusy(true)
    setNotice('')
    try {
      const r = await fetch('/api/stop', { method: 'POST' })
      const j = await r.json().catch(() => ({}))
      if (r.ok) {
        setNotice('Stop requested — finishing the current task…')
        refreshStatus()
      } else {
        setNotice(j.error || `HTTP ${r.status}`)
      }
    } catch {
      setNotice('Could not reach the pipeline server.')
    } finally {
      setBusy(false)
    }
  }

  const running = !!status?.running
  const tel = status?.last_telemetry
  const logs = status?.logs || []

  return (
    <div className="app">
      <header className="top">
        <div className="title">
          <h1>Midway Pipeline</h1>
          <span className={`pill ${running ? 'on' : 'off'}`}>
            {running ? (status?.stop_requested ? '● STOPPING' : '● RUNNING') : '○ IDLE'}
          </span>
        </div>
        <div className="conn">
          {ollama == null ? (
            <span className="dot unknown" title="probing…" />
          ) : ollama.reachable ? (
            <span className="dot ok" title="Ollama reachable" />
          ) : (
            <span className="dot bad" title="Ollama unreachable" />
          )}
          <span>Ollama {ollama?.version || ''}</span>
        </div>
      </header>

      <section className="card">
        <h2>Command</h2>
        <textarea
          rows={3}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="e.g. refer to the GDD and build the strongman striker"
        />
        <div className="row">
          <button
            onClick={running ? stop : run}
            disabled={busy || (running && status?.stop_requested)}
            className={running && !status?.stop_requested ? 'stop' : ''}
          >
            {busy ? '…' : running ? (status?.stop_requested ? 'Stopping…' : 'Stop pipeline') : 'Run pipeline'}
          </button>
          {notice && <span className="notice">{notice}</span>}
        </div>
      </section>

      <section className="card">
        <h2>Generation</h2>
        <div className="kv">
          <div>
            <span className="k">Phase</span>
            <span className="v">{status?.phase || '—'}{status?.detail ? ` · ${status.detail}` : ''}</span>
          </div>
          <div>
            <span className="k">Elapsed</span>
            <span className="v">{fmtElapsed(status?.elapsed_s)}</span>
          </div>
          <div>
            <span className="k">Prompt</span>
            <span className="v dim">{status?.prompt || '—'}</span>
          </div>
          {status?.run_count > 0 && (
            <div>
              <span className="k">Runs</span>
              <span className="v">{status.run_count}</span>
            </div>
          )}
          {status?.last_error && (
            <div>
              <span className="k">Error</span>
              <span className="v err">{status.last_error}</span>
            </div>
          )}
        </div>

        <h3 className="sub">Last telemetry</h3>
        <div className="tele">
          <div className="cell">
            <span className="k">Persona</span>
            <span className="v persona">{tel?.label || status?.current_label || '—'}</span>
          </div>
          <div className="cell">
            <span className="k">Model</span>
            <span className="v model">{tel?.model || status?.current_model || '—'}</span>
          </div>
          <div className="cell">
            <span className="k">TTFT</span>
            <span className="v ttft">{tel?.ttft != null ? `${tel.ttft}s` : '—'}</span>
          </div>
          <div className="cell">
            <span className="k">TPS</span>
            <span className="v tps">{tel?.tps != null ? `${tel.tps} tok/s` : '—'}</span>
          </div>
          <div className="cell">
            <span className="k">Tokens</span>
            <span className="v">{tel?.tokens ?? '—'}</span>
          </div>
          <div className="cell">
            <span className="k">Effective</span>
            <span className="v">{tel?.effective_tps != null ? `${tel.effective_tps} tok/s` : '—'}</span>
          </div>
        </div>
      </section>

      <section className="card">
        <h2>Ollama</h2>
        {ollama == null ? (
          <div className="dim">probing…</div>
        ) : !ollama.reachable ? (
          <div className="err">Unreachable: {ollama.error || 'unknown'}</div>
        ) : (
          <>
            <div className="kv">
              <div>
                <span className="k">Host</span>
                <span className="v dim">{ollama.host}</span>
              </div>
              <div>
                <span className="k">Version</span>
                <span className="v">{ollama.version || '—'}</span>
              </div>
            </div>
            <h3 className="sub">Loaded models</h3>
            {ollama.models.length === 0 ? (
              <div className="dim">No models loaded right now.</div>
            ) : (
              <ul className="models">
                {ollama.models.map((m) => (
                  <li key={m.name}>
                    <span className="model">{m.name}</span>
                    <span className="dim"> · unloads in {fmtTtl(m.ttl_s)}</span>
                  </li>
                ))}
              </ul>
            )}
            {ollama.models_error && <div className="warn">/api/ps error: {ollama.models_error}</div>}
          </>
        )}
      </section>

      <section className="card">
        <div className="row space">
          <h2>Console log</h2>
          <label className="follow">
            <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
            follow
          </label>
        </div>
        <div className="log" ref={logRef} onScroll={onLogScroll}>
          {logs.map((line, i) => renderLogLine(line, i))}
        </div>
      </section>
    </div>
  )
}
