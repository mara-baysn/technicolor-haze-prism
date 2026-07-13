import { useState, useEffect, useCallback } from 'react'
import { firewallApi, metricsStream } from '../api/client'
import type { FirewallRule, FirewallMetrics } from '../api/client'

export default function FirewallPanel() {
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [metrics, setMetrics] = useState<FirewallMetrics | null>(null)
  const [rulesLoading, setRulesLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load rules on mount
  useEffect(() => {
    loadRules()
  }, [])

  // Stream metrics
  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      if (data.firewall) {
        setMetrics(data.firewall)
      }
    })
    return unsub
  }, [])

  const loadRules = useCallback(async () => {
    try {
      const res = await firewallApi.getRules()
      setRules(Array.isArray(res.rules) ? res.rules : [])
      setError(null)
    } catch (_e) {
      setError('Failed to load rules')
    }
  }, [])

  const handleBlockPort80 = useCallback(async () => {
    setRulesLoading(true)
    try {
      await firewallApi.addRule({
        dst_port: 80,
        protocol: 'tcp',
        action: 'DENY',
        priority: 10,
      })
      await loadRules()
      setError(null)
    } catch (_e) {
      setError('Failed to add rule')
    } finally {
      setRulesLoading(false)
    }
  }, [loadRules])

  const handleAllowAll = useCallback(async () => {
    setRulesLoading(true)
    try {
      // Delete all deny rules
      const denyRules = rules.filter(
        (r) => (r.action ?? '').toUpperCase() === 'DENY'
      )
      for (const rule of denyRules) {
        await firewallApi.deleteRule(rule.id)
      }
      await loadRules()
      setError(null)
    } catch (_e) {
      setError('Failed to remove rules')
    } finally {
      setRulesLoading(false)
    }
  }, [rules, loadRules])

  const handleDeleteRule = useCallback(
    async (ruleId: string) => {
      setRulesLoading(true)
      try {
        await firewallApi.deleteRule(ruleId)
        await loadRules()
      } catch (_e) {
        setError('Failed to delete rule')
      } finally {
        setRulesLoading(false)
      }
    },
    [loadRules]
  )

  const pktsForwarded = metrics?.packets_forwarded ?? 0
  const pktsDropped = metrics?.packets_dropped ?? 0
  const activeRules = metrics?.active_rules ?? rules.length

  return (
    <div className="flex flex-col h-full">
      <h2 className="text-lg font-semibold text-amber-400 mb-4 flex items-center gap-2">
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
        </svg>
        DPU Firewall (tc-flower)
      </h2>

      {/* Metrics summary */}
      <div className="bg-gray-800/60 rounded-lg p-4 mb-4">
        <div className="grid grid-cols-3 gap-3">
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Forwarded</div>
            <div className="text-lg font-mono font-bold text-green-400">
              {pktsForwarded.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Dropped</div>
            <div className="text-lg font-mono font-bold text-red-400">
              {pktsDropped.toLocaleString()}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-400 uppercase tracking-wider">Active Rules</div>
            <div className="text-lg font-mono font-bold text-amber-300">
              {activeRules}
            </div>
          </div>
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 mb-4">
        <button
          onClick={handleBlockPort80}
          disabled={rulesLoading}
          className="flex-1 text-sm bg-red-700 hover:bg-red-600 text-white px-3 py-2 rounded transition-colors disabled:opacity-50"
        >
          Block Port 80
        </button>
        <button
          onClick={handleAllowAll}
          disabled={rulesLoading}
          className="flex-1 text-sm bg-green-700 hover:bg-green-600 text-white px-3 py-2 rounded transition-colors disabled:opacity-50"
        >
          Allow All
        </button>
        <button
          onClick={loadRules}
          disabled={rulesLoading}
          className="text-sm bg-gray-700 hover:bg-gray-600 text-white px-3 py-2 rounded transition-colors disabled:opacity-50"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="text-xs text-red-400 mb-2">{error}</div>
      )}

      {/* Rules list */}
      <div className="flex-1 overflow-auto">
        <div className="text-xs text-gray-400 uppercase tracking-wider mb-2">
          Active Rules ({rules.length})
        </div>
        <div className="space-y-1">
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center justify-between bg-gray-800/40 rounded px-3 py-2"
            >
              <div className="flex items-center gap-2">
                <span
                  className={`w-2 h-2 rounded-full ${
                    (rule.action ?? '').toUpperCase() === 'DENY' ? 'bg-red-400' : 'bg-green-400'
                  }`}
                />
                <span className="text-sm text-gray-200">
                  {(rule.action ?? 'ALLOW').toUpperCase()} {rule.protocol ?? ''}
                  {rule.dst_port ? `:${rule.dst_port}` : ''}
                </span>
                {rule.in_hw && (
                  <span className="text-xs bg-blue-900/60 text-blue-300 px-1.5 py-0.5 rounded">
                    in_hw
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                {rule.packets != null && (
                  <span className="text-xs text-gray-500 font-mono">
                    {rule.packets} pkts
                  </span>
                )}
                <button
                  onClick={() => handleDeleteRule(rule.id)}
                  disabled={rulesLoading}
                  className="text-xs text-red-400 hover:text-red-300 px-1.5 py-0.5 rounded hover:bg-red-900/30 transition-colors disabled:opacity-50"
                >
                  X
                </button>
              </div>
            </div>
          ))}
          {rules.length === 0 && (
            <div className="text-sm text-gray-500 text-center py-4">
              No rules — default policy: deny-all
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
