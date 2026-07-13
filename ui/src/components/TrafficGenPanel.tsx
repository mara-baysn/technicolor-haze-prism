import { useState, useEffect, useCallback } from 'react'
import { trafficApi, metricsStream } from '../api/client'
import type { TrafficStats, TrafficProfile } from '../api/client'

const PROFILES_FALLBACK: TrafficProfile[] = [
  { id: 'mixed-web', name: 'Mixed Web Traffic' },
  { id: 'http-flood', name: 'HTTP Flood (Port 80)' },
  { id: 'dns-heavy', name: 'DNS Heavy' },
  { id: 'enterprise', name: 'Enterprise Mix' },
]

export default function TrafficGenPanel() {
  const [stats, setStats] = useState<TrafficStats | null>(null)
  const [profiles, setProfiles] = useState<TrafficProfile[]>(PROFILES_FALLBACK)
  const [selectedProfile, setSelectedProfile] = useState('mixed-web')
  const [rateMbps, setRateMbps] = useState(1000)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    trafficApi.profiles().then(setProfiles).catch(() => {
      /* use fallback */
    })
  }, [])

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setStats(data.traffic)
    })
    return unsub
  }, [])

  const handleStart = useCallback(async () => {
    setLoading(true)
    try {
      await trafficApi.start(selectedProfile, rateMbps)
    } finally {
      setLoading(false)
    }
  }, [selectedProfile, rateMbps])

  const handleStop = useCallback(async () => {
    setLoading(true)
    try {
      await trafficApi.stop()
    } finally {
      setLoading(false)
    }
  }, [])

  const isGenerating = stats?.generating ?? false
  const txPps = stats?.tx_pps ?? 0

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
        <span className={`w-3 h-3 rounded-full ${isGenerating ? 'bg-green-400 animate-pulse' : 'bg-gray-600'}`} />
        <span className="text-sm text-gray-300">
          {isGenerating ? 'Generating' : 'Idle'}
        </span>
      </div>

      {/* PPS counter */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="text-xs text-gray-500 uppercase tracking-wider mb-1">TX Packets/sec</div>
        <div className="text-3xl font-mono font-bold text-white">
          {formatPps(txPps)}
        </div>
        {stats && (
          <div className="text-xs text-gray-500 mt-1">
            {stats.total_packets.toLocaleString()} total | {stats.elapsed_sec.toFixed(0)}s elapsed
          </div>
        )}
      </div>

      {/* Profile selector */}
      <div className="mb-3">
        <label className="block text-xs text-gray-400 mb-1">Traffic Profile</label>
        <select
          value={selectedProfile}
          onChange={(e) => setSelectedProfile(e.target.value)}
          disabled={isGenerating}
          className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-cyan-500 disabled:opacity-50"
        >
          {profiles.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
      </div>

      {/* Rate slider */}
      <div className="mb-4">
        <label className="block text-xs text-gray-400 mb-1">
          Rate: <span className="text-cyan-400 font-mono">{rateMbps} Mbps</span>
        </label>
        <input
          type="range"
          min={100}
          max={10000}
          step={100}
          value={rateMbps}
          onChange={(e) => setRateMbps(Number(e.target.value))}
          disabled={isGenerating}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-cyan-500 disabled:opacity-50"
        />
        <div className="flex justify-between text-xs text-gray-600 mt-0.5">
          <span>100M</span>
          <span>10G</span>
        </div>
      </div>

      {/* Controls */}
      <div className="flex gap-2 mt-auto">
        <button
          onClick={handleStart}
          disabled={isGenerating || loading}
          className="flex-1 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          Start
        </button>
        <button
          onClick={handleStop}
          disabled={!isGenerating || loading}
          className="flex-1 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-500 text-white font-medium py-2 px-4 rounded transition-colors"
        >
          Stop
        </button>
      </div>
    </div>
  )
}

function formatPps(pps: number): string {
  if (pps >= 1_000_000) return `${(pps / 1_000_000).toFixed(2)}M`
  if (pps >= 1_000) return `${(pps / 1_000).toFixed(1)}K`
  return pps.toFixed(0)
}
