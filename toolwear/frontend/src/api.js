const base = ''

async function req(path, options) {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  modelInfo: () => req('/api/model-info'),
  machines: () => req('/api/machines'),
  tools: () => req('/api/tools'),
  tool: (id) => req(`/api/tools/${id}`),
  loadReport: (id) => req(`/api/tools/${id}/load`),
  lifeEstimate: (id) => req(`/api/tools/${id}/life-estimate`),
  transfer: (id, payload) =>
    req(`/api/tools/${id}/transfer`, { method: 'POST', body: JSON.stringify(payload) }),
  seedDemo: () => req('/api/seed/demo', { method: 'POST' }),
}
