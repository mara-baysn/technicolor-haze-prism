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
    <div className="flex flex-col h-full px-6 py-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-bold text-amber-400">Firewall Rules</h2>
          <p className="text-sm text-gray-400 mt-1">
            Default policy: <span className="text-red-400 font-mono">{defaultPolicy}</span>
            {' '} | Active rules: <span className="text-amber-300 font-mono">{activeRules}</span>
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded transition-colors"
          >
            + Add Rule
          </button>
          <button
            onClick={() => setFlushConfirm(true)}
            disabled={loading || rules.length === 0}
            className="bg-red-700 hover:bg-red-600 text-white text-sm font-medium px-4 py-2 rounded transition-colors disabled:opacity-50"
          >
            Flush All
          </button>
          <button
            onClick={loadRules}
            disabled={loading}
            className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-4 py-2 rounded transition-colors disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
      </div>

      {/* Flush confirmation */}
      {flushConfirm && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-4 mb-4 flex items-center justify-between">
          <span className="text-red-300 text-sm">Delete ALL {rules.length} rules? This cannot be undone.</span>
          <div className="flex gap-2">
            <button
              onClick={handleFlush}
              className="bg-red-600 hover:bg-red-500 text-white text-sm px-3 py-1.5 rounded"
            >
              Confirm Flush
            </button>
            <button
              onClick={() => setFlushConfirm(false)}
              className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-3 py-1.5 rounded"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Add Rule Form */}
      {showAddForm && (
        <form onSubmit={handleAddRule} className="bg-gray-900/80 border border-gray-700 rounded-lg p-4 mb-4">
          <div className="grid grid-cols-7 gap-3 mb-3">
            <div>
              <label className="text-xs text-gray-400 block mb-1">Protocol</label>
              <select
                value={formProtocol}
                onChange={(e) => setFormProtocol(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
              >
                <option value="tcp">TCP</option>
                <option value="udp">UDP</option>
                <option value="icmp">ICMP</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Src IP</label>
              <input
                type="text"
                value={formSrcIp}
                onChange={(e) => setFormSrcIp(e.target.value)}
                placeholder="any"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-600"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Dst IP</label>
              <input
                type="text"
                value={formDstIp}
                onChange={(e) => setFormDstIp(e.target.value)}
                placeholder="any"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-600"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Src Port</label>
              <input
                type="number"
                value={formSrcPort}
                onChange={(e) => setFormSrcPort(e.target.value)}
                placeholder="any"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-600"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Dst Port</label>
              <input
                type="number"
                value={formDstPort}
                onChange={(e) => setFormDstPort(e.target.value)}
                placeholder="any"
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white placeholder-gray-600"
              />
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Action</label>
              <select
                value={formAction}
                onChange={(e) => setFormAction(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
              >
                <option value="DENY">DENY</option>
                <option value="ALLOW">ALLOW</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-gray-400 block mb-1">Priority</label>
              <input
                type="number"
                value={formPriority}
                onChange={(e) => setFormPriority(e.target.value)}
                className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-white"
              />
            </div>
          </div>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={loading}
              className="bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded transition-colors disabled:opacity-50"
            >
              Create Rule
            </button>
            <button
              type="button"
              onClick={() => { setShowAddForm(false); resetForm() }}
              className="bg-gray-700 hover:bg-gray-600 text-white text-sm px-4 py-2 rounded transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="text-sm text-red-400 bg-red-900/20 border border-red-800 rounded px-3 py-2 mb-4">
          {error}
        </div>
      )}

      {/* Rules Table */}
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-gray-950">
            <tr className="text-left text-xs text-gray-400 uppercase tracking-wider border-b border-gray-800">
              <th className="px-3 py-2">ID</th>
              <th className="px-3 py-2">Protocol</th>
              <th className="px-3 py-2">Src IP</th>
              <th className="px-3 py-2">Dst IP</th>
              <th className="px-3 py-2">Src Port</th>
              <th className="px-3 py-2">Dst Port</th>
              <th className="px-3 py-2">Action</th>
              <th className="px-3 py-2">Priority</th>
              <th className="px-3 py-2">Offload</th>
              <th className="px-3 py-2">Packets</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((rule) => (
              <tr key={rule.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                <td className="px-3 py-2 font-mono text-gray-400 text-xs">{rule.id}</td>
                <td className="px-3 py-2 font-mono text-gray-200">{rule.protocol ?? '-'}</td>
                <td className="px-3 py-2 font-mono text-gray-300">{rule.src_ip ?? '*'}</td>
                <td className="px-3 py-2 font-mono text-gray-300">{rule.dst_ip ?? '*'}</td>
                <td className="px-3 py-2 font-mono text-gray-300">{rule.src_port ?? '*'}</td>
                <td className="px-3 py-2 font-mono text-gray-300">{rule.dst_port ?? '*'}</td>
                <td className="px-3 py-2">
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${
                    (rule.action ?? '').toUpperCase() === 'DENY'
                      ? 'bg-red-900/50 text-red-300'
                      : 'bg-green-900/50 text-green-300'
                  }`}>
                    {(rule.action ?? 'ALLOW').toUpperCase()}
                  </span>
                </td>
                <td className="px-3 py-2 font-mono text-gray-400">{rule.priority ?? '-'}</td>
                <td className="px-3 py-2">
                  {rule.in_hw ? (
                    <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-green-900/50 text-green-300">
                      HW OFFLOADED
                    </span>
                  ) : (
                    <span className="inline-block px-2 py-0.5 rounded text-xs font-medium bg-yellow-900/50 text-yellow-300">
                      SOFTWARE
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 font-mono text-gray-400">{(rule.packets ?? 0).toLocaleString()}</td>
                <td className="px-3 py-2">
                  {deleteConfirm === rule.id ? (
                    <div className="flex gap-1">
                      <button
                        onClick={() => handleDelete(rule.id)}
                        disabled={loading}
                        className="text-xs bg-red-600 text-white px-2 py-1 rounded disabled:opacity-50"
                      >
                        Yes
                      </button>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        className="text-xs bg-gray-600 text-white px-2 py-1 rounded"
                      >
                        No
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => setDeleteConfirm(rule.id)}
                      disabled={loading}
                      className="text-xs text-red-400 hover:text-red-300 hover:bg-red-900/30 px-2 py-1 rounded transition-colors disabled:opacity-50"
                    >
                      Delete
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {rules.length === 0 && !loading && (
          <div className="text-center py-12 text-gray-500">
            No rules configured. Default policy: <span className="text-red-400">{defaultPolicy}</span>
          </div>
        )}
        {loading && rules.length === 0 && (
          <div className="text-center py-12 text-gray-500">Loading rules...</div>
        )}
      </div>
    </div>
  )
}
