import { useState, useEffect } from 'react'
import { BarChart, Bar, XAxis, YAxis, ResponsiveContainer, Cell } from 'recharts'
import { prismClient, MetricsSnapshot } from '../api/client'

export default function MetricsPanel() {
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null)

  useEffect(() => {
    const unsub = prismClient.subscribe((data) => {
      setMetrics(data)
    })
    return unsub
  }, [])

  if (!metrics) {
    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-4">System Metrics</h3>
        <div className="text-gray-500 text-sm">Waiting for data...</div>
      </div>
    )
  }

  const vmCpu = metrics.vm_cpu_pct
  const dpuCpu = metrics.dpu_cpu_pct ?? 0
  const queueDepth = metrics.queue_depth ?? 0
  const newFlows = metrics.new_flows_sec ?? 0
  const mbufUsed = metrics.mbuf_used_pct ?? 0

  const queueData = [
    { name: 'RX Q0', value: Math.round(queueDepth * 0.4) },
    { name: 'RX Q1', value: Math.round(queueDepth * 0.3) },
    { name: 'TX Q0', value: Math.round(queueDepth * 0.2) },
    { name: 'TX Q1', value: Math.round(queueDepth * 0.1) },
  ]

  return (
    <div className="bg-gray-900 rounded-lg p-4 space-y-4">
      <h3 className="text-sm font-medium text-gray-400">System Metrics</h3>

      {/* CPU Gauges */}
      <div className="grid grid-cols-2 gap-4">
        <GaugeBar label="VM CPU" value={vmCpu} color="#3B82F6" />
        <GaugeBar label="DPU ARM" value={dpuCpu} color="#8B5CF6" />
      </div>

      {/* Queue Depth */}
      <div>
        <div className="text-xs text-gray-400 mb-1">Queue Depth</div>
        <ResponsiveContainer width="100%" height={80}>
          <BarChart data={queueData} layout="vertical">
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" width={50} tick={{ fontSize: 10, fill: '#9CA3AF' }} />
            <Bar dataKey="value" radius={[0, 4, 4, 0]}>
              {queueData.map((_, index) => (
                <Cell key={index} fill={index < 2 ? '#3B82F6' : '#10B981'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Counters */}
      <div className="grid grid-cols-2 gap-4">
        <div>
          <div className="text-xs text-gray-400">New Flows/sec</div>
          <div className="text-lg font-bold font-mono">{newFlows.toLocaleString()}</div>
        </div>
        <div>
          <div className="text-xs text-gray-400">Mbuf Used</div>
          <div className="flex items-center gap-2">
            <div className="flex-1 bg-gray-700 rounded-full h-2">
              <div
                className="h-2 rounded-full transition-all"
                style={{
                  width: `${mbufUsed}%`,
                  backgroundColor: mbufUsed > 80 ? '#EF4444' : mbufUsed > 60 ? '#F59E0B' : '#10B981',
                }}
              />
            </div>
            <span className="text-xs font-mono text-gray-300">{mbufUsed}%</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function GaugeBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="flex justify-between text-xs mb-1">
        <span className="text-gray-400">{label}</span>
        <span className="font-mono text-gray-300">{value.toFixed(1)}%</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-3">
        <div
          className="h-3 rounded-full transition-all duration-300"
          style={{ width: `${Math.min(value, 100)}%`, backgroundColor: color }}
        />
      </div>
    </div>
  )
}
