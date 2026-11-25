import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function Settings() {
  const [rows, setRows] = useState([])
  const [key, setKey] = useState('回流自动再路由')
  const [val, setVal] = useState('否')
  const [globalCap, setGlobalCap] = useState('120')
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = () => api('/设置').then(setRows).catch((e) => setErr(e.message))
  useEffect(() => { load() }, [])
  const save = async () => {
    setErr(''); setMsg('')
    try {
      await api('/设置', { method: 'POST', body: JSON.stringify({ 键: key, 值: val }) })
      setMsg('设置已保存'); load()
    } catch (e) { setErr(e.message) }
  }
  const saveGlobalCap = async () => {
    setErr(''); setMsg('')
    try {
      const r = await api('/设置', { method: 'POST', body: JSON.stringify({ 键: '口门软容量提示', 值: Number(globalCap) }) })
      setMsg(`全局软容量 ${globalCap} 已同步到 ${r.同步口门数} 个口门，裁决闸即时生效`)
      load()
    } catch (e) { setErr(e.message) }
  }
  return (
    <div className="panel">
      <h2>设置 · 软容量</h2>
      <div className="alert-bar">
        当前全局口径：<strong>超软容量硬拒</strong> —— 达到软容量则裁决整笔失败并回滚落入；不采用“告警仍可超容落入”，全局不混用。
      </div>
      {msg && <div className="msg">{msg}</div>}
      {err && <div className="err">{err}</div>}

      <div className="row">
        <label>全局软容量（保存即同步所有口门）
          <input value={globalCap} inputMode="numeric" onChange={(e) => setGlobalCap(e.target.value)} />
        </label>
        <button type="button" onClick={saveGlobalCap}>应用到全部口门</button>
      </div>

      <h3>通用键值设置</h3>
      <div className="row">
        <label>键<input value={key} onChange={(e) => setKey(e.target.value)} /></label>
        <label>值<input value={val} onChange={(e) => setVal(e.target.value)} /></label>
        <button type="button" onClick={save}>保存</button>
      </div>
      <table>
        <thead><tr><th>键</th><th>值</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.键}><td>{r.键}</td><td>{r.值}</td></tr>)}</tbody>
      </table>
    </div>
  )
}
