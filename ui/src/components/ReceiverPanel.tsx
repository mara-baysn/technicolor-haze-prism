import { useState, useEffect } from 'react'
import { metricsStream } from '../api/client'
import type { ReceiverStats } from '../api/client'

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

  // Scale bars relative to max
  const maxConns = Math.max(...ports.map((p) => p.connections ?? 0), 1)

  return (
    <div className="flex flex-col h-full">
      {/* Title */}
      <h2 className="text-sm font-semibold text-text font-sans uppercase tracking-wide mb-3">
        Destination
      </h2>

      {/* Summary counters */}
      <div className="grid grid-cols-2 gap-2 mb-3">
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider font-sans">Connections</div>
          <div className="text-base font-mono font-bold tabular-nums text-text">
            {totalConns.toLocaleString()}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider font-sans">Bytes</div>
          <div className="text-base font-mono font-bold tabular-nums text-text">
            {formatBytes(totalBytes)}
          </div>
        </div>
      </div>

      {/* Per-port breakdown */}
      <div className="flex-1 overflow-auto">
        <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5 font-sans">
          Per-Port
        </div>
        <div className="space-y-1.5">
          {ports.map((portStat) => {
            const conns = portStat.connections ?? 0
            const pct = maxConns > 0 ? (conns / maxConns) * 100 : 0

            return (
              <div key={portStat.port}>
                <div className="flex items-center justify-between mb-0.5">
                  <div className="flex items-center gap-1.5">
                    <span
                      className={`w-1.5 h-1.5 rounded-full ${portStat.active ? 'bg-allow' : 'bg-muted/30'}`}
                    />
                    <span className="text-xs font-mono text-text">:{portStat.port}</span>
                  </div>
                  <span className="text-[10px] font-mono text-muted tabular-nums">
                    {conns.toLocaleString()}
                  </span>
                </div>
                <div className="w-full h-2 bg-void rounded-sm overflow-hidden border border-border">
                  <div
                    className="h-full bg-allow/60 transition-all duration-300"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            )
          })}
          {ports.length === 0 && (
            <div className="text-xs text-muted text-center py-4 font-mono">
              waiting for data...
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function formatBytes(bytes: number | undefined | null): string {
  const b = bytes ?? 0
  if (b >= 1_000_000_000) return `${(b / 1_000_000_000).toFixed(1)} GB`
  if (b >= 1_000_000) return `${(b / 1_000_000).toFixed(1)} MB`
  if (b >= 1_000) return `${(b / 1_000).toFixed(1)} KB`
  return `${b} B`
}
