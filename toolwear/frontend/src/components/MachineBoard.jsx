import React from 'react'

/** 机床刀位看板：每个刀位当前装的是哪把刀。 */
export default function MachineBoard({ machines, onSelect }) {
  return (
    <div className="board">
      {machines.map((m) => (
        <div key={m.id} className="machine-card">
          <h3>{m.code} <span className="muted">{m.name}</span></h3>
          <div className="slots">
            {m.slots.map((s) => (
              <button
                key={s.position}
                className={`slot ${s.tool ? 'occupied' : ''}`}
                disabled={!s.tool}
                onClick={() => s.tool && onSelect(s.tool.id)}
                title={s.tool ? `刃口 #${s.tool.edge_id}` : '空刀位'}
              >
                <span className="slot-pos">#{s.position}</span>
                <span className="slot-tool">{s.tool ? s.tool.code : '—'}</span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
