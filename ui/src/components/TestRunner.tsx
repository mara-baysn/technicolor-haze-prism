export default function TestRunner() {
  const tests = [
    { id: 'T1', name: 'DPDK Baseline', status: 'ready' },
    { id: 'T2', name: 'Single Session Offload', status: 'ready' },
    { id: 'T3', name: 'Offload Ratio Sweep', status: 'ready' },
    { id: 'T4', name: 'Connection Storm', status: 'ready' },
    { id: 'T5', name: 'Mixed Workload', status: 'ready' },
    { id: 'T6', name: 'RSS Validation', status: 'ready' },
    { id: 'T7', name: 'Bidirectional', status: 'ready' },
    { id: 'T8', name: 'Offload Latency', status: 'ready' },
    { id: 'T9', name: 'Session Eviction', status: 'ready' },
    { id: 'T10', name: '30-min Sustained', status: 'ready' },
  ]

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-medium">PoC-3 Test Scenarios</h2>
      <div className="grid gap-2">
        {tests.map(test => (
          <div key={test.id} className="bg-gray-900 rounded-lg p-4 flex items-center justify-between">
            <div>
              <span className="text-gray-400 mr-2">{test.id}</span>
              <span>{test.name}</span>
            </div>
            <button className="px-3 py-1 bg-blue-600 rounded text-sm hover:bg-blue-500">
              Run
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
