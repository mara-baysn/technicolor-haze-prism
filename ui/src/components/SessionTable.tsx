export default function SessionTable() {
  const sessions = [
    { vni: 10100, src: '10.0.0.1:54321', dst: '10.0.1.5:443', proto: 'TCP', state: 'ESTABLISHED', offloaded: true, packets: 15234, bytes: '22.4 MB' },
    { vni: 10100, src: '10.0.0.2:12345', dst: '10.0.1.10:80', proto: 'TCP', state: 'ESTABLISHED', offloaded: true, packets: 8921, bytes: '13.1 MB' },
    { vni: 10200, src: '10.0.0.1:33210', dst: '10.0.2.3:5432', proto: 'TCP', state: 'NEW', offloaded: false, packets: 3, bytes: '192 B' },
  ]

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">Active Sessions</h2>
      <div className="bg-gray-900 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-800">
            <tr>
              <th className="px-4 py-2 text-left text-gray-400">VNI</th>
              <th className="px-4 py-2 text-left text-gray-400">Source</th>
              <th className="px-4 py-2 text-left text-gray-400">Destination</th>
              <th className="px-4 py-2 text-left text-gray-400">Proto</th>
              <th className="px-4 py-2 text-left text-gray-400">State</th>
              <th className="px-4 py-2 text-left text-gray-400">Offloaded</th>
              <th className="px-4 py-2 text-right text-gray-400">Packets</th>
              <th className="px-4 py-2 text-right text-gray-400">Bytes</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s, i) => (
              <tr key={i} className="border-t border-gray-800">
                <td className="px-4 py-2">{s.vni}</td>
                <td className="px-4 py-2 font-mono">{s.src}</td>
                <td className="px-4 py-2 font-mono">{s.dst}</td>
                <td className="px-4 py-2">{s.proto}</td>
                <td className="px-4 py-2">{s.state}</td>
                <td className="px-4 py-2">
                  <span className={`px-2 py-0.5 rounded text-xs ${s.offloaded ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-300'}`}>
                    {s.offloaded ? 'HW' : 'SW'}
                  </span>
                </td>
                <td className="px-4 py-2 text-right font-mono">{s.packets.toLocaleString()}</td>
                <td className="px-4 py-2 text-right">{s.bytes}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
