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

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-lg font-semibold text-cyan-400 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M13 10V3L4 14h7v7l9-11h-7z" />
        </svg>
        Traffic Generator
      </h2>

      {/* Status indicator */}
      <div className="mb-4 flex items-center gap-2">
        <span className={`w-3 h-3 rounded-full ${isRunning ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
        <span className="text-sm text-gray-300">
          {isRunning ? 'Generating' : 'Idle'}
        </span>
        {stats && (
          <span className="text-xs text-gray-500 ml-auto">
            {stats.profile} @ {stats.rate_cps} cps
          </span>
        )}
      </div>

      {/* Aggregate counters */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Attempted</div>
            <div className="text-lg font-mono font-bold text-white">
              {aggregate?.total_attempted?.toLocaleString() ?? '0'}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Succeeded</div>
            <div className="text-lg font-mono font-bold text-green-400">
              {aggregate?.total_succeeded?.toLocaleString() ?? '0'}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Failed</div>
            <div className="text-lg font-mono font-bold text-red-400">
              {aggregate?.total_failed?.toLocaleString() ?? '0'}
            </div>
          </div>
        </div>
        {aggregate && (
          <div className="text-xs text-gray-500 mt-2">
            {(aggregate.connections_per_sec ?? 0).toFixed(1)} conn/s | {(aggregate.elapsed_s ?? 0).toFixed(0)}s elapsed
          </div>
        )}
      </div>

      {/* Per-port breakdown */}
      <div className="flex-1 overflow-auto mb-4">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Per-Port Stats</div>
        <div className="space-y-1">
          {perPort.map((ps) => {
            const attempted = ps.attempted ?? 0
            const succeeded = ps.succeeded ?? 0
            const failed = ps.failed ?? 0
            const successRate = attempted > 0 ? (succeeded / attempted * 100) : 0
            return (
              <div key={ps.port} className="flex items-center justify-between bg-gray-800/40 rounded px-3 py-1.5">
                <span className="text-sm font-mono text-gray-300">:{ps.port}</span>
                <div className="flex items-center gap-3 text-xs font-mono">
                  <span className="text-green-400">{succeeded}</span>
                  <span className="text-gray-500">/</span>
                  <span className="text-red-400">{failed}</span>
                  <span className={`px-1.5 py-0.5 rounded ${
                    successRate === 100 ? 'bg-green-900/40 text-green-300' :
                    successRate === 0 ? 'bg-red-900/40 text-red-300' :
                    'bg-yellow-900/40 text-yellow-300'
                  }`}>
                    {successRate.toFixed(0)}%
                  </span>
                </div>
              </div>
            )
          })}
          {perPort.length === 0 && (
            <div className="text-sm text-gray-500 text-center py-4">
              Start generator to see per-port stats
            </div>
          )}
        </div>
      </div>

      {/* Controls */}
      <div className="flex gap-2 mt-auto">
        <button
          onClick={handleStart}
          disabled={isRunning || loading}
          className="flex-1 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          Start
        </button>
        <button
          onClick={handleStop}
          disabled={!isRunning || loading}
          className="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          Stop
        </button>
      </div>
    </div>
  )
}
