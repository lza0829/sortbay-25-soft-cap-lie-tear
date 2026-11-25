import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Diverts() {
  const [batches, setBatches] = useState([])
  const [scans, setScans] = useState([])
  const [scanId, setScanId] = useState('')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = async () => {
    setBatches(await api('/回流批次'))
    const s = await api('/扫描')
    setScans(s.filter((x) => x.status === '已路由' || x.status === '已入袋'))
  }
  useEffect(() => { load().catch((e) => setErr(e.message)) }, [])
  const create = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/回流批次', { method: 'POST', body: JSON.stringify({ 扫描编号: Number(scanId) }) })
      setMsg(`已建回流批次，扫描转错分回流并离开在口口径；该口门当前在口 ${r.在口件数} 件，可在扫描册重新裁决`)
      setScanId('')
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>错分回流 · 回流入口</h2>
      <p className="muted">已路由/已入袋件建回流后转为错分回流，在口件数同步减一；之后可在扫描册再次路由裁决。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>扫描
          <select value={scanId} onChange={(e) => setScanId(e.target.value)}>
            <option value="">选择</option>
            {scans.map((s) => <option key={s.id} value={s.id}>{s.id} · {s.barcode} · {s.status}</option>)}
          </select>
        </label>
        <button type="button" onClick={create}>建回流批次</button>
      </div>
      <table>
        <thead><tr><th>编号</th><th>批次码</th><th>状态</th><th>时间</th></tr></thead>
        <tbody>
          {batches.map((b) => (
            <tr key={b.id}><td>{b.id}</td><td>{b.code}</td><td>{b.status}</td><td>{b.created_at}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
