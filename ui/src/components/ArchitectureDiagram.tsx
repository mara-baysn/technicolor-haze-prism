import { useState } from 'react'

export default function ArchitectureDiagram() {
  const [open, setOpen] = useState(false)

  return (
    <div className="mx-5 mt-3 border border-border rounded-sm bg-surface">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-2 text-left hover:bg-border/30 transition-colors"
      >
        <span className="text-xs font-sans font-medium text-muted uppercase tracking-wider">
          Architecture: 3-Interface Firewall Model
        </span>
        <svg
          className={`w-4 h-4 text-muted transition-transform ${open ? 'rotate-180' : ''}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1">
          <svg
            viewBox="0 0 800 340"
            className="w-full max-w-4xl mx-auto"
            xmlns="http://www.w3.org/2000/svg"
            aria-label="3-interface firewall architecture diagram"
          >
            {/* Background */}
            <rect width="800" height="340" fill="#0A0A0A" rx="4" />

            {/* Left zone: Internet / Red / Untrusted */}
            <rect x="30" y="100" width="160" height="100" rx="4" fill="none" stroke="#E53935" strokeWidth="2" />
            <text x="110" y="135" textAnchor="middle" fill="#E53935" fontSize="12" fontFamily="Inter, sans-serif" fontWeight="600">
              Internet
            </text>
            <text x="110" y="155" textAnchor="middle" fill="#E53935" fontSize="10" fontFamily="Inter, sans-serif">
              (Red / Untrusted)
            </text>
            <rect x="70" y="168" width="80" height="22" rx="3" fill="#E53935" fillOpacity="0.15" stroke="#E53935" strokeWidth="1" />
            <text x="110" y="183" textAnchor="middle" fill="#E8E6E3" fontSize="11" fontFamily="JetBrains Mono, monospace">
              VF0
            </text>

            {/* Right zone: Client / Green / Trusted */}
            <rect x="610" y="100" width="160" height="100" rx="4" fill="none" stroke="#43A047" strokeWidth="2" />
            <text x="690" y="135" textAnchor="middle" fill="#43A047" fontSize="12" fontFamily="Inter, sans-serif" fontWeight="600">
              Client
            </text>
            <text x="690" y="155" textAnchor="middle" fill="#43A047" fontSize="10" fontFamily="Inter, sans-serif">
              (Green / Trusted)
            </text>
            <rect x="650" y="168" width="80" height="22" rx="3" fill="#43A047" fillOpacity="0.15" stroke="#43A047" strokeWidth="1" />
            <text x="690" y="183" textAnchor="middle" fill="#E8E6E3" fontSize="11" fontFamily="JetBrains Mono, monospace">
              VF3
            </text>

            {/* Center box: BF3 DPU eSwitch */}
            <rect x="250" y="80" width="300" height="140" rx="4" fill="#141414" stroke="#F5C518" strokeWidth="2" />
            <text x="400" y="108" textAnchor="middle" fill="#F5C518" fontSize="13" fontFamily="Inter, sans-serif" fontWeight="700">
              BF3 DPU eSwitch
            </text>
            <text x="400" y="135" textAnchor="middle" fill="#E8E6E3" fontSize="11" fontFamily="JetBrains Mono, monospace">
              tc-flower rules
            </text>
            <rect x="340" y="148" width="120" height="22" rx="3" fill="#F5C518" fillOpacity="0.12" stroke="#F5C518" strokeWidth="1" />
            <text x="400" y="163" textAnchor="middle" fill="#F5C518" fontSize="11" fontFamily="JetBrains Mono, monospace" fontWeight="600">
              148 Gbps
            </text>

            {/* Top: Admin API (Blue/Management) */}
            <rect x="310" y="18" width="180" height="40" rx="4" fill="none" stroke="#42A5F5" strokeWidth="1.5" />
            <text x="400" y="37" textAnchor="middle" fill="#42A5F5" fontSize="11" fontFamily="Inter, sans-serif" fontWeight="600">
              Admin API :8443
            </text>
            <text x="400" y="51" textAnchor="middle" fill="#42A5F5" fontSize="9" fontFamily="Inter, sans-serif">
              (Blue / Management)
            </text>
            {/* Connector line from admin to center box */}
            <line x1="400" y1="58" x2="400" y2="80" stroke="#42A5F5" strokeWidth="1" strokeDasharray="3,3" />

            {/* Arrow: VF0 to center (left side) */}
            <line x1="190" y1="145" x2="250" y2="145" stroke="#E8E6E3" strokeWidth="1.5" markerEnd="url(#arrowhead)" />

            {/* Arrow: center to VF3 (right side) */}
            <line x1="550" y1="145" x2="610" y2="145" stroke="#E8E6E3" strokeWidth="1.5" markerEnd="url(#arrowhead)" />

            {/* Flow arrows below center */}
            {/* New flows: solid arrow */}
            <line x1="280" y1="185" x2="280" y2="195" stroke="#F5C518" strokeWidth="1" />
            <path d="M 280 195 L 280 240 L 520 240 L 520 195" fill="none" stroke="#F5C518" strokeWidth="1.5" markerEnd="url(#arrowSignal)" />
            <text x="400" y="237" textAnchor="middle" fill="#F5C518" fontSize="9" fontFamily="Inter, sans-serif">
              New flows: ARM policy check then offload to silicon
            </text>

            {/* Established flows: dotted arrow */}
            <path d="M 300 260 L 300 275 L 500 275 L 500 260" fill="none" stroke="#43A047" strokeWidth="1.5" strokeDasharray="5,3" markerEnd="url(#arrowAllow)" />
            <text x="400" y="272" textAnchor="middle" fill="#43A047" fontSize="9" fontFamily="Inter, sans-serif">
              Established flows: hardware bypass (fast path)
            </text>

            {/* Production note */}
            <text x="400" y="315" textAnchor="middle" fill="#888888" fontSize="9" fontFamily="Inter, sans-serif" fontStyle="italic">
              Production: replace with DOCA Flow CT + Tier 3 inspection VM
            </text>

            {/* Arrow markers */}
            <defs>
              <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#E8E6E3" />
              </marker>
              <marker id="arrowSignal" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#F5C518" />
              </marker>
              <marker id="arrowAllow" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
                <polygon points="0 0, 8 3, 0 6" fill="#43A047" />
              </marker>
            </defs>
          </svg>
        </div>
      )}
    </div>
  )
}
