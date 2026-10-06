const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000'

async function req(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${text}`)
  }
  return res.status === 204 ? null : res.json()
}

export const api = {
  health: () => req('/api/health'),
  modelParams: () => req('/api/model-params'),
  machines: () => req('/api/machines'),
  positions: (code) => req(`/api/machines/${code}/positions`),
  tools: () => req('/api/tools'),
  report: (code) => req(`/api/tools/${code}/report`),
  events: (code) => req(`/api/tools/${code}/events`),
  mountHistory: (code) => req(`/api/tools/${code}/mount-history`),
}
