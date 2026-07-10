import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { prismClient, ReportInfo } from '../api/client'

export default function Reports() {
  const queryClient = useQueryClient()
  const [generatingMsg, setGeneratingMsg] = useState<string | null>(null)

  const { data: reports, isLoading, error } = useQuery<ReportInfo[]>({
    queryKey: ['reports'],
    queryFn: () => prismClient.getReports(),
    refetchInterval: 10000,
  })

  const generateMutation = useMutation({
    mutationFn: () => prismClient.generateReport(),
    onSuccess: (data) => {
      setGeneratingMsg(`Report generated: ${data.path}`)
      queryClient.invalidateQueries({ queryKey: ['reports'] })
      setTimeout(() => setGeneratingMsg(null), 5000)
    },
    onError: () => {
      setGeneratingMsg('Failed to generate report')
      setTimeout(() => setGeneratingMsg(null), 5000)
    },
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">Test Reports</h2>
        <button
          onClick={() => generateMutation.mutate()}
          disabled={generateMutation.isPending}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:bg-gray-700 disabled:text-gray-500 rounded-lg text-sm font-medium transition-colors"
        >
          {generateMutation.isPending ? 'Generating...' : 'Generate Report'}
        </button>
      </div>

      {generatingMsg && (
        <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm text-gray-300">
          {generatingMsg}
        </div>
      )}

      {isLoading && <div className="text-gray-500 text-sm">Loading reports...</div>}
      {error && <div className="text-red-400 text-sm">Failed to load reports</div>}

      {reports && reports.length === 0 && (
        <div className="bg-gray-900 rounded-lg p-6 text-center text-gray-500">
          No reports generated yet. Run tests and generate a report.
        </div>
      )}

      {reports && reports.length > 0 && (
        <div className="space-y-2">
          {reports.map((report) => (
            <div key={report.id} className="bg-gray-900 rounded-lg p-4 flex items-center justify-between">
              <div>
                <div className="font-medium">{report.name}</div>
                <div className="text-sm text-gray-400 mt-1">
                  {new Date(report.created_at).toLocaleString()}
                </div>
              </div>
              <div className="flex gap-2">
                <a
                  href={report.path_html}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-sm text-blue-400 transition-colors"
                >
                  HTML
                </a>
                <a
                  href={report.path_json}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-sm text-blue-400 transition-colors"
                >
                  JSON
                </a>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
