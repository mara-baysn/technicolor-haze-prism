import { useState, useEffect } from 'react'
import TrafficGenPanel from './components/TrafficGenPanel'
import FirewallPanel from './components/FirewallPanel'
import ReceiverPanel from './components/ReceiverPanel'
import TopologyDiagram from './components/TopologyDiagram'
import { metricsStream } from './api/client'

type WsStatus = 'connected' | 'disconnected' | 'connecting'

function App() {
  const [wsStatus, setWsStatus] = useState<WsStatus>('disconnected')

  useEffect(() => {
    const unsub = metricsStream.onStatusChange((status) => {
      setWsStatus(status)
    })
    // Ensure connection is initiated
    metricsStream.connect()
    return unsub
  }, [])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-bold tracking-tight">Prism Virtual Firewall</h1>
          <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">PoC Demo</span>
        </div>
        <ConnectionIndicator status={wsStatus} />
      </header>

      {/* Topology diagram */}
      <div className="px-6 pt-4 pb-2 shrink-0">
        <TopologyDiagram />
      </div>

      {/* 3-panel layout */}
      <div className="flex-1 grid grid-cols-3 gap-4 px-6 pb-6 min-h-0">
        {/* LEFT: Traffic Generator */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <TrafficGenPanel />
        </div>

        {/* CENTER: Firewall */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <FirewallPanel />
        </div>

        {/* RIGHT: Receiver */}
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <ReceiverPanel />
        </div>
      </div>
    </div>
  )
}

function ConnectionIndicator({ status }: { status: WsStatus }) {
  const config = {
    connected: { color: 'bg-green-400', label: 'Live' },
    connecting: { color: 'bg-yellow-400 animate-pulse', label: 'Connecting...' },
    disconnected: { color: 'bg-red-400', label: 'Disconnected' },
  }

  const { color, label } = config[status]

  return (
    <div className="flex items-center gap-1.5">
      <span className={`w-2 h-2 rounded-full ${color}`} />
      <span className="text-xs text-gray-400">{label}</span>
    </div>
  )
}

export default App
