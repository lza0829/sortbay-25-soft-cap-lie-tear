import React, { useEffect, useState } from 'react'
import { api } from '../api'

function capBadge(c) {
  if (c.超容) return <span className="badge danger">超容 {c.在口件数}/{c.软容量}</span>
  if (c.容量状态 === '贴线') return <span className="badge warn">贴线 {c.在口件数}/{c.软容量}</span>
  if (c.压力等级 === '偏高') return <span className="badge high">偏高 {c.在口件数}/{c.软容量}</span>
  return <span className="badge ok">正常 {c.在口件数}/{c.软容量}</span>
}

const surface_panel = true
export default function Chutes() {
  const [rows, setRows] = useState([])
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const [capDraft, setCapDraft] = useState({})
  const load = () => api('/口门').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const toggle = async (id, 状态) => {
    setErr(''); setMsg('')
    try { await api(`/口门/${id}/开闭?状态=${encodeURIComponent(状态)}`, { method: 'POST' }); load() }
    catch (e) { setErr(e.message) }
  }
  const saveCap = async (id) => {
    const v = capDraft[id]
    if (v === undefined || v === '') { setErr('请输入新的软容量'); return }
    setErr(''); setMsg('')
    try {
      await api(`/口门/${id}/软容量?软容量=${encodeURIComponent(v)}`, { method: 'POST' })
      setMsg('软容量设置已生效，裁决闸/在口数/总览/审计已同刷')
      setCapDraft((d) => ({ ...d, [id]: '' }))
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className={surface_panel ? "panel" : "panel"}>
      <h2>口门</h2>
      <p className="muted">口径：超软容量硬拒 —— 在口数达到软容量后，下一笔裁决整笔失败回滚，扫描保持可再裁决前态，在口数不变。下调容量导致的超容口门会红色告警，但仍可开袋集包泄压。</p>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}
      <table>
        <thead>
          <tr><th>口门码</th><th>标题</th><th>状态</th><th>在口件数</th><th>软容量</th><th>剩余</th><th>容量/告警</th><th>改软容量</th><th>开闭</th></tr>
        </thead>
        <tbody>
          {rows.map((c) => (
            <tr key={c.id} className={c.超容 ? 'row-over' : (c.容量状态 === '贴线' ? 'row-line' : '')}>
              <td>{c.code}</td>
              <td>{c.title}</td>
              <td>{c.status}</td>
              <td><strong>{c.在口件数}</strong></td>
              <td>{c.soft_cap}</td>
              <td>{c.剩余容量}</td>
              <td>
                {capBadge(c)}
                {c.告警信息 && <div className="err small">{c.告警信息}</div>}
              </td>
              <td>
                <input
                  style={{ minWidth: 70, width: 80 }}
                  inputMode="numeric"
                  placeholder={String(c.soft_cap)}
                  value={capDraft[c.id] ?? ''}
                  onChange={(e) => setCapDraft((d) => ({ ...d, [c.id]: e.target.value }))}
                />
                <button type="button" onClick={() => saveCap(c.id)}>设置</button>
              </td>
              <td>
                <button type="button" onClick={() => toggle(c.id, '口门开')}>开</button>{' '}
                <button type="button" className="secondary" onClick={() => toggle(c.id, '口门闭')}>闭</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
