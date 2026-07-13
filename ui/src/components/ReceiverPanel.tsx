import { useState, useEffect } from 'react'
import { metricsStream } from '../api/client'
import type { ReceiverStats } from '../api/client'

const PORT_LABELS: Record<number, string> = {
  80: 'HTTP',
  443: 'HTTPS',
  22: 'SSH',
  5432: 'PostgreSQL',
  53: 'DNS',
}

const PORT_COLORS: Record<number, string> = {
  80: 'bg-blue-500',
  443: 'bg-green-500',
  22: 'bg-orange-500',
  5432: 'bg-pink-500',
  53: 'bg-purple-500',
}

function getPortColor(port: number): string {
  return PORT_COLORS[port] ?? 'bg-gray-500'
}

export default function ReceiverPanel() {
  const [stats, setStats] = useState<ReceiverStats | null>(null)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      if (data.receiver && data.receiver.ports) {
        setStats(data.receiver)
      }
    })
    return unsub
  }, [])

  const ports = stats?.ports ?? []
  const totalConns = stats?.total_connections ?? 0
  const totalBytes = stats?.total_bytes ?? 0
  const isRunning = stats?.running ?? false

  // Find max connections for scaling bars
  const maxConns = Math.max(...ports.map((p) => p.connections), 1)

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-lg font-semibold text-emerald-400 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        Receiver (10.0.2.1)
      </h2>

      {/* Status */}
      <div className="mb-4 flex items-center gap-2">
        <span className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-400' : 'bg-gray-600'}`} />
        <span className="text-sm text-gray-300">
          {isRunning ? 'Listening' : 'Stopped'}
        </span>
      </div>

      {/* Total counters */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Total Connections</div>
            <div className="text-2xl font-mono font-bold text-white">
              {totalConns.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Total Bytes</div>
            <div className="text-2xl font-mono font-bold text-white">
              {formatBytes(totalBytes)}
            </div>
          </div>
        </div>
      </div>

      {/* Per-port bars */}
      <div className="flex-1 overflow-auto">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Per-Port Connections</div>
        <div className="space-y-2">
          {ports.map((portStat) => {
            const pct = maxConns > 0 ? (portStat.connections / maxConns) * 100 : 0
            const barColor = getPortColor(portStat.port)
            const label = PORT_LABELS[portStat.port] ?? ''

            return (
              <div key={portStat.port} className="group">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-sm text-gray-300 font-mono">
                    :{portStat.port}
                    {label && (
                      <span className="text-gray-500 ml-1 text-xs">({label})</span>
                    )}
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-mono text-gray-400">
                      {portStat.connections.toLocaleString()} conn
                    </span>
                    <span className={`w-2 h-2 rounded-full ${portStat.active ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
                  </div>
                </div>
                <div className="w-full h-4 bg-gray-800 rounded overflow-hidden">
                  <div
                    className={`h-full ${barColor} rounded transition-all duration-300 opacity-80 group-hover:opacity-100`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )
          })}
          {ports.length === 0 && (
            <div className="text-sm text-gray-500 text-center py-8">
              Waiting for receiver data...
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)}MB`
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(1)}KB`
  return `${bytes}B`
}
