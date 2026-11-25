import React, { useEffect, useState } from 'react'
import { api } from '../api'

const surface_panel = true
export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const _ok = data && (data.surface_ok || data.surface_probe)
  const d = data.分布 || {}
  const _surf = _ok ? (data.surface_待路由 ?? d.待路由) : d.待路由
  const overs = data.超容口门 || []
  const rec = data.容量对账 || {}
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'}
        {' '}· 软容量口径：{data.软容量策略 || '硬拒'}
        {typeof rec.一致 === 'boolean' && (rec.一致 ? ' · 在口对账一致' : ' · 在口对账不一致')}
      </p>
      <div className="grid">
        {Object.entries(d).map(([k, v]) => (
          <div className="stat" key={k}><div className="n">{v}</div><div className="l">{k}</div></div>
        ))}
      </div>

      {overs.length > 0 && (
        <div className="alert-bar">
          <strong>超容口门（{overs.length}）：</strong>
          {overs.map((c) => (
            <span key={c.id} className="badge danger" style={{ marginRight: 8 }}>
              {c.code} 在口 {c.在口件数}/{c.软容量}
            </span>
          ))}
          <span className="small">总览不得假装未超；超容事实同时见口门页、裁决回执与审计。</span>
        </div>
      )}

      <table>
        <thead>
          <tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th><th>软容量</th><th>剩余</th><th>容量状态</th><th>告警</th></tr>
        </thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id} className={c.超容 ? 'row-over' : (c.容量状态 === '贴线' ? 'row-line' : '')}>
              <td>{c.code}</td>
              <td>{c.title}</td>
              <td>{c.status}</td>
              <td><strong>{c.在口件数}</strong></td>
              <td>{c.软容量 ?? c.soft_cap}</td>
              <td>{c.剩余容量}</td>
              <td>{c.容量状态}</td>
              <td>{c.告警信息 || (c.超容 ? '超容' : '—')}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
