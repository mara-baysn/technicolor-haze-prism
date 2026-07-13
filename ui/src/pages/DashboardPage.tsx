import { useState, useEffect } from 'react'
import TrafficGenPanel from '../components/TrafficGenPanel'
import FirewallPanel from '../components/FirewallPanel'
import ReceiverPanel from '../components/ReceiverPanel'
import TopologyDiagram from '../components/TopologyDiagram'
import { metricsStream } from '../api/client'
import type { AggregatedMetrics } from '../api/client'

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<AggregatedMetrics | null>(null)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setMetrics(data)
    })
    return unsub
  }, [])

  const activeRules = metrics?.firewall?.active_rules ?? 0
  const pktsForwarded = metrics?.firewall?.packets_forwarded ?? 0
  const pktsDropped = metrics?.firewall?.packets_dropped ?? 0
  const bytesForwarded = metrics?.firewall?.bytes_forwarded ?? 0
  const throughputGbps = bytesForwarded > 0 ? (bytesForwarded * 8) / 1_000_000_000 : 0
  const hwOffload = activeRules > 0 ? 'Active' : 'Idle'

  return (
    <div className="flex flex-col h-full">
      {/* Stat Tiles */}
      <div className="grid grid-cols-5 gap-3 px-6 pt-4 pb-2 shrink-0">
        <StatTile label="Active Rules" value={String(activeRules)} color="text-amber-400" />
        <StatTile label="Throughput" value={`${throughputGbps.toFixed(2)} Gbps`} color="text-cyan-400" />
        <StatTile label="Pkts Forwarded" value={pktsForwarded.toLocaleString()} color="text-green-400" />
        <StatTile label="Pkts Dropped" value={pktsDropped.toLocaleString()} color="text-red-400" />
        <StatTile label="Offload Status" value={hwOffload} color="text-blue-400" />
      </div>

      {/* Topology diagram */}
      <div className="px-6 pt-2 pb-2 shrink-0">
        <TopologyDiagram />
      </div>

      {/* 3-panel layout */}
      <div className="flex-1 grid grid-cols-3 gap-4 px-6 pb-6 min-h-0">
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <TrafficGenPanel />
        </div>
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <FirewallPanel />
        </div>
        <div className="bg-gray-900/80 border border-gray-800 rounded-xl p-5 overflow-hidden flex flex-col">
          <ReceiverPanel />
        </div>
      </div>
    </div>
  )
}

function StatTile({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-gray-900/80 border border-gray-800 rounded-lg p-3">
      <div className="text-xs text-gray-400 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-lg font-mono font-bold ${color}`}>{value}</div>
    </div>
  )
}
