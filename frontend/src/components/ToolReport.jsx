import { useEffect, useState } from 'react'
import { api } from '../api.js'

const PHASE_LABEL = { cutting: '切削', idle: '空转', pause: '暂停' }
const PHASE_COLOR = { cutting: '#2f6fb0', idle: '#b0842f', pause: '#7a7a7a',
                      unknown: '#c0392b' }

export default function ToolReport({ toolCode, onPickTool }) {
  const [tools, setTools] = useState([])
  const [code, setCode] = useState(toolCode || '')
  const [report, setReport] = useState(null)
  const [history, setHistory] = useState([])
  const [err, setErr] = useState('')

  useEffect(() => {
    api.tools().then((ts) => {
      setTools(ts)
      const c = toolCode || (ts[0] && ts[0].tool_code) || ''
      setCode(c)
    }).catch((e) => setErr(e.message))
  }, [])

  useEffect(() => { if (toolCode) setCode(toolCode) }, [toolCode])

  useEffect(() => {
    if (!code) return
    setErr('')
    Promise.all([api.report(code), api.mountHistory(code)])
      .then(([r, h]) => { setReport(r); setHistory(h) })
      .catch((e) => { setReport(null); setErr(e.message) })
  }, [code])

  const segs = report?.segments || []
  const span = segs.length
    ? [toT(segs[0].start_ts), toT(segs[segs.length - 1].end_ts)]
    : null
  const maxLoad = Math.max(0.0001, ...segs
    .filter((s) => s.phase === 'cutting' && s.load != null)
    .map((s) => s.load))

  return (
    <section>
      <h2>加工区段与负载明细</h2>
      {err && <div className="error">{err}</div>}

      <div className="toolbar">
        <label>选择刀具：
          <select value={code} onChange={(e) => setCode(e.target.value)}>
            {tools.map((t) => <option key={t.id}
              value={t.tool_code}>{t.tool_code} · {t.edge_type}</option>)}
          </select>
        </label>
        {report && (
          <div className="muted">
            {report.edge_type} / {report.material_grade}
          </div>
        )}
      </div>

      {report && (
        <>
          <SummaryCards r={report} />
          <LifeBar r={report} />
          <h3>时间线（按转速 / 材料变化切段，空转与暂停独立）</h3>
          <Timeline segs={segs} span={span} maxLoad={maxLoad} />

          <div className="two-col">
            <div>
              <h3>区段明细（负载可回溯到原工序事件）</h3>
              <table className="data small">
                <thead>
                  <tr><th>起</th><th>止</th><th>阶段</th><th>时长(min)</th>
                    <th>转速</th><th>材料</th><th>断续</th>
                    <th>负载</th><th>来源事件</th></tr>
                </thead>
                <tbody>
                  {segs.map((s) => (
                    <tr key={s.id}
                      className={s.load == null && s.phase === 'cutting'
                        ? 'unknown-row' : ''}>
                      <td>{hm(s.start_ts)}</td><td>{hm(s.end_ts)}</td>
                      <td><PhaseBadge phase={s.phase}
                        unknown={s.load == null} /></td>
                      <td>{s.duration_min.toFixed(1)}</td>
                      <td>{s.rpm ?? '—'}</td>
                      <td>{s.material || '—'}</td>
                      <td>{s.interrupted ? '是' : '—'}</td>
                      <td>{s.load == null
                        ? <span className="warn">未知（{reason(s.unknown_reason)}）</span>
                        : s.load.toFixed(5)}</td>
                      <td className="muted">[{s.source_event_ids.join(', ')}]</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3>跨机床挂载历史（身份延续）</h3>
              <ul className="history">
                {history.map((h, i) => (
                  <li key={i}>
                    <b>{h.machine_code}</b> 刀位 #{h.position}
                    <div className="muted">
                      {fmtDt(h.mounted_at)} → {h.dismounted_at
                        ? fmtDt(h.dismounted_at) : '当前在装'}
                    </div>
                    {h.note && <div className="note">备注：{h.note}</div>}
                  </li>
                ))}
              </ul>

              <h3>按材料累计（已知区段）</h3>
              <table className="data small">
                <thead><tr><th>材料</th><th>负载</th></tr></thead>
                <tbody>
                  {Object.entries(report.by_material).map(([k, v]) => (
                    <tr key={k}><td>{k}</td><td>{v.toFixed(5)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="disclaimer">⚠ {report.disclaimer}</div>
        </>
      )}
    </section>
  )
}

function SummaryCards({ r }) {
  return (
    <div className="cards">
      <Card label="切削负载（已知）" value={r.cutting_load_known.toFixed(4)} />
      <Card label="空转负载（单列）" value={r.idle_load.toFixed(5)} />
      <Card label="暂停负载" value="0.00000" />
      <Card label="切削时间 (min)" value={r.cutting_min.toFixed(1)} />
      <Card label="未知工况切削 (min)" value={r.unknown_min.toFixed(1)}
        warn={r.unknown_min > 0} />
      <Card label="已知切削时长占比"
        value={`${(r.known_fraction_of_cutting_min * 100).toFixed(0)}%`} />
    </div>
  )
}

function Card({ label, value, warn }) {
  return (
    <div className={warn ? 'card warn-card' : 'card'}>
      <div className="card-v">{value}</div>
      <div className="card-l">{label}</div>
    </div>
  )
}

function LifeBar({ r }) {
  const used = Math.min(1, r.cutting_load_known)
  const unkShare = r.cutting_min > 0 ? r.unknown_min / r.cutting_min : 0
  return (
    <div className="life">
      <div className="life-label">
        估算寿命占比（仅按已知负载，非精确失效倒计时）
      </div>
      <div className="life-bar">
        <div className="life-used" style={{ width: `${used * 100}%` }} />
        <div className="life-unknown"
          style={{ left: `${used * 100}%`, width: `${unkShare * 30}%` }}
          title="未知工况时间不计入，不代表安全余量" />
      </div>
      <div className="muted">
        模型估算剩余比例 ≈ {(r.estimated_remaining_fraction * 100).toFixed(1)}%
        ；未知区段 {r.unknown_min.toFixed(1)} min 未计入，
        因此该数字可能偏乐观。
      </div>
    </div>
  )
}

function Timeline({ segs, span, maxLoad }) {
  if (!span) return <div className="muted">无区段</div>
  const [t0, t1] = span
  const total = t1 - t0
  return (
    <div className="timeline">
      {segs.map((s) => {
        const left = ((toT(s.start_ts) - t0) / total) * 100
        const width = Math.max(0.5, ((toT(s.end_ts) - toT(s.start_ts)) / total) * 100)
        const unknown = s.phase === 'cutting' && s.load == null
        const color = unknown ? PHASE_COLOR.unknown : PHASE_COLOR[s.phase]
        const height = s.phase === 'cutting' && s.load != null
          ? 28 + (s.load / maxLoad) * 44 : 28
        return (
          <div key={s.id} className="tl-seg"
            style={{ left: `${left}%`, width: `${width}%`,
              background: color, height }}
            title={`${PHASE_LABEL[s.phase]}${unknown ? '（未知）' : ''}
${hm(s.start_ts)}–${hm(s.end_ts)}  ${s.duration_min.toFixed(1)}min
${s.material || ''} ${s.rpm || ''}rpm
负载: ${s.load == null ? '未知' : s.load.toFixed(5)}
来源事件: ${s.source_event_ids.join(',')}`}>
            {width > 4 && (s.phase !== 'cutting' || s.interrupted)
              ? (s.interrupted ? '断续' : PHASE_LABEL[s.phase]) : ''}
          </div>
        )
      })}
    </div>
  )
}

function PhaseBadge({ phase, unknown }) {
  if (phase === 'cutting' && unknown)
    return <span className="badge unk">切削·未知</span>
  return <span className={`badge ${phase}`}>{PHASE_LABEL[phase]}</span>
}

function toT(s) { return new Date(s).getTime() }
function hm(s) {
  const d = new Date(s)
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}
function pad(n) { return String(n).padStart(2, '0') }
function fmtDt(s) { return new Date(s).toLocaleString() }
function reason(r) {
  return ({
    missing_condition: '工况缺失',
    unsupported_material: '材料不在演示表',
    conflicting_active_events: '同时段记录冲突',
    material_unknown_or_conflicting: '材料缺失/冲突',
  })[r] || r
}
