import { useState, useEffect, useCallback } from 'react'
import { firewallApi, metricsStream } from '../api/client'
import type { FirewallRule, FirewallMetrics } from '../api/client'

export default function RulesPage() {
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [metrics, setMetrics] = useState<FirewallMetrics | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<string | null>(null)
  const [flushConfirm, setFlushConfirm] = useState(false)
  const [defaultPolicy, setDefaultPolicy] = useState('deny-all')

  // Form state
  const [formProtocol, setFormProtocol] = useState('tcp')
  const [formSrcIp, setFormSrcIp] = useState('')
  const [formDstIp, setFormDstIp] = useState('')
  const [formSrcPort, setFormSrcPort] = useState('')
  const [formDstPort, setFormDstPort] = useState('')
  const [formAction, setFormAction] = useState('DENY')
  const [formPriority, setFormPriority] = useState('10')

  useEffect(() => {
    loadRules()
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
      setLoading(true)
      const res = await firewallApi.getRules()
      setRules(Array.isArray(res.rules) ? res.rules : [])
      setDefaultPolicy(res.default_policy ?? 'deny-all')
      setError(null)
    } catch (_e) {
      setError('Failed to load rules')
    } finally {
      setLoading(false)
    }
  }, [])

  const handleAddRule = useCallback(async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      const rule: Record<string, unknown> = {
        protocol: formProtocol,
        action: formAction,
        priority: parseInt(formPriority, 10) || 10,
      }
      if (formSrcIp) rule.src_ip = formSrcIp
      if (formDstIp) rule.dst_ip = formDstIp
      if (formSrcPort) rule.src_port = parseInt(formSrcPort, 10)
      if (formDstPort) rule.dst_port = parseInt(formDstPort, 10)

      await firewallApi.addRule(rule as Parameters<typeof firewallApi.addRule>[0])
      await loadRules()
      setShowAddForm(false)
      resetForm()
      setError(null)
    } catch (_e) {
      setError('Failed to add rule')
    } finally {
      setLoading(false)
    }
  }, [formProtocol, formSrcIp, formDstIp, formSrcPort, formDstPort, formAction, formPriority, loadRules])

  const handleDelete = useCallback(async (ruleId: string) => {
    setLoading(true)
    try {
      await firewallApi.deleteRule(ruleId)
      await loadRules()
      setDeleteConfirm(null)
      setError(null)
    } catch (_e) {
      setError('Failed to delete rule')
    } finally {
      setLoading(false)
    }
  }, [loadRules])

  const handleFlush = useCallback(async () => {
    setLoading(true)
    try {
      await firewallApi.flushRules()
      await loadRules()
      setFlushConfirm(false)
      setError(null)
    } catch (_e) {
      setError('Failed to flush rules')
    } finally {
      setLoading(false)
    }
  }, [loadRules])

  const resetForm = () => {
    setFormProtocol('tcp')
    setFormSrcIp('')
    setFormDstIp('')
    setFormSrcPort('')
    setFormDstPort('')
    setFormAction('DENY')
    setFormPriority('10')
  }

  const activeRules = metrics?.active_rules ?? rules.length

  return (
    <div className="flex flex-col h-full px-5 py-3">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-sm font-bold text-text uppercase tracking-wide">Firewall Rules</h2>
          <p className="text-sm text-muted mt-0.5 font-mono">
            default: <span className="text-deny">{defaultPolicy}</span>
            {' | '}active: <span className="text-signal">{activeRules}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="border border-signal text-signal text-sm font-bold px-3 py-1.5 rounded-[4px] transition-colors hover:bg-signal/10"
          >
            + Add Rule
          </button>
          <button
            onClick={() => setFlushConfirm(true)}
            disabled={loading || rules.length === 0}
            className="border border-deny text-deny text-sm font-bold px-3 py-1.5 rounded-[4px] transition-colors hover:bg-deny/10 disabled:border-muted/30 disabled:text-muted/40"
          >
            Flush All
          </button>
          <button
            onClick={loadRules}
            disabled={loading}
            className="border border-border text-muted text-sm px-3 py-1.5 rounded-[4px] transition-colors hover:text-text hover:border-muted disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Flush confirmation */}
      {flushConfirm && (
        <div className="bg-deny/5 border border-deny/40 rounded-sm p-3 mb-3 flex items-center justify-between">
          <span className="text-deny text-base font-mono">
            Delete ALL {rules.length} rules? Cannot undo.
          </span>
          <div className="flex gap-2">
            <button
              onClick={handleFlush}
              className="bg-deny text-void text-sm font-bold px-3 py-1 rounded-[4px]"
            >
              Confirm
            </button>
            <button
              onClick={() => setFlushConfirm(false)}
              className="border border-border text-muted text-sm px-3 py-1 rounded-[4px] hover:text-text"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Add Rule Form */}
      {showAddForm && (
        <form onSubmit={handleAddRule} className="bg-surface border border-border rounded-sm p-3 mb-3">
          <div className="grid grid-cols-7 gap-2 mb-2">
            <FormField label="Protocol">
              <select
                value={formProtocol}
                onChange={(e) => setFormProtocol(e.target.value)}
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text"
              >
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
              </select>
            </FormField>
            <FormField label="Src IP">
              <input
                type="text"
                value={formSrcIp}
                onChange={(e) => setFormSrcIp(e.target.value)}
                placeholder="any"
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text placeholder-muted/50"
              />
            </FormField>
            <FormField label="Dst IP">
              <input
                type="text"
                value={formDstIp}
                onChange={(e) => setFormDstIp(e.target.value)}
                placeholder="any"
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text placeholder-muted/50"
              />
            </FormField>
            <FormField label="Src Port">
              <input
                type="number"
                value={formSrcPort}
                onChange={(e) => setFormSrcPort(e.target.value)}
                placeholder="any"
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text placeholder-muted/50"
              />
            </FormField>
            <FormField label="Dst Port">
              <input
                type="number"
                value={formDstPort}
                onChange={(e) => setFormDstPort(e.target.value)}
                placeholder="any"
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text placeholder-muted/50"
              />
            </FormField>
            <FormField label="Action">
              <select
                value={formAction}
                onChange={(e) => setFormAction(e.target.value)}
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text"
              >
                <option value="DENY">DENY</option>
                <option value="ALLOW">ALLOW</option>
              </select>
            </FormField>
            <FormField label="Priority">
              <input
                type="number"
                value={formPriority}
                onChange={(e) => setFormPriority(e.target.value)}
                className="w-full bg-void border border-border rounded-sm px-3 py-2.5 text-base font-mono text-text"
              />
            </FormField>
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={loading}
              className="bg-signal text-void text-sm font-bold px-4 py-1.5 rounded-[4px] transition-colors hover:bg-signal/90 disabled:opacity-50"
            >
              Create Rule
            </button>
            <button
              type="button"
              onClick={() => { setShowAddForm(false); resetForm() }}
              className="border border-border text-muted text-sm px-4 py-1.5 rounded-[4px] hover:text-text"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="text-sm text-deny font-mono bg-deny/5 border border-deny/30 rounded-sm px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {/* Rules Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-void">
            <tr className="text-left text-xs text-muted uppercase tracking-wider border-b border-border">
              <th className="px-2 py-2 font-sans">ID</th>
              <th className="px-2 py-2 font-sans">Protocol</th>
              <th className="px-2 py-2 font-sans">Src IP</th>
              <th className="px-2 py-2 font-sans">Dst IP</th>
              <th className="px-2 py-2 font-sans">Src Port</th>
              <th className="px-2 py-2 font-sans">Dst Port</th>
              <th className="px-2 py-2 font-sans">Action</th>
              <th className="px-2 py-2 font-sans">Priority</th>
              <th className="px-2 py-2 font-sans">in_hw</th>
              <th className="px-2 py-2 font-sans">Packets</th>
              <th className="px-2 py-2 font-sans"></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className="border-b border-border/50 hover:bg-surface/50">
                <td className="px-3 py-2.5 font-mono text-muted">{rule.id}</td>
                <td className="px-3 py-2.5 font-mono text-text">{rule.protocol ?? '-'}</td>
                <td className="px-3 py-2.5 font-mono text-text">{rule.src_ip ?? '*'}</td>
                <td className="px-3 py-2.5 font-mono text-text">{rule.dst_ip ?? '*'}</td>
                <td className="px-3 py-2.5 font-mono text-text tabular-nums">{rule.src_port ?? '*'}</td>
                <td className="px-3 py-2.5 font-mono text-text tabular-nums">{rule.dst_port ?? '*'}</td>
                <td className="px-3 py-2.5">
                  <span className={`font-mono font-bold ${
                    (rule.action ?? '').toUpperCase() === 'DENY' ? 'text-deny' : 'text-allow'
                  }`}>
                    {(rule.action ?? 'ALLOW').toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2.5 font-mono text-muted tabular-nums">{rule.priority ?? '-'}</td>
                <td className="px-3 py-2.5">
                  {rule.in_hw ? (
                    <span className="inline-flex items-center gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-signal led-active" />
                      <span className="text-[9px] font-mono text-signal font-bold">HW</span>
                    </span>
                  ) : (
                    <span className="text-[9px] font-mono text-muted">SW</span>
                  )}
                </td>
                <td className="px-3 py-2.5 font-mono text-muted tabular-nums">
                  {(rule.packets ?? 0).toLocaleString()}
                </td>
                <td className="px-3 py-2.5">
                  {deleteConfirm === rule.id ? (
                    <div className="flex gap-1">
                      <button
                        onClick={() => handleDelete(rule.id)}
                        disabled={loading}
                        className="text-xs text-deny font-bold hover:underline disabled:opacity-50"
                      >
                        confirm
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        className="text-xs text-muted hover:text-text"
                      >
                        no
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setDeleteConfirm(rule.id)}
                      disabled={loading}
                      className="text-xs text-deny hover:underline disabled:opacity-50"
                    >
                      delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules.length === 0 && !loading && (
          <div className="text-center py-8 text-muted text-base font-mono">
            No rules configured. Default policy: <span className="text-deny">{defaultPolicy}</span>
          </div>
        )}
        {loading && rules.length === 0 && (
          <div className="text-center py-8 text-muted text-base font-mono">Loading...</div>
        )}
      </div>
    </div>
  )
}

function FormField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="text-xs text-muted uppercase tracking-wider block mb-0.5 font-sans">
        {label}
      </label>
      {children}
    </div>
  )
}
