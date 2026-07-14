import { useState, useEffect, useCallback } from 'react'
import { trafficApi, metricsStream } from '../api/client'
import type { GeneratorStats } from '../api/client'

export default function TrafficGenPanel() {
  const [stats, setStats] = useState<GeneratorStats | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      if (data.generator && data.generator.running !== undefined) {
        setStats(data.generator)
      }
    })
    return unsub
  }, [])

  const handleStart = useCallback(async () => {
    setLoading(true)
    try {
      await trafficApi.start('mixed')
    } finally {
      setLoading(false)
    }
  }, [])

  const handleStop = useCallback(async () => {
    setLoading(true)
    try {
      await trafficApi.stop()
    } finally {
      setLoading(false)
    }
  }, [])

  const isRunning = stats?.running ?? false
  const aggregate = stats?.aggregate
  const perPort = stats?.per_port ?? []

  // Find max attempted for bar scaling
  const maxAttempted = Math.max(...perPort.map((p) => p.attempted), 1)

  return (
    <div className="flex flex-col h-full">
      {/* Title */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-text font-sans uppercase tracking-wide">
          Traffic Source
        </h2>
        <div className="flex items-center gap-1.5">
          <span className={`w-2 h-2 rounded-full ${isRunning ? 'bg-allow' : 'bg-muted/40'}`} />
          <span className="text-[10px] font-mono text-muted">
            {isRunning ? `${stats?.rate_cps ?? 0} cps` : 'idle'}
          </span>
        </div>
      </div>

      {/* Aggregate counters */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider font-sans">Attempted</div>
          <div className="text-base font-mono font-bold tabular-nums text-text">
            {aggregate?.total_attempted?.toLocaleString() ?? '0'}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider font-sans">Succeeded</div>
          <div className="text-base font-mono font-bold tabular-nums text-allow">
            {aggregate?.total_succeeded?.toLocaleString() ?? '0'}
          </div>
        </div>
        <div>
          <div className="text-[10px] text-muted uppercase tracking-wider font-sans">Failed</div>
          <div className="text-base font-mono font-bold tabular-nums text-deny">
            {aggregate?.total_failed?.toLocaleString() ?? '0'}
          </div>
        </div>
      </div>

      {/* Rate info */}
      {aggregate && (
        <div className="text-[10px] text-muted font-mono mb-3 border-b border-border pb-2">
          {(aggregate.connections_per_sec ?? 0).toFixed(1)} conn/s | {(aggregate.elapsed_s ?? 0).toFixed(0)}s elapsed
        </div>
      )}

      {/* Per-port breakdown */}
      <div className="flex-1 overflow-auto mb-3">
        <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5 font-sans">
          Per-Port
        </div>
        <div className="space-y-1">
          {perPort.map((ps) => {
            const pct = maxAttempted > 0 ? (ps.attempted / maxAttempted) * 100 : 0
            return (
              <div key={ps.port} className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted w-10 text-right shrink-0">:{ps.port}</span>
                <div className="flex-1 h-3 bg-void rounded-sm overflow-hidden border border-border">
                  <div
                    className="h-full bg-signal/70 transition-all duration-300"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-[10px] font-mono text-muted tabular-nums w-8 text-right">
                  {pct.toFixed(0)}%
                </span>
              </div>
            )
          })}
          {perPort.length === 0 && (
            <div className="text-xs text-muted text-center py-3 font-mono">
              start to see per-port stats
            </div>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex gap-2 mt-auto">
        <button
          onClick={handleStart}
          disabled={isRunning || loading}
          className="flex-1 border border-signal text-signal text-xs font-bold py-1.5 px-3 rounded-[4px] transition-colors hover:bg-signal/10 disabled:border-muted/30 disabled:text-muted/40"
        >
          START
        </button>
        <button
          onClick={handleStop}
          disabled={!isRunning || loading}
          className="flex-1 border border-deny text-deny text-xs font-bold py-1.5 px-3 rounded-[4px] transition-colors hover:bg-deny/10 disabled:border-muted/30 disabled:text-muted/40"
        >
          STOP
        </button>
      </div>
    </div>
  )
}
