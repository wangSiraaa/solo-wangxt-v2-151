import React, { useCallback, useEffect, useState } from 'react'
import { api } from './api.js'
import MachineBoard from './components/MachineBoard.jsx'
import ToolList from './components/ToolList.jsx'
import ToolDetail from './components/ToolDetail.jsx'

export default function App() {
  const [machines, setMachines] = useState([])
  const [tools, setTools] = useState([])
  const [modelInfo, setModelInfo] = useState(null)
  const [selectedTool, setSelectedTool] = useState(null)
  const [error, setError] = useState(null)
  const [seeding, setSeeding] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [m, t] = await Promise.all([api.machines(), api.tools()])
      setMachines(m)
      setTools(t)
      setError(null)
    } catch (e) {
      setError(`后端连接失败：${e.message}`)
    }
  }, [])

  useEffect(() => {
    api.modelInfo().then(setModelInfo).catch(() => {})
    refresh()
  }, [refresh])

  const reseed = async () => {
    setSeeding(true)
    try {
      await api.seedDemo()
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setSeeding(false)
    }
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>刀具消耗演示系统</h1>
          <p className="subtitle">从工序记录估计刀具消耗 · 不按安装天数换刀</p>
        </div>
        <button onClick={reseed} disabled={seeding}>
          {seeding ? '重建中…' : '重建演示案例'}
        </button>
      </header>

      {modelInfo && (
        <div className="disclaimer">
          ⚠ {modelInfo.disclaimer}（模型 {modelInfo.model}：
          参考转速 {modelInfo.params.ref_rpm} rpm，速度指数{' '}
          {modelInfo.params.speed_exponent}，断续系数{' '}
          {modelInfo.params.interrupted_factor}）
        </div>
      )}
      {error && <div className="error">{error}</div>}

      <main>
        <section>
          <h2>机床刀位</h2>
          <MachineBoard machines={machines} onSelect={setSelectedTool} />
        </section>
        <section>
          <h2>刀具</h2>
          <ToolList tools={tools} onSelect={setSelectedTool}
                    selectedId={selectedTool} />
        </section>
        {selectedTool && (
          <section>
            <ToolDetail toolId={selectedTool} machines={machines}
                        onChanged={refresh} onClose={() => setSelectedTool(null)} />
          </section>
        )}
      </main>
    </div>
  )
}
