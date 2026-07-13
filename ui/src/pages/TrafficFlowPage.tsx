import { useState, useEffect } from 'react'
import { metricsStream } from '../api/client'
import type { AggregatedMetrics, FirewallRule } from '../api/client'
import { firewallApi } from '../api/client'

export default function TrafficFlowPage() {
  const [metrics, setMetrics] = useState<AggregatedMetrics | null>(null)
  const [rules, setRules] = useState<FirewallRule[]>([])
  const [hasDenyRules, setHasDenyRules] = useState(false)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setMetrics(data)
    })
    return unsub
  }, [])

  useEffect(() => {
    const loadRules = async () => {
      try {
        const res = await firewallApi.getRules()
        const rulesList = Array.isArray(res.rules) ? res.rules : []
        setRules(rulesList)
        setHasDenyRules(rulesList.some((r) => (r.action ?? '').toUpperCase() === 'DENY'))
      } catch (_e) {
        // ignore
      }
    }
    loadRules()
    const interval = setInterval(loadRules, 5000)
    return () => clearInterval(interval)
  }, [])

  const isActive = metrics?.generator?.running ?? false
  const pktsForwarded = metrics?.firewall?.packets_forwarded ?? 0
  const pktsDropped = metrics?.firewall?.packets_dropped ?? 0
  const activeRules = metrics?.firewall?.active_rules ?? rules.length

  return (
    <div className="flex flex-col h-full px-6 py-4">
      <h2 className="text-xl font-bold text-purple-400 mb-2">Traffic Flow Visualization</h2>
      <p className="text-sm text-gray-400 mb-6">
        Real-time packet flow: Internet (VF0) through the BF3 DPU Firewall to the Client (VF3)
      </p>

      {/* Main flow visualization */}
      <div className="flex-1 flex items-center justify-center">
        <svg viewBox="0 0 900 300" className="w-full max-w-4xl h-auto" xmlns="http://www.w3.org/2000/svg">
          <defs>
            {/* Animated green dot for allowed traffic */}
            <radialGradient id="greenGlow">
              <stop offset="0%" stopColor="#4ade80" stopOpacity="1" />
              <stop offset="100%" stopColor="#22c55e" stopOpacity="0" />
            </radialGradient>
            <radialGradient id="redGlow">
              <stop offset="0%" stopColor="#f87171" stopOpacity="1" />
              <stop offset="100%" stopColor="#ef4444" stopOpacity="0" />
            </radialGradient>
            {/* Flow path filter */}
            <filter id="glow">
              <feGaussianBlur stdDeviation="2" result="coloredBlur" />
              <feMerge>
                <feMergeNode in="coloredBlur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* Connection paths */}
          <path
            id="pathLeft"
            d="M 180 150 C 260 150, 300 150, 380 150"
            fill="none"
            stroke="#374151"
            strokeWidth="3"
            strokeDasharray={isActive ? 'none' : '8 4'}
          />
          <path
            id="pathRight"
            d="M 520 150 C 600 150, 640 150, 720 150"
            fill="none"
            stroke="#374151"
            strokeWidth="3"
            strokeDasharray={isActive ? 'none' : '8 4'}
          />

          {/* Internet / VF0 Box */}
          <rect x="40" y="100" width="140" height="100" rx="12" fill="#0f172a" stroke="#06b6d4" strokeWidth="2" />
          <text x="110" y="135" textAnchor="middle" fill="#06b6d4" fontSize="14" fontWeight="bold">Internet</text>
          <text x="110" y="155" textAnchor="middle" fill="#94a3b8" fontSize="11">VF0 (rep0)</text>
          <text x="110" y="178" textAnchor="middle" fill="#6b7280" fontSize="10">
            {isActive ? 'Sending' : 'Idle'}
          </text>

          {/* BF3 Firewall Box */}
          <rect x="380" y="80" width="140" height="140" rx="14" fill="#0f172a" stroke="#f59e0b" strokeWidth="2.5" />
          <text x="450" y="115" textAnchor="middle" fill="#f59e0b" fontSize="13" fontWeight="bold">BF3 Firewall</text>
          <text x="450" y="138" textAnchor="middle" fill="#94a3b8" fontSize="10">tc-flower rules</text>
          <text x="450" y="160" textAnchor="middle" fill="#6b7280" fontSize="10">
            {activeRules} rules active
          </text>
          {hasDenyRules && (
            <text x="450" y="182" textAnchor="middle" fill="#f87171" fontSize="10" fontWeight="bold">
              DENY active
            </text>
          )}
          {!hasDenyRules && (
            <text x="450" y="182" textAnchor="middle" fill="#4ade80" fontSize="10">
              ALLOW ALL
            </text>
          )}
          {/* Packet counters */}
          <text x="450" y="205" textAnchor="middle" fill="#4b5563" fontSize="9">
            fwd: {pktsForwarded.toLocaleString()} | drop: {pktsDropped.toLocaleString()}
          </text>

          {/* Client / VF3 Box */}
          <rect x="720" y="100" width="140" height="100" rx="12" fill="#0f172a" stroke="#10b981" strokeWidth="2" />
          <text x="790" y="135" textAnchor="middle" fill="#10b981" fontSize="14" fontWeight="bold">Client</text>
          <text x="790" y="155" textAnchor="middle" fill="#94a3b8" fontSize="11">VF3 (10.0.2.1)</text>
          <text x="790" y="178" textAnchor="middle" fill="#6b7280" fontSize="10">
            {isActive ? 'Receiving' : 'Idle'}
          </text>

          {/* Arrow indicators */}
          <polygon points="375,145 375,155 385,150" fill={isActive ? '#06b6d4' : '#374151'} />
          <polygon points="715,145 715,155 725,150" fill={isActive && !hasDenyRules ? '#10b981' : '#374151'} />

          {/* Animated green dots on left path (traffic in) */}
          {isActive && (
            <>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.8s" repeatCount="indefinite" path="M 180 150 C 260 150, 300 150, 380 150" />
              </circle>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.8s" repeatCount="indefinite" begin="0.6s" path="M 180 150 C 260 150, 300 150, 380 150" />
              </circle>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.8s" repeatCount="indefinite" begin="1.2s" path="M 180 150 C 260 150, 300 150, 380 150" />
              </circle>
            </>
          )}

          {/* Animated dots on right path (traffic out or blocked) */}
          {isActive && !hasDenyRules && (
            <>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.5s" repeatCount="indefinite" path="M 520 150 C 600 150, 640 150, 720 150" />
              </circle>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.5s" repeatCount="indefinite" begin="0.5s" path="M 520 150 C 600 150, 640 150, 720 150" />
              </circle>
              <circle r="5" fill="#4ade80" filter="url(#glow)">
                <animateMotion dur="1.5s" repeatCount="indefinite" begin="1.0s" path="M 520 150 C 600 150, 640 150, 720 150" />
              </circle>
            </>
          )}

          {/* Red X / blocked indicator when deny rules are active */}
          {isActive && hasDenyRules && (
            <>
              {/* Red flash at firewall exit */}
              <circle cx="520" cy="150" r="8" fill="#ef4444" opacity="0.6">
                <animate attributeName="opacity" values="0.6;0.2;0.6" dur="0.8s" repeatCount="indefinite" />
                <animate attributeName="r" values="6;10;6" dur="0.8s" repeatCount="indefinite" />
              </circle>
              {/* Red X */}
              <line x1="512" y1="142" x2="528" y2="158" stroke="#ef4444" strokeWidth="3" strokeLinecap="round">
                <animate attributeName="opacity" values="1;0.4;1" dur="0.8s" repeatCount="indefinite" />
              </line>
              <line x1="528" y1="142" x2="512" y2="158" stroke="#ef4444" strokeWidth="3" strokeLinecap="round">
                <animate attributeName="opacity" values="1;0.4;1" dur="0.8s" repeatCount="indefinite" />
              </line>
              {/* Some dots still getting through (partial deny) */}
              {pktsForwarded > 0 && (
                <circle r="4" fill="#4ade80" opacity="0.5" filter="url(#glow)">
                  <animateMotion dur="2.5s" repeatCount="indefinite" path="M 520 150 C 600 150, 640 150, 720 150" />
                </circle>
              )}
              {/* Red dots being dropped */}
              <circle r="4" fill="#ef4444" filter="url(#glow)">
                <animateMotion dur="1.2s" repeatCount="indefinite" path="M 520 150 C 540 140, 550 170, 520 180" />
                <animate attributeName="opacity" values="1;0" dur="1.2s" repeatCount="indefinite" />
              </circle>
              <circle r="4" fill="#ef4444" filter="url(#glow)">
                <animateMotion dur="1.2s" repeatCount="indefinite" begin="0.4s" path="M 520 150 C 540 160, 530 130, 510 120" />
                <animate attributeName="opacity" values="1;0" dur="1.2s" repeatCount="indefinite" />
              </circle>
            </>
          )}

          {/* Legend */}
          <circle cx="60" cy="270" r="5" fill="#4ade80" />
          <text x="75" y="274" fill="#94a3b8" fontSize="10">Allowed traffic</text>
          <circle cx="200" cy="270" r="5" fill="#ef4444" />
          <text x="215" y="274" fill="#94a3b8" fontSize="10">Blocked/dropped</text>
          <rect x="330" y="265" width="10" height="10" rx="2" fill="none" stroke="#f59e0b" strokeWidth="1.5" />
          <text x="348" y="274" fill="#94a3b8" fontSize="10">DPU hw-offload</text>
        </svg>
      </div>

      {/* Status bar at bottom */}
      <div className="mt-4 bg-gray-900/80 border border-gray-800 rounded-lg p-4 grid grid-cols-4 gap-4">
        <div>
          <div className="text-xs text-gray-400 uppercase tracking-wider">Traffic Status</div>
          <div className={`text-sm font-medium mt-1 ${isActive ? 'text-green-400' : 'text-gray-500'}`}>
            {isActive ? 'Active' : 'Idle'}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-400 uppercase tracking-wider">Deny Rules</div>
          <div className={`text-sm font-medium mt-1 ${hasDenyRules ? 'text-red-400' : 'text-green-400'}`}>
            {hasDenyRules ? `${rules.filter(r => (r.action ?? '').toUpperCase() === 'DENY').length} active` : 'None'}
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-400 uppercase tracking-wider">Packets Forwarded</div>
          <div className="text-sm font-medium mt-1 text-green-400">{pktsForwarded.toLocaleString()}</div>
        </div>
        <div>
          <div className="text-xs text-gray-400 uppercase tracking-wider">Packets Dropped</div>
          <div className="text-sm font-medium mt-1 text-red-400">{pktsDropped.toLocaleString()}</div>
        </div>
      </div>
    </div>
  )
}
