import { useState, useEffect, useCallback } from 'react'
import { LineChart, Line, XAxis, YAxis, ResponsiveContainer, Tooltip } from 'recharts'
import { firewallApi, metricsStream } from '../api/client'
import type { FirewallRule, FirewallMetrics } from '../api/client'

interface ThroughputPoint {
  time: string
  pps: number
}

const MAX_CHART_POINTS = 60

export default function FirewallPanel() {
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [metrics, setMetrics] = useState<FirewallMetrics | null>(null)
  const [throughputHistory, setThroughputHistory] = useState<ThroughputPoint[]>([])
  const [rulesLoading, setRulesLoading] = useState(false)

  // Load rules on mount
  useEffect(() => {
    firewallApi.getRules().then(setRules).catch(() => {})
  }, [])

  // Stream metrics
  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setMetrics(data.firewall)
      setThroughputHistory((prev) => {
        const next = [
          ...prev,
          {
            time: new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }),
            pps: data.firewall.throughput_pps,
          },
        ]
        return next.slice(-MAX_CHART_POINTS)
      })
    })
    return unsub
  }, [])

  const handleToggleRule = useCallback(async (rule: FirewallRule) => {
    setRulesLoading(true)
    try {
      await firewallApi.toggleRule(rule.id, !rule.enabled)
      setRules((prev) =>
        prev.map((r) => (r.id === rule.id ? { ...r, enabled: !r.enabled } : r))
      )
    } finally {
      setRulesLoading(false)
    }
  }, [])

  const handleBlockPort80 = useCallback(async () => {
    setRulesLoading(true)
    try {
      await firewallApi.blockPort(80)
      const updated = await firewallApi.getRules()
      setRules(updated)
    } finally {
      setRulesLoading(false)
    }
  }, [])

  const offloadPct = metrics?.offload_ratio_pct ?? 0
  const activeSessions = metrics?.active_sessions ?? 0
  const hwSessions = metrics?.hw_sessions ?? 0
  const swSessions = metrics?.sw_sessions ?? 0

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-lg font-semibold text-amber-400 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        Firewall Status
      </h2>

      {/* Offload gauge */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="flex justify-between items-center mb-2">
          <span className="text-xs text-gray-400 uppercase tracking-wider">HW Offload Ratio</span>
          <span className="text-xl font-mono font-bold text-amber-300">{offloadPct.toFixed(1)}%</span>
        </div>
        <div className="w-full h-3 bg-gray-700 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-amber-600 to-amber-400 rounded-full transition-all duration-500"
            style={{ width: `${Math.min(offloadPct, 100)}%` }}
          />
        </div>
        <div className="flex justify-between text-xs text-gray-500 mt-2">
          <span>Sessions: {activeSessions.toLocaleString()}</span>
          <span>HW: {hwSessions} / SW: {swSessions}</span>
        </div>
      </div>

      {/* Throughput chart */}
      <div className="bg-gray-800/60 rounded-lg p-3 mb-4">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">Throughput (pps)</div>
        <div className="h-28">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={throughputHistory}>
              <XAxis dataKey="time" hide />
              <YAxis hide domain={['dataMin', 'dataMax']} />
              <Tooltip
                contentStyle={{ background: '#1f2937', border: '1px solid #374151', borderRadius: '6px' }}
                labelStyle={{ color: '#9ca3af' }}
                itemStyle={{ color: '#fbbf24' }}
              />
              <Line
                type="monotone"
                dataKey="pps"
                stroke="#f59e0b"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Rules list */}
      <div className="flex-1 overflow-auto">
        <div className="flex items-center justify-between mb-2">
          <span className="text-xs text-gray-400 uppercase tracking-wider">Firewall Rules</span>
          <button
            onClick={handleBlockPort80}
            disabled={rulesLoading}
            className="text-xs bg-red-700 hover:bg-red-600 text-white px-2 py-1 rounded transition-colors disabled:opacity-50"
          >
            Block Port 80
          </button>
        </div>
        <div className="space-y-1">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between bg-gray-800/40 rounded px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full ${rule.enabled ? (rule.action === 'deny' ? 'bg-red-400' : 'bg-green-400') : 'bg-gray-600'}`} />
                <span className="text-sm text-gray-200">{rule.name}</span>
                <span className="text-xs text-gray-500">
                  {rule.protocol}{rule.dst_port ? `:${rule.dst_port}` : ''}
                </span>
              </div>
              <button
                onClick={() => handleToggleRule(rule)}
                disabled={rulesLoading}
                className={`text-xs px-2 py-0.5 rounded transition-colors ${
                  rule.enabled
                    ? 'bg-green-900/60 text-green-300 hover:bg-green-800/60'
                    : 'bg-gray-700 text-gray-400 hover:bg-gray-600'
                }`}
              >
                {rule.enabled ? 'ON' : 'OFF'}
              </button>
            </div>
          ))}
          {rules.length === 0 && (
            <div className="text-sm text-gray-500 text-center py-4">No rules loaded</div>
          )}
        </div>
      </div>
    </div>
  )
}
