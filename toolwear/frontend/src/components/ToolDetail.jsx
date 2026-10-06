import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api.js'

const KIND_LABEL = { cut: '切削', idle: '空转', pause: '暂停' }
const BAND_CLASS = { low: 'band-low', medium: 'band-medium', high: 'band-high', beyond: 'band-beyond' }

function fmtTime(iso) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

/** 刀具详情：负载回溯、区段时间线、消耗区间、转机床。 */
export default function ToolDetail({ toolId, machines, onChanged, onClose }) {
  const [detail, setDetail] = useState(null)
  const [report, setReport] = useState(null)
  const [estimate, setEstimate] = useState(null)
  const [error, setError] = useState(null)
  const [transferTo, setTransferTo] = useState({ machine_code: '', position: 1 })

  const load = useCallback(async () => {
    try {
      const [d, r, e] = await Promise.all([
        api.tool(toolId), api.loadReport(toolId), api.lifeEstimate(toolId),
      ])
      setDetail(d); setReport(r); setEstimate(e); setError(null)
    } catch (e2) {
      setError(e2.message)
    }
  }, [toolId])

  useEffect(() => { load() }, [load])

  const doTransfer = async () => {
    if (!transferTo.machine_code) return
    try {
      await api.transfer(toolId, {
        machine_code: transferTo.machine_code,
        position: Number(transferTo.position),
      })
      await load()
      onChanged()
    } catch (e) {
      setError(e.message)
    }
  }

  if (!detail || !report || !estimate) {
    return <div className="detail card">{error || '加载中…'}</div>
  }

  const ratio = estimate.consumption_ratio_known
  const edgeName = (id) => {
    const e = detail.edges.find((x) => String(x.id) === String(id))
    return e ? `刃口 ${e.edge_index}` : `刃口#${id}`
  }

  return (
    <div className="detail card">
      <div className="detail-head">
        <h2>{detail.code} <span className="muted">{detail.material}</span></h2>
        <button className="link" onClick={onClose}>关闭</button>
      </div>
      {error && <div className="error">{error}</div>}

      <div className="grid2">
        <div>
          <h3>消耗区间（非倒计时）</h3>
          <div className="consumption">
            <div className="bar">
              <div className={`fill ${BAND_CLASS[estimate.band] || ''}`}
                   style={{ width: `${Math.min(100, ratio * 100)}%` }} />
            </div>
            <div>
              已知消耗 {report.total_known_load.toFixed(2)} / 经验上限 {detail.nominal_capacity}
              （{(ratio * 100).toFixed(1)}%）
              <span className={`band ${BAND_CLASS[estimate.band] || ''}`}>{estimate.band_label}</span>
            </div>
            {estimate.estimate_incomplete && (
              <div className="unknown-note">
                ⚠ {estimate.unknown_segment_count} 段切削工况缺失，负载未知未计入，
                估计不完整（占切削段 {(estimate.unknown_share_of_cut_segments * 100).toFixed(0)}%）。
              </div>
            )}
            <div className="muted small">{estimate.note}</div>
          </div>

          <h3>负载回溯</h3>
          <div className="breakdowns">
            <Breakdown title="按工序" data={report.by_operation} />
            <Breakdown title="按材料" data={report.by_material} />
            <Breakdown title="按机床" data={report.by_machine} />
            <Breakdown title="按刃口" data={report.by_edge} name={edgeName} />
          </div>
          <div className="muted small">
            切削 {report.cut_minutes} min · 空转 {report.idle_minutes} min ·
            暂停 {report.pause_minutes} min（空转/暂停不计负载）
          </div>

          <h3>装刀历史（转机床保留身份）</h3>
          <table className="mini">
            <thead><tr><th>机床刀位</th><th>刃口</th><th>装刀</th><th>卸刀</th></tr></thead>
            <tbody>
              {detail.assignments.map((a) => (
                <tr key={a.id}>
                  <td>{a.machine_code} #{a.position}</td>
                  <td>{edgeName(a.edge_id)}</td>
                  <td>{fmtTime(a.mounted_at)}</td>
                  <td>{a.unmounted_at ? fmtTime(a.unmounted_at) : '在岗'}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <h3>转机床</h3>
          <div className="transfer">
            <select value={transferTo.machine_code}
                    onChange={(e) => setTransferTo({ ...transferTo, machine_code: e.target.value })}>
              <option value="">选择机床</option>
              {machines.map((m) => <option key={m.id} value={m.code}>{m.code}</option>)}
            </select>
            <input type="number" min="1" max="8" value={transferTo.position}
                   onChange={(e) => setTransferTo({ ...transferTo, position: e.target.value })} />
            <button onClick={doTransfer} disabled={!transferTo.machine_code}>转移</button>
          </div>
        </div>

        <div>
          <h3>加工区段时间线（按发生时间；到达序列为审计编号）</h3>
          <table className="mini segments">
            <thead>
              <tr>
                <th>到达</th><th>开始</th><th>类型</th><th>工序</th>
                <th>转速</th><th>材料</th><th>min</th><th>负载</th>
              </tr>
            </thead>
            <tbody>
              {report.segments.map((s) => (
                <tr key={s.segment_id}
                    className={`kind-${s.kind} ${s.load === null ? 'unknown-row' : ''}`}>
                  <td className="muted">#{s.arrival_order}</td>
                  <td>{fmtTime(s.started_at)}</td>
                  <td>
                    {KIND_LABEL[s.kind]}
                    {s.interrupted && <span className="tag">断续</span>}
                  </td>
                  <td>{s.operation_ref || '—'}</td>
                  <td>{s.rpm ?? '?'}</td>
                  <td>{s.workpiece_material ?? '?'}</td>
                  <td>{s.minutes}</td>
                  <td>{s.load === null
                    ? <span className="unknown-badge">未知</span>
                    : s.load.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

function Breakdown({ title, data, name = (k) => k }) {
  const entries = Object.entries(data)
  const max = Math.max(1e-9, ...entries.map(([, v]) => v))
  return (
    <div className="breakdown">
      <h4>{title}</h4>
      {entries.length === 0 && <div className="muted small">无</div>}
      {entries.map(([k, v]) => (
        <div key={k} className="breakdown-row">
          <span className="breakdown-key">{name(k)}</span>
          <span className="breakdown-bar">
            <span className="breakdown-fill" style={{ width: `${(v / max) * 100}%` }} />
          </span>
          <span className="breakdown-val">{v.toFixed(2)}</span>
        </div>
      ))}
    </div>
  )
}
