import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Audit() {
  const [events, setEvents] = useState([])
  const [chutes, setChutes] = useState([])
  const [cid, setCid] = useState('')
  const [replay, setReplay] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/审计').then(setEvents).catch((e) => setErr(e.message))
  useEffect(() => {
    load()
    api('/口门').then((cs) => { setChutes(cs); if (cs[0]) setCid(String(cs[0].id)) }).catch(() => {})
  }, [])
  const doReplay = async () => {
    setErr(''); setReplay(null)
    try { setReplay(await api(`/审计/重放?口门编号=${cid}`)) }
    catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>审计 · 超容命运重放</h2>
      <p className="muted">
        裁决、软容量设置、硬拒、回流、开袋入袋封口均写审计。容量账本可重放：
        每口门 SUM(delta) 必须等于派生在口件数，半截回滚后也一致。
      </p>
      {err && <div className="err">{err}</div>}
      <div className="row">
        <label>口门
          <select value={cid} onChange={(e) => setCid(e.target.value)}>
            {chutes.map((c) => <option key={c.id} value={c.id}>{c.code} · 在口{c.在口件数}/{c.软容量}</option>)}
          </select>
        </label>
        <button type="button" onClick={doReplay}>重放该口门容量</button>
        {replay && (
          <span className={replay.一致 ? 'badge ok' : 'badge danger'}>
            {replay.口门码}：账本 {replay.账本累计} / 在口 {replay.在口件数} · {replay.一致 ? '可重放且一致' : '不一致'}
          </span>
        )}
      </div>

      {replay && (
        <table>
          <thead><tr><th>#</th><th>类型</th><th>Δ</th><th>前</th><th>后</th><th>快照容量</th><th>超容</th><th>时间</th><th>说明</th></tr></thead>
          <tbody>
            {(replay.流水 || []).map((e) => (
              <tr key={e.id} className={e.kind === '裁决硬拒' ? 'row-line' : ''}>
                <td>{e.id}</td><td>{e.kind}</td><td>{e.delta}</td><td>{e.count_before}</td>
                <td>{e.count_after}</td><td>{e.cap_snapshot}</td>
                <td>{e.over_cap ? '是' : ''}</td><td>{e.created_at}</td><td>{e.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>审计事件</h3>
      <table>
        <thead><tr><th>编号</th><th>种类</th><th>关联</th><th>动作</th><th>时间</th><th>说明</th></tr></thead>
        <tbody>
          {events.map((a) => (
            <tr key={a.id}><td>{a.id}</td><td>{a.kind}</td><td>{a.ref_id ?? ''}</td><td>{a.action}</td><td>{a.created_at}</td><td>{a.note}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
