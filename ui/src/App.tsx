import { useState } from 'react'
import Dashboard from './components/Dashboard'
import TestRunner from './components/TestRunner'
import SessionTable from './components/SessionTable'

type Tab = 'dashboard' | 'tests' | 'sessions'

function App() {
  const [activeTab, setActiveTab] = useState<Tab>('dashboard')

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-800 px-6 py-4">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold">Prism Virtual Firewall</h1>
          <nav className="flex gap-4">
            {(['dashboard', 'tests', 'sessions'] as Tab[]).map(tab => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1 rounded ${
                  activeTab === tab ? 'bg-blue-600 text-white' : 'text-gray-400 hover:text-white'
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
      </main>
    </div>
  )
}

export default App
