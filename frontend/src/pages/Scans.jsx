import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Scans() {
  const [rows, setRows] = useState([])
  const [barcode, setBarcode] = useState('')
  const [dest, setDest] = useState('110')
  const [msg, setMsg] = useState('')
  const [receipt, setReceipt] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/扫描').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const create = async () => {
    setErr(''); setMsg(''); setReceipt(null)
    try {
      await api('/扫描', { method: 'POST', body: JSON.stringify({ 条码: barcode, 目的地码: dest }) })
      setMsg('过机装载成功'); setBarcode(''); load()
    } catch (e) { setErr(e.message) }
  }
  const routeOne = async (id) => {
    setErr(''); setMsg(''); setReceipt(null)
    try {
      const r = await api(`/扫描/${id}/路由`, { method: 'POST' })
      setReceipt(r.回执 || r)
      setMsg(`路由成功：${r.口门码 || ''}，在口 ${r.在口件数}/${r.软容量}（${r.容量状态 || ''}）`)
      load()
    } catch (e) {
      setErr(`裁决硬拒/失败：${e.message}`)
      load() // 硬拒后扫描仍为可再裁决前态，必须同刷扫描册
    }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>过机扫描 · 裁决入口</h2>
      <p className="muted">条码目的地经路由表唯一决定落入口门；达到该口门软容量后裁决整笔失败回滚，扫描保持可再裁决，在口数不变。</p>
      <div className="row">
        <label>条码<input value={barcode} onChange={(e) => setBarcode(e.target.value)} /></label>
        <label>目的地码<input value={dest} onChange={(e) => setDest(e.target.value)} /></label>
        <button type="button" onClick={create}>装载扫描</button>
      </div>
      {msg && <div className="msg">{msg}</div>}
      {receipt && (
        <div className="receipt">
          裁决回执：{receipt.结果} · 口门 {receipt.口门码} · 在口 {receipt.在口件数}/{receipt.软容量}
          {receipt.容量状态 ? ` · ${receipt.容量状态}` : ''}
        </div>
      )}
      {err && <div className="err">{err}</div>}
      <table>
        <thead><tr><th>编号</th><th>条码</th><th>目的地</th><th>状态</th><th>口门</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.status === '待路由' || r.status === '错分回流' ? '' : 'muted-row'}>
              <td>{r.id}</td><td>{r.barcode}</td><td>{r.dest_code}</td><td>{r.status}</td>
              <td>{r.口门码 ? `${r.口门码} (#${r.chute_id})` : (r.chute_id || '-')}</td>
              <td>{(r.status === '待路由' || r.status === '错分回流') && <button type="button" onClick={() => routeOne(r.id)}>路由</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
