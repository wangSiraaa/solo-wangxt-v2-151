import { useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import Positions from './components/Positions.jsx'
import ToolList from './components/ToolList.jsx'
import ToolReport from './components/ToolReport.jsx'
import ModelParams from './components/ModelParams.jsx'

const TABS = [
  { key: 'positions', label: '刀位 / 机床' },
  { key: 'tools', label: '刀具' },
  { key: 'report', label: '加工区段与负载' },
  { key: 'model', label: '演示模型参数' },
]

export default function App() {
  const [tab, setTab] = useState('positions')
  const [selectedTool, setSelectedTool] = useState(null)
  const [health, setHealth] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.health().then(setHealth).catch((e) => setError(e.message))
  }, [])

  const openReport = (code) => {
    setSelectedTool(code)
    setTab('report')
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>刀具消耗估算 <span className="tag">DEMO</span></h1>
        <div className="health">
          {health
            ? <span className="dot ok" title={JSON.stringify(health)}>后端在线 · 演示模型</span>
            : <span className="dot bad">后端未连接</span>}
        </div>
      </header>

      <DemoBanner />

      <nav className="tabs">
        {TABS.map((t) => (
          <button key={t.key}
            className={tab === t.key ? 'tab active' : 'tab'}
            onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </nav>

      {error && <div className="error">{error}</div>}

      <main>
        {tab === 'positions' && <Positions onPickTool={openReport} />}
        {tab === 'tools' && <ToolList onPickTool={openReport} />}
        {tab === 'report' && (
          <ToolReport toolCode={selectedTool} onPickTool={openReport} />
        )}
        {tab === 'model' && <ModelParams />}
      </main>

      <footer className="foot">
        演示系统：不连接真实机床信号，输出为工序记录的经验模型估算，
        不构成失效倒计时，也不能替代安全换刀决策。
      </footer>
    </div>
  )
}

function DemoBanner() {
  return (
    <div className="banner">
      本系统使用<b>固定演示参数</b>，依据工序记录（切削 / 空转 / 暂停）估算刀具刃口负载；
      缺失工况的切削区段标记为<b>未知负载（不为零）</b>。
    </div>
  )
}
