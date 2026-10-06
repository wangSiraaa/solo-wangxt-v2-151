import React from 'react'

const BAND_CLASS = { low: 'band-low', medium: 'band-medium', high: 'band-high', beyond: 'band-beyond', unknown: '' }

/** 刀具列表：位置、已知负载、消耗区间（定性，非倒计时）。 */
export default function ToolList({ tools, onSelect, selectedId }) {
  return (
    <table className="tool-table">
      <thead>
        <tr>
          <th>编号</th><th>材质</th><th>当前位置</th>
          <th>已知负载</th><th>消耗区间</th><th>未知区段</th>
        </tr>
      </thead>
      <tbody>
        {tools.map((t) => (
          <tr key={t.id}
              className={t.id === selectedId ? 'selected' : ''}
              onClick={() => onSelect(t.id)}>
            <td>{t.code}</td>
            <td>{t.material}</td>
            <td>{t.location ? `${t.location.machine_code} #${t.location.position}` : '未装机'}</td>
            <td>{t.total_known_load.toFixed(2)}</td>
            <td><span className={`band ${BAND_CLASS[t.band] || ''}`}>{t.band_label}</span></td>
            <td>{t.unknown_segment_count > 0
              ? <span className="unknown-badge">{t.unknown_segment_count} 段未知</span>
              : '0'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
