import { useState, useEffect, useCallback, useRef } from 'react'
import {
  LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { prismClient, MetricsSnapshot } from '../api/client'
import OffloadControls from './OffloadControls'
import MetricsPanel from './MetricsPanel'

interface ChartPoint {
  time: number
  tx_gbps: number
  rx_gbps: number
  offload_ratio_pct: number
}

const MAX_POINTS = 300 // 5 min at 1Hz

export default function Dashboard() {
  const [dataPoints, setDataPoints] = useState<ChartPoint[]>([])
  const [latest, setLatest] = useState<MetricsSnapshot | null>(null)
  const counterRef = useRef(0)

  const handleMetrics = useCallback((snapshot: MetricsSnapshot) => {
    setLatest(snapshot)
    counterRef.current += 1
    const point: ChartPoint = {
      time: counterRef.current,
      tx_gbps: snapshot.tx_gbps,
      rx_gbps: snapshot.rx_gbps,
      offload_ratio_pct: snapshot.offload_ratio_pct,
    }
    setDataPoints(prev => {
      const next = [...prev, point]
      return next.length > MAX_POINTS ? next.slice(-MAX_POINTS) : next
    })
  }, [])

  useEffect(() => {
    const unsub = prismClient.subscribe(handleMetrics)
    return unsub
  }, [handleMetrics])

  return (
    <div className="space-y-6">
      {/* Stat Tiles */}
      <div className="grid grid-cols-4 gap-4">
        <StatTile
          label="Throughput"
          value={latest ? `${(latest.tx_gbps + latest.rx_gbps).toFixed(1)} Gbps` : '--'}
        />
        <StatTile
          label="Offload Ratio"
          value={latest ? `${latest.offload_ratio_pct.toFixed(0)}%` : '--'}
        />
        <StatTile
          label="Active Sessions"
          value={latest ? latest.active_sessions.toLocaleString() : '--'}
        />
        <StatTile
          label="VM CPU"
          value={latest ? `${latest.vm_cpu_pct.toFixed(1)}%` : '--'}
        />
      </div>

      <div className="grid grid-cols-3 gap-6">
        {/* Charts Column */}
        <div className="col-span-2 space-y-4">
          {/* Throughput Chart */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h2 className="text-sm font-medium text-gray-400 mb-4">
              Throughput (Gbps)
              {dataPoints.length > 0 && (
                <span className="ml-2 text-xs text-green-400">LIVE</span>
              )}
            </h2>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={dataPoints}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" stroke="#9CA3AF" tick={false} />
                <YAxis stroke="#9CA3AF" domain={[0, 'auto']} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none', borderRadius: '8px' }}
                  labelStyle={{ color: '#9CA3AF' }}
                />
                <Line
                  type="monotone"
                  dataKey="tx_gbps"
                  stroke="#3B82F6"
                  strokeWidth={2}
                  dot={false}
                  name="TX"
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="rx_gbps"
                  stroke="#10B981"
                  strokeWidth={2}
                  dot={false}
                  name="RX"
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Offload Ratio Area Chart */}
          <div className="bg-gray-900 rounded-lg p-4">
            <h2 className="text-sm font-medium text-gray-400 mb-4">Offload Ratio (%)</h2>
            <ResponsiveContainer width="100%" height={150}>
              <AreaChart data={dataPoints}>
                <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                <XAxis dataKey="time" stroke="#9CA3AF" tick={false} />
                <YAxis stroke="#9CA3AF" domain={[0, 100]} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none', borderRadius: '8px' }}
                  labelStyle={{ color: '#9CA3AF' }}
                />
                <Area
                  type="monotone"
                  dataKey="offload_ratio_pct"
                  stroke="#8B5CF6"
                  fill="#8B5CF6"
                  fillOpacity={0.2}
                  strokeWidth={2}
                  name="Offload %"
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Sidebar Column */}
        <div className="space-y-4">
          <OffloadControls />
          <MetricsPanel />
        </div>
      </div>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-gray-900 rounded-lg p-4">
      <div className="text-sm text-gray-400">{label}</div>
      <div className="text-2xl font-bold mt-1">{value}</div>
    </div>
  )
}
