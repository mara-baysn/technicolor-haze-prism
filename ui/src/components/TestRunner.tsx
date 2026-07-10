import { useState, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { prismClient, TestInfo, TestResult } from '../api/client'

export default function TestRunner() {
  const queryClient = useQueryClient()
  const [runningTests, setRunningTests] = useState<Set<string>>(new Set())
  const [results, setResults] = useState<Map<string, TestResult>>(new Map())
  const [expandedTest, setExpandedTest] = useState<string | null>(null)
  const [runAllInProgress, setRunAllInProgress] = useState(false)

  const { data: tests, isLoading, error } = useQuery<TestInfo[]>({
    queryKey: ['tests'],
    queryFn: () => prismClient.getTests(),
    refetchInterval: 5000,
  })

  const runSingleTest = useCallback(async (testId: string) => {
    setRunningTests(prev => new Set(prev).add(testId))
    try {
      await prismClient.runTest(testId)
      // Poll for result
      const result = await prismClient.getTestResult(testId)
      setResults(prev => new Map(prev).set(testId, result))
      queryClient.invalidateQueries({ queryKey: ['tests'] })
    } catch {
      // Test failed to start or fetch result
    } finally {
      setRunningTests(prev => {
        const next = new Set(prev)
        next.delete(testId)
        return next
      })
    }
  }, [queryClient])

  const runAllTests = useCallback(async () => {
    if (!tests) return
    setRunAllInProgress(true)
    for (const test of tests) {
      await runSingleTest(test.id)
    }
    setRunAllInProgress(false)
  }, [tests, runSingleTest])

  const handleGenerateReport = useCallback(async () => {
    await prismClient.generateReport()
  }, [])

  const getStatusBadge = (testId: string) => {
    if (runningTests.has(testId)) {
      return (
        <span className="flex items-center gap-1 text-xs text-yellow-400">
          <Spinner /> Running
        </span>
      )
    }
    const result = results.get(testId)
    if (result) {
      const isPass = result.status === 'pass'
      return (
        <span className={`px-2 py-0.5 rounded text-xs font-medium ${
          isPass ? 'bg-green-900 text-green-300' : 'bg-red-900 text-red-300'
        }`}>
          {result.status.toUpperCase()}
        </span>
      )
    }
    return <span className="text-xs text-gray-500">Ready</span>
  }

  if (isLoading) return <div className="text-gray-500">Loading tests...</div>
  if (error) return <div className="text-red-400">Failed to load tests</div>

  const allDone = tests && tests.length > 0 && results.size === tests.length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">PoC-3 Test Scenarios</h2>
        <div className="flex gap-2">
          {allDone && (
            <button
              onClick={handleGenerateReport}
              className="px-4 py-2 bg-green-600 hover:bg-green-500 rounded-lg text-sm font-medium transition-colors"
            >
              Generate Report
            </button>
          )}
          <button
            onClick={runAllTests}
            disabled={runAllInProgress || runningTests.size > 0}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
          >
            {runAllInProgress ? 'Running All...' : 'Run All'}
          </button>
        </div>
      </div>

      <div className="grid gap-2">
        {(tests ?? []).map(test => (
          <div key={test.id} className="bg-gray-900 rounded-lg overflow-hidden">
            <div className="p-4 flex items-center justify-between">
              <div className="flex items-center gap-3">
                <span className="text-gray-400 font-mono text-sm">{test.id}</span>
                <span>{test.name}</span>
                {getStatusBadge(test.id)}
              </div>
              <div className="flex items-center gap-2">
                {results.has(test.id) && (
                  <button
                    onClick={() => setExpandedTest(expandedTest === test.id ? null : test.id)}
                    className="px-2 py-1 text-xs text-gray-400 hover:text-white transition-colors"
                  >
                    {expandedTest === test.id ? 'Hide' : 'Details'}
                  </button>
                )}
                <button
                  onClick={() => runSingleTest(test.id)}
                  disabled={runningTests.has(test.id) || runAllInProgress}
                  className="px-3 py-1 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded text-sm transition-colors"
                >
                  Run
                </button>
              </div>
            </div>

            {/* Expanded Step Results */}
            {expandedTest === test.id && results.has(test.id) && (
              <div className="border-t border-gray-800 px-4 py-3 bg-gray-950">
                <div className="text-xs text-gray-400 mb-2">
                  Duration: {results.get(test.id)!.duration_ms}ms
                </div>
                <div className="space-y-1">
                  {results.get(test.id)!.steps.map((step, i) => (
                    <div key={i} className="flex items-center gap-2 text-sm">
                      <span className={`w-2 h-2 rounded-full ${
                        step.status === 'pass' ? 'bg-green-400' :
                        step.status === 'fail' ? 'bg-red-400' :
                        'bg-gray-500'
                      }`} />
                      <span className="text-gray-300">{step.name}</span>
                      <span className="text-gray-500 text-xs ml-auto font-mono">
                        {step.duration_ms}ms
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function Spinner() {
  return (
    <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  )
}
