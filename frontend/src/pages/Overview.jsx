import React, { useEffect, useState } from 'react'
import { api } from '../api'

function CapBar({ c }) {
  const cap = Number(c.软容量 ?? c.soft_cap ?? 0)
  const n = Number(c.在口件数 ?? c.在口数 ?? 0)
  const pct = cap > 0 ? Math.min(100, Math.round((n / cap) * 100)) : (n > 0 ? 100 : 0)
  const cls = c.超容 ? 'over' : (c.达到贴线阈值 || c.贴线) ? 'line' : ''
  return (
    <div className={`cap-bar ${cls}`} title={`在口 ${n}/${cap}`}>
      <i style={{ width: `${(c.超容 || c.红条顶满 || c.达到贴线阈值) ? 100 : pct}%` }} />
    </div>
  )
}

export default function Overview() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const load = () => api('/总览').then(setData).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  if (err) return <div className="panel err">{err}</div>
  if (!data) return <div className="panel">加载中…</div>
  const d = data.分布 || {}
  const overs = data.超容口门 || []
  const lines = data.贴线口门 || []
  const closed = data.闸线闭闭口门 || data.红条口门 || []
  const rec = data.容量对账 || {}
  const ledgerOk = data.全部一致 ?? rec.全部一致 ?? rec.一致
  return (
    <div className="panel">
      <h2>总览</h2>
      <p className="muted">
        当前班次：{data.班次?.business_day} {data.班次?.name} · {data.班次?.sealed ? '已封班' : '未封班'}
        {' '}· 软容量口径：{data.软容量策略 || '硬拒'}
        {typeof rec.一致 === 'boolean' && (rec.一致 ? ' · 在口对账一致' : ' · 在口对账不一致')}
        {typeof ledgerOk === 'boolean' && (ledgerOk ? ' · 账本可重放' : ' · 容量账不一致')}
      </p>
      {data.冻结宣称 && (
        <div className="alert-bar danger-bar">
          <strong>容量账未清，成功/未超宣称已冻结：</strong>
          <span className="small">SUM、链式流水或裁决审计未通过重放，请以审计页对账异常为准。</span>
        </div>
      )}

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
          <span className="small">总览不得假装未超；超容事实同时见口门页、裁决回执、容量戳与审计。</span>
        </div>
      )}
      {overs.length === 0 && lines.length > 0 && (
        <div className="alert-bar warn-bar">
          <strong>贴线口门（{lines.length}）：</strong>
          {lines.map((c) => (
            <span key={c.id} className="badge warn" style={{ marginRight: 8 }}>
              {c.code} 在口 {c.在口件数}/{c.软容量}
            </span>
          ))}
          <span className="small">红条顶满后下一笔裁决必须硬拒，回执、在口、贴线、容量戳与审计同命运。</span>
        </div>
      )}
      {closed.length > 0 && overs.length > 0 && (
        <div className="small">另有闸线闭合/红条格 {closed.length} 个口门（含超容）。</div>
      )}

      <table>
        <thead>
          <tr><th>口门</th><th>标题</th><th>状态</th><th>在口件数</th><th>软容量</th><th>剩余</th><th>容量条</th><th>容量状态</th><th>告警</th></tr>
        </thead>
        <tbody>
          {(data.口门 || []).map((c) => (
            <tr key={c.id} className={c.超容 ? 'row-over' : ((c.容量状态 === '贴线' || c.贴线) ? 'row-line' : '')}>
              <td>{c.code}</td>
              <td>{c.title}</td>
              <td>{c.status}</td>
              <td><strong>{c.在口件数}</strong></td>
              <td>{c.软容量 ?? c.soft_cap}</td>
              <td>{c.剩余容量}</td>
              <td style={{ minWidth: 130 }}><CapBar c={c} /></td>
              <td>{c.容量状态}</td>
              <td>{c.告警信息 || (c.超容 ? '超容' : (c.贴线 ? '贴线' : '—'))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row"><button type="button" className="secondary" onClick={load}>刷新</button></div>
    </div>
  )
}
