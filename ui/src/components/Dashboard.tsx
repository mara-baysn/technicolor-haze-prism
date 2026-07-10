import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

const mockData = Array.from({ length: 30 }, (_, i) => ({
  time: i,
  throughput: 40 + Math.random() * 60,
  offloadRatio: 70 + Math.random() * 20,
}))

export default function Dashboard() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-4 gap-4">
        <StatTile label="Throughput" value="92.4 Gbps" />
        <StatTile label="Offload Ratio" value="81%" />
        <StatTile label="Active Sessions" value="1,247,832" />
        <StatTile label="VM CPU" value="34%" />
      </div>
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-medium text-gray-400 mb-4">Throughput (Gbps)</h2>
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={mockData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="time" stroke="#9CA3AF" />
            <YAxis stroke="#9CA3AF" />
            <Tooltip contentStyle={{ backgroundColor: '#1F2937', border: 'none' }} />
            <Line type="monotone" dataKey="throughput" stroke="#3B82F6" strokeWidth={2} dot={false} />
            <Line type="monotone" dataKey="offloadRatio" stroke="#10B981" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
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
