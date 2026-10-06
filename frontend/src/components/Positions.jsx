import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function Positions({ onPickTool }) {
  const [machines, setMachines] = useState([])
  const [active, setActive] = useState(null)
  const [slots, setSlots] = useState([])
  const [err, setErr] = useState('')

  useEffect(() => {
    api.machines().then((ms) => {
      setMachines(ms)
      if (ms.length && !active) setActive(ms[0].machine_code)
    }).catch((e) => setErr(e.message))
  }, [])

  useEffect(() => {
    if (active) api.positions(active).then(setSlots).catch((e) => setErr(e.message))
  }, [active])

  return (
    <section>
      <h2>机床刀位（刀位 → 当前刀具）</h2>
      {err && <div className="error">{err}</div>}
      <div className="machine-switch">
        {machines.map((m) => (
          <button key={m.machine_code}
            className={active === m.machine_code ? 'chip on' : 'chip'}
            onClick={() => setActive(m.machine_code)}>
            {m.machine_code} · {m.name}
          </button>
        ))}
      </div>

      <div className="slot-grid">
        {slots.map((s) => (
          <div key={s.position}
            className={s.occupied ? 'slot occupied' : 'slot'}>
            <div className="slot-pos">#{s.position}</div>
            {s.occupied ? (
              <>
                <button className="link"
                  onClick={() => onPickTool(s.tool_code)}>{s.tool_code}</button>
                <div className="muted">{s.edge_type}</div>
                <div>已知负载 <b>{fmt(s.accum_known_load)}</b></div>
                {s.accum_unknown_min > 0 && (
                  <div className="warn">
                    未知工况切削 {s.accum_unknown_min.toFixed(1)} min
                  </div>
                )}
              </>
            ) : (
              <div className="muted">空刀位</div>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}

function fmt(x) {
  return Number(x).toFixed(4)
}
