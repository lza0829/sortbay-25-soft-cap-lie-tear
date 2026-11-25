export async function api(path, options = {}) {
  const res = await fetch(`/api${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { 明细: text } }
  if (!res.ok) {
    const msg = (data && (data.detail || data.明细 || data.message)) || res.statusText
    const error = new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
    error.status = res.status
    error.data = data
    error.reasonCode = data && (data.原因码 || data.reason_code)
    throw error
  }
  return data
}
