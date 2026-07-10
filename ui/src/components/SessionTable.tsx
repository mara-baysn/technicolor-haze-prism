import { useState, useMemo, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { prismClient, SessionsResponse } from '../api/client'

export default function SessionTable() {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState('')
  const [flushing, setFlushing] = useState(false)

  const { data, isLoading, error } = useQuery<SessionsResponse>({
    queryKey: ['sessions'],
    queryFn: () => prismClient.getSessions(),
    refetchInterval: 2000,
  })

  const filteredSessions = useMemo(() => {
    if (!data) return []
    if (!filter.trim()) return data.sessions
    const lowerFilter = filter.toLowerCase()
    return data.sessions.filter(s =>
      s.src.toLowerCase().includes(lowerFilter) ||
      s.dst.toLowerCase().includes(lowerFilter) ||
      s.vni.toString().includes(lowerFilter)
    )
  }, [data, filter])

  const handleFlush = useCallback(async () => {
    setFlushing(true)
    try {
      await prismClient.flushSessions()
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
    } finally {
      setFlushing(false)
    }
  }, [queryClient])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-medium">Active Sessions</h2>
          {data && (
            <div className="text-sm text-gray-400 mt-1">
              <span className="text-green-400">{data.offloaded_count}</span> offloaded
              {' / '}
              <span className="text-gray-300">{data.software_count}</span> software
              {' / '}
              <span className="text-white">{data.total}</span> total
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by IP or VNI..."
            className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 focus:outline-none focus:border-blue-500 w-56"
          />
          <button
            onClick={handleFlush}
            disabled={flushing}
            className="px-4 py-2 bg-red-600 hover:bg-red-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
          >
            {flushing ? 'Flushing...' : 'Flush All'}
          </button>
        </div>
      </div>

      {isLoading && <div className="text-gray-500 text-sm">Loading sessions...</div>}
      {error && <div className="text-red-400 text-sm">Failed to load sessions</div>}

      {data && (
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
              {filteredSessions.map((s, i) => (
                <tr key={i} className="border-t border-gray-800 hover:bg-gray-800/50">
                  <td className="px-4 py-2 font-mono">{s.vni}</td>
                  <td className="px-4 py-2 font-mono">{s.src}</td>
                  <td className="px-4 py-2 font-mono">{s.dst}</td>
                  <td className="px-4 py-2">{s.proto}</td>
                  <td className="px-4 py-2">{s.state}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${
                      s.offloaded ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-300'
                    }`}>
                      {s.offloaded ? 'HW' : 'SW'}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right font-mono">{s.packets.toLocaleString()}</td>
                  <td className="px-4 py-2 text-right">{s.bytes}</td>
                </tr>
              ))}
              {filteredSessions.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-4 py-6 text-center text-gray-500">
                    {filter ? 'No sessions match filter' : 'No active sessions'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
