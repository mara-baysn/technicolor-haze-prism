import { useState, useEffect } from 'react'
import DashboardPage from './pages/DashboardPage'
import RulesPage from './pages/RulesPage'
import TrafficFlowPage from './pages/TrafficFlowPage'
import { metricsStream } from './api/client'

type Page = 'dashboard' | 'rules' | 'traffic-flow'
type WsStatus = 'connected' | 'disconnected' | 'connecting'

function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [wsStatus, setWsStatus] = useState<WsStatus>('disconnected')

  useEffect(() => {
    const unsub = metricsStream.onStatusChange((status) => {
      setWsStatus(status)
    })
    metricsStream.connect()
    return unsub
  }, [])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      {/* Header with navigation */}
      <header className="border-b border-gray-800 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-6">
          <h1 className="text-xl font-bold tracking-tight text-white">Prism Virtual Firewall</h1>
          <nav className="flex items-center gap-1">
            <NavButton active={page === 'dashboard'} onClick={() => setPage('dashboard')}>
              Dashboard
            </NavButton>
            <NavButton active={page === 'rules'} onClick={() => setPage('rules')}>
              Rules
            </NavButton>
            <NavButton active={page === 'traffic-flow'} onClick={() => setPage('traffic-flow')}>
              Traffic Flow
            </NavButton>
          </nav>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500 bg-gray-800 px-2 py-0.5 rounded">PoC Demo</span>
          <ConnectionIndicator status={wsStatus} />
        </div>
      </header>

      {/* Page content */}
      <main className="flex-1 min-h-0 flex flex-col">
        {page === 'dashboard' && <DashboardPage />}
        {page === 'rules' && <RulesPage />}
        {page === 'traffic-flow' && <TrafficFlowPage />}
      </main>
    </div>
  )
}

function NavButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${
        active
          ? 'bg-gray-800 text-white'
          : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50'
      }`}
    >
      {children}
    </button>
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
