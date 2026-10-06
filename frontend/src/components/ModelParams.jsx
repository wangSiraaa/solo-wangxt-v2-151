import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ModelParams() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    api.modelParams().then(setData).catch((e) => setErr(e.message))
  }, [])

  if (err) return <div className="error">{err}</div>
  if (!data) return <div>加载中…</div>

  return (
    <section>
      <h2>固定演示参数（不可用于真实换刀决策）</h2>
      <div className="banner">
        {data.note}
      </div>
      <h3>公式</h3>
      <pre className="formula">{
`wear_rate = k0 · k_material
          · (rpm / v_ref)^n_v
          · (feed / f_ref)^n_f
          · (depth / d_ref)^n_d
load      = wear_rate · duration_min
断续切削: wear_rate × k_interrupted
空转负载: 单独按 idle_load_per_min 累计
暂停负载: 0（不切削）
缺失/冲突工况: load = NULL（未知，不当作零）`
      }</pre>
      <h3>参数</h3>
      <table className="data">
        <tbody>
          {Object.entries(data.params).map(([k, v]) => (
            <tr key={k}><td>{k}</td><td>{v}</td></tr>
          ))}
          <tr><td>idle_load_per_min</td><td>{data.idle_load_per_min}</td></tr>
          <tr><td>pause_load_per_min</td><td>{data.pause_load_per_min}</td></tr>
        </tbody>
      </table>
      <h3>材料系数 k_material（演示表）</h3>
      <table className="data">
        <tbody>
          {Object.entries(data.material_factors).map(([k, v]) => (
            <tr key={k}><td>{k}</td><td>{v}</td></tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
