import { useEffect, useState } from 'react'
import { metricsStream } from '../api/client'

export default function TopologyDiagram() {
  const [active, setActive] = useState(false)
  const [offloadPct, setOffloadPct] = useState(0)

  useEffect(() => {
    const unsub = metricsStream.subscribe((data) => {
      setActive(data.traffic.generating)
      setOffloadPct(data.firewall.offload_ratio_pct)
    })
    return unsub
  }, [])

  return (
    <div className="w-full">
      <svg viewBox="0 0 800 100" className="w-full h-20" xmlns="http://www.w3.org/2000/svg">
        {/* Definitions for animated flow */}
        <defs>
          <linearGradient id="flowGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#06b6d4" stopOpacity="0" />
            <stop offset="50%" stopColor="#06b6d4" stopOpacity="1" />
            <stop offset="100%" stopColor="#06b6d4" stopOpacity="0" />
          </linearGradient>
          <linearGradient id="offloadGrad" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#f59e0b" stopOpacity="0" />
            <stop offset="50%" stopColor="#f59e0b" stopOpacity="1" />
            <stop offset="100%" stopColor="#f59e0b" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Connection lines */}
        <line x1="170" y1="50" x2="330" y2="50" stroke="#374151" strokeWidth="2" />
        <line x1="470" y1="50" x2="630" y2="50" stroke="#374151" strokeWidth="2" />

        {/* Animated flow particles (when generating) */}
        {active && (
          <>
            <circle r="4" fill="url(#flowGrad)">
              <animateMotion dur="1.5s" repeatCount="indefinite" path="M170,50 L330,50" />
            </circle>
            <circle r="4" fill="url(#flowGrad)">
              <animateMotion dur="1.5s" repeatCount="indefinite" begin="0.5s" path="M170,50 L330,50" />
            </circle>
            <circle r="4" fill={offloadPct > 50 ? 'url(#offloadGrad)' : 'url(#flowGrad)'}>
              <animateMotion dur="1.2s" repeatCount="indefinite" path="M470,50 L630,50" />
            </circle>
            <circle r="4" fill={offloadPct > 50 ? 'url(#offloadGrad)' : 'url(#flowGrad)'}>
              <animateMotion dur="1.2s" repeatCount="indefinite" begin="0.4s" path="M470,50 L630,50" />
            </circle>
          </>
        )}

        {/* VF0 - Traffic Source */}
        <rect x="50" y="25" width="120" height="50" rx="8" fill="#1e293b" stroke="#06b6d4" strokeWidth="1.5" />
        <text x="110" y="45" textAnchor="middle" fill="#06b6d4" fontSize="11" fontWeight="bold">VF0</text>
        <text x="110" y="62" textAnchor="middle" fill="#94a3b8" fontSize="9">Traffic Gen</text>

        {/* DPU - Firewall */}
        <rect x="330" y="15" width="140" height="70" rx="10" fill="#1e293b" stroke="#f59e0b" strokeWidth="2" />
        <text x="400" y="38" textAnchor="middle" fill="#f59e0b" fontSize="11" fontWeight="bold">BlueField-3 DPU</text>
        <text x="400" y="55" textAnchor="middle" fill="#94a3b8" fontSize="9">Prism Firewall</text>
        <text x="400" y="72" textAnchor="middle" fill="#6b7280" fontSize="8">
          {offloadPct.toFixed(0)}% HW offload
        </text>

        {/* VF3 - Receiver */}
        <rect x="630" y="25" width="120" height="50" rx="8" fill="#1e293b" stroke="#10b981" strokeWidth="1.5" />
        <text x="690" y="45" textAnchor="middle" fill="#10b981" fontSize="11" fontWeight="bold">VF3</text>
        <text x="690" y="62" textAnchor="middle" fill="#94a3b8" fontSize="9">Receiver</text>

        {/* Arrow heads */}
        <polygon points="325,45 325,55 335,50" fill={active ? '#06b6d4' : '#374151'} />
        <polygon points="625,45 625,55 635,50" fill={active ? '#10b981' : '#374151'} />
      </svg>
    </div>
  )
}
