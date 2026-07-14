import { useState, useEffect } from 'react'
import TrafficGenPanel from '../components/TrafficGenPanel'
import FirewallPanel from '../components/FirewallPanel'
import ReceiverPanel from '../components/ReceiverPanel'
import ArchitectureDiagram from '../components/ArchitectureDiagram'
import { metricsStream } from '../api/client'
import type { AggregatedMetrics } from '../api/client'

export default function DashboardPage() {
  const [metrics, setMetrics] = useState<AggregatedMetrics | null>(null)
  const [throughputGbps, setThroughputGbps] = useState(0)
  const [lastBytesRef] = useState<{ value: number }>({ value: 0 })

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      const currentBytes = data?.firewall?.bytes_forwarded ?? 0
      if (lastBytesRef.value > 0 && currentBytes > lastBytesRef.value) {
        const deltaBytes = currentBytes - lastBytesRef.value
        const rateBps = deltaBytes * 8
        setThroughputGbps(rateBps / 1_000_000_000)
      } else if (currentBytes === lastBytesRef.value && currentBytes > 0) {
        setThroughputGbps(t => t * 0.5)
      }
      lastBytesRef.value = currentBytes
      setMetrics(data)
    })
    return unsub
  }, [lastBytesRef])

  const activeRules = metrics?.firewall?.active_rules ?? metrics?.firewall?.total_rules ?? 0
  const pktsForwarded = metrics?.firewall?.packets_forwarded ?? 0
  const pktsDropped = metrics?.firewall?.packets_dropped ?? 0
  const hwRules = Number(metrics?.firewall?.hw_offloaded_rules ?? 0)
  const totalConns = metrics?.receiver?.total_connections ?? 0

  return (
    <div className="flex flex-col h-full">
      {/* Stats strip */}
      <div className="grid grid-cols-5 gap-px px-5 pt-3 pb-2 shrink-0 bg-border">
        <StatCell label="Active Rules" value={String(activeRules)} accent="text-signal" />
        <StatCell label="Throughput" value={`${throughputGbps.toFixed(2)} Gbps`} accent="text-text" />
        <StatCell label="Forwarded" value={pktsForwarded.toLocaleString()} accent="text-allow" />
        <StatCell label="Dropped" value={pktsDropped.toLocaleString()} accent="text-deny" />
        <StatCell
          label="HW Offload"
          value={hwRules > 0 ? `${hwRules} rules` : 'idle'}
          accent={hwRules > 0 ? 'text-signal' : 'text-muted'}
          led={hwRules > 0}
        />
      </div>

      {/* Connections counter — thin row */}
      <div className="px-5 py-1.5 text-xs text-muted font-mono flex items-center gap-4 shrink-0 border-b border-border">
        <span>Total connections: <span className="text-text">{totalConns.toLocaleString()}</span></span>
      </div>

      {/* Architecture diagram (collapsible) */}
      <ArchitectureDiagram />

      {/* 3-column topology layout */}
      <div className="flex-1 grid grid-cols-3 gap-px px-5 pb-4 pt-3 min-h-0">
        <div className="bg-surface border border-border rounded-sm p-4 overflow-hidden flex flex-col">
          <TrafficGenPanel />
        </div>
        <div className="bg-surface border border-border rounded-sm p-4 overflow-hidden flex flex-col">
          <FirewallPanel />
        </div>
        <div className="bg-surface border border-border rounded-sm p-4 overflow-hidden flex flex-col">
          <ReceiverPanel />
        </div>
      </div>
    </div>
  )
}

function StatCell({
  label,
  value,
  accent,
  led = false,
}: {
  label: string
  value: string
  accent: string
  led?: boolean
}) {
  return (
    <div className="bg-surface px-3 py-2.5">
      <div className="text-[10px] text-muted uppercase tracking-wider font-sans mb-0.5">{label}</div>
      <div className="flex items-center gap-1.5">
        {led && (
          <span className="w-2 h-2 rounded-full bg-signal led-active inline-block shrink-0" />
        )}
        <span className={`text-lg font-mono font-bold tabular-nums ${accent}`}>{value}</span>
      </div>
    </div>
  )
}
