import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function ToolList({ onPickTool }) {
  const [tools, setTools] = useState([])
  const [err, setErr] = useState('')

  useEffect(() => {
    api.tools().then(setTools).catch((e) => setErr(e.message))
  }, [])

  return (
    <section>
      <h2>刀具（刃口 / 材质 / 累计切削时间）</h2>
      {err && <div className="error">{err}</div>}
      <table className="data">
        <thead>
          <tr>
            <th>刀具编码</th><th>刃口类型</th><th>材质</th>
            <th>累计切削 (min)</th><th>已知负载</th>
            <th>未知工况切削 (min)</th><th>当前位置</th>
          </tr>
        </thead>
        <tbody>
          {tools.map((t) => (
            <tr key={t.id}>
              <td><button className="link"
                onClick={() => onPickTool(t.tool_code)}>{t.tool_code}</button></td>
              <td>{t.edge_type}</td>
              <td>{t.material_grade}</td>
              <td>{t.accum_cutting_min.toFixed(1)}</td>
              <td>{t.accum_known_load.toFixed(4)}</td>
              <td className={t.accum_unknown_min > 0 ? 'warn-cell' : ''}>
                {t.accum_unknown_min.toFixed(1)}
              </td>
              <td>{t.current_machine
                ? `${t.current_machine} #${t.current_position}`
                : <span className="muted">未挂载（在库）</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">
        刀具身份为全局唯一；从一台机床拆下再装到另一台机床，编码与累计历史不变。
      </p>
    </section>
  )
}
