import { useState, useEffect, useCallback } from 'react'
import { firewallApi, metricsStream } from '../api/client'
import type { FirewallRule, FirewallMetrics } from '../api/client'

export default function FirewallPanel() {
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [metrics, setMetrics] = useState<FirewallMetrics | null>(null)

  useEffect(() => {
    loadRules()
    const interval = setInterval(loadRules, 5000)
    return () => clearInterval(interval)
  }, [])

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
    } catch (_e) { /* silent */ }
  }, [])

  const pktsForwarded = metrics?.packets_forwarded ?? 0
  const pktsDropped = metrics?.packets_dropped ?? 0
  const activeRules = metrics?.active_rules ?? rules.length

  return (
    <div className="flex flex-col h-full">
      {/* Title */}
      <div className="flex items-center gap-2 mb-3">
        <span className="w-2 h-2 rounded-full bg-signal led-active" />
        <h2 className="text-sm font-semibold text-text font-sans uppercase tracking-wide">
          DPU Firewall
        </h2>
      </div>

      {/* Counters */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <Counter label="Forwarded" value={pktsForwarded} color="text-allow" />
        <Counter label="Dropped" value={pktsDropped} color="text-deny" />
        <Counter label="Rules" value={activeRules} color="text-signal" />
      </div>

      {/* Rules list */}
      <div className="flex-1 overflow-auto">
        <div className="text-[10px] text-muted uppercase tracking-wider mb-1.5 font-sans">
          Active Rules ({rules.length})
        </div>
        <div className="space-y-0.5">
          {rules.map((rule) => (
            <RuleRow key={rule.id} rule={rule} />
          ))}
          {rules.length === 0 && (
            <div className="text-xs text-muted text-center py-4 font-mono">
              no rules -- default: deny-all
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Counter({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="text-[10px] text-muted uppercase tracking-wider font-sans">{label}</div>
      <div className={`text-base font-mono font-bold tabular-nums ${color}`}>
        {value.toLocaleString()}
      </div>
    </div>
  )
}

function RuleRow({ rule }: { rule: FirewallRule }) {
  const isDeny = (rule.action ?? '').toUpperCase() === 'DENY'

  return (
    <div className="flex items-center justify-between bg-void/50 border border-border px-2.5 py-1.5 rounded-sm">
      <div className="flex items-center gap-2">
        <span className={`text-xs font-mono font-bold ${isDeny ? 'text-deny' : 'text-allow'}`}>
          {isDeny ? 'DENY' : 'ALLOW'}
        </span>
        <span className="text-xs font-mono text-text">
          {rule.protocol ?? ''}{rule.dst_port ? `:${rule.dst_port}` : ''}
        </span>
        {rule.in_hw && <HwLed />}
      </div>
      {rule.packets != null && (
        <span className="text-[10px] font-mono text-muted tabular-nums">
          {rule.packets.toLocaleString()} pkt
        </span>
      )}
    </div>
  )
}

function HwLed() {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-sm bg-signal/10 border border-signal/30"
      title="Hardware offloaded"
    >
      <span className="w-1.5 h-1.5 rounded-full bg-signal led-active" />
      <span className="text-[9px] font-mono text-signal font-bold">in_hw</span>
    </span>
  )
}
