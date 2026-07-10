import { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import TestRunner from './components/TestRunner'
import SessionTable from './components/SessionTable'
import Reports from './components/Reports'
import { prismClient } from './api/client'

type Tab = 'dashboard' | 'tests' | 'sessions' | 'reports'

type WsStatus = 'connected' | 'disconnected' | 'connecting'

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')
  const [wsStatus, setWsStatus] = useState<WsStatus>('disconnected')

  useEffect(() => {
    const unsub = prismClient.onStatusChange((status) => {
      setWsStatus(status)
    })
    return unsub
  }, [])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-bold">Prism Virtual Firewall</h1>
            <ConnectionIndicator status={wsStatus} />
          </div>
          <nav className="flex gap-4">
            {(['dashboard', 'tests', 'sessions', 'reports'] as Tab[]).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1 rounded transition-colors ${
                  activeTab === tab
                    ? 'bg-blue-600 text-white'
                    : 'text-gray-400 hover:text-white'
                }`}
              >
                {tab.charAt(0).toUpperCase() + tab.slice(1)}
              </button>
            ))}
          </nav>
        </div>
      </header>
      <main className="p-6">
        {activeTab === 'dashboard' && <Dashboard />}
        {activeTab === 'tests' && <TestRunner />}
        {activeTab === 'sessions' && <SessionTable />}
        {activeTab === 'reports' && <Reports />}
      </main>
    </div>
  )
}

function ConnectionIndicator({ status }: { status: WsStatus }) {
  const config = {
    connected: { color: 'bg-green-400', label: 'Connected' },
    connecting: { color: 'bg-yellow-400 animate-pulse', label: 'Connecting' },
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
