import { useState, useEffect, useRef, useCallback } from 'react'
import { prismClient } from '../api/client'

const WAN_PROFILES = ['Clean', 'Typical WAN', 'Lossy', 'Congested'] as const
type WanProfile = typeof WAN_PROFILES[number]

export default function OffloadControls() {
  const [ratio, setRatio] = useState(80)
  const [wanProfile, setWanProfile] = useState<WanProfile>('Clean')
  const [generating, setGenerating] = useState(false)
  const [loading, setLoading] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    prismClient.getTrafficStatus().then(s => setGenerating(s.generating)).catch(() => {})
  }, [])

  const handleRatioChange = useCallback((value: number) => {
    setRatio(value)
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      prismClient.setOffloadRatio(value)
    }, 300)
  }, [])

  const handleProfileChange = useCallback((profile: WanProfile) => {
    setWanProfile(profile)
    prismClient.setWanProfile(profile)
  }, [])

  const handleStartTraffic = useCallback(async () => {
    setLoading(true)
    try {
      await prismClient.startTraffic()
      setGenerating(true)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleStopTraffic = useCallback(async () => {
    setLoading(true)
    try {
      await prismClient.stopTraffic()
      setGenerating(false)
    } finally {
      setLoading(false)
    }
  }, [])

  return (
    <div className="bg-gray-900 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-medium text-gray-400">Traffic Controls</h3>

      {/* Offload Ratio Slider */}
      <div>
        <div className="flex justify-between text-sm mb-1">
          <span className="text-gray-400">Target Offload Ratio</span>
          <span className="text-white font-mono">{ratio}%</span>
        </div>
        <input
          type="range"
          min={0}
          max={100}
          value={ratio}
          onChange={(e) => handleRatioChange(Number(e.target.value))}
          className="w-full h-2 bg-gray-700 rounded-lg appearance-none cursor-pointer accent-blue-500"
        />
      </div>

      {/* WAN Profile */}
      <div>
        <label className="text-sm text-gray-400 block mb-1">WAN Profile</label>
        <select
          value={wanProfile}
          onChange={(e) => handleProfileChange(e.target.value as WanProfile)}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-blue-500"
        >
          {WAN_PROFILES.map(p => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      {/* Traffic Generation */}
      <div className="flex items-center gap-3">
        <button
          onClick={handleStartTraffic}
          disabled={generating || loading}
          className="px-4 py-2 bg-green-600 hover:bg-green-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
        >
          Start Traffic
        </button>
        <button
          onClick={handleStopTraffic}
          disabled={!generating || loading}
          className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
        >
          Stop Traffic
        </button>
        <span className={`text-sm ${generating ? 'text-green-400' : 'text-gray-500'}`}>
          {generating ? 'Generating' : 'Stopped'}
        </span>
        {generating && (
          <span className="inline-block w-2 h-2 bg-green-400 rounded-full animate-pulse" />
        )}
      </div>
    </div>
  )
}
