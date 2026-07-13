import { useState, useEffect } from 'react'
import { metricsStream } from '../api/client'
import type { ReceiverStats } from '../api/client'

const PORT_COLORS: Record<number, string> = {
  80: 'bg-blue-500',
  443: 'bg-green-500',
  53: 'bg-purple-500',
  22: 'bg-orange-500',
  8080: 'bg-cyan-500',
  3306: 'bg-pink-500',
}

function getPortColor(port: number): string {
  return PORT_COLORS[port] ?? 'bg-gray-500'
}

export default function ReceiverPanel() {
  const [stats, setStats] = useState<ReceiverStats | null>(null)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setStats(data.receiver)
    })
    return unsub
  }, [])

  const ports = stats?.ports ?? []
  const totalRxPps = stats?.total_rx_pps ?? 0

  // Find max pps for scaling bars
  const maxPps = Math.max(...ports.map((p) => p.rx_pps), 1)

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-lg font-semibold text-emerald-400 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
        </svg>
        Receiver Stats
      </h2>

      {/* Total counter */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">Total RX Packets/sec</div>
        <div className="text-3xl font-mono font-bold text-white">
          {formatPps(totalRxPps)}
        </div>
      </div>

      {/* Per-port bars */}
      <div className="flex-1 overflow-auto">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Per-Port Counters</div>
        <div className="space-y-2">
          {ports.map((portStat) => {
            const pct = maxPps > 0 ? (portStat.rx_pps / maxPps) * 100 : 0
            const barColor = getPortColor(portStat.port)

            return (
              <div key={portStat.port} className="group">
                <div className="flex items-center justify-between mb-0.5">
                  <span className="text-sm text-gray-300 font-mono">
                    :{portStat.port}
                    {portStat.label && (
                      <span className="text-gray-500 ml-1 text-xs">({portStat.label})</span>
                    )}
                  </span>
                  <span className="text-xs font-mono text-gray-400">
                    {formatPps(portStat.rx_pps)}
                  </span>
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

function formatPps(pps: number): string {
  if (pps >= 1_000_000) return `${(pps / 1_000_000).toFixed(2)}M`
  if (pps >= 1_000) return `${(pps / 1_000).toFixed(1)}K`
  return pps.toFixed(0)
}
