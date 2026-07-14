import { useState, useEffect } from 'react'
import DashboardPage from './pages/DashboardPage'
import RulesPage from './pages/RulesPage'
import { metricsStream } from './api/client'

type Page = 'dashboard' | 'rules'
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
    <div className="min-h-screen bg-void text-text flex flex-col">
      {/* Header */}
      <header className="border-b border-border px-5 py-2.5 flex items-center justify-between shrink-0 bg-void">
        <div className="flex items-center gap-5">
          <h1 className="text-base font-bold tracking-tight text-text font-sans">
            Prism Virtual Firewall
          </h1>
          <nav className="flex items-center gap-0.5">
            <NavTab active={page === 'dashboard'} onClick={() => setPage('dashboard')}>
              Dashboard
            </NavTab>
            <NavTab active={page === 'rules'} onClick={() => setPage('rules')}>
              Rules
            </NavTab>
          </nav>
        </div>
        <ConnectionLED status={wsStatus} />
      </header>

      {/* Page content */}
      <main className="flex-1 min-h-0 flex flex-col">
        {page === 'dashboard' && <DashboardPage />}
        {page === 'rules' && <RulesPage />}
      </main>
    </div>
  )
}

function NavTab({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm font-medium transition-colors rounded-[4px] ${
        active
          ? 'bg-surface text-text border border-border'
          : 'text-muted hover:text-text border border-transparent'
      }`}
    >
      {children}
    </button>
  )
}

function ConnectionLED({ status }: { status: WsStatus }) {
  const isConnected = status === 'connected'
  const isConnecting = status === 'connecting'

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-muted font-mono uppercase">
        {status}
      </span>
      <span
        className={`w-2.5 h-2.5 rounded-full ${
          isConnected
            ? 'bg-signal led-active'
            : isConnecting
              ? 'bg-signal/50 animate-pulse'
              : 'bg-muted/40'
        }`}
      />
    </div>
  )
}

export default App
