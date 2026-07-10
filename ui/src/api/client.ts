export interface MetricsSnapshot {
  tx_gbps: number
  rx_gbps: number
  offload_ratio_pct: number
  active_sessions: number
  vm_cpu_pct: number
  dpu_cpu_pct?: number
  queue_depth?: number
  new_flows_sec?: number
  mbuf_used_pct?: number
  timestamp?: number
}

export interface StepResult {
  name: string
  status: 'pass' | 'fail' | 'skipped'
  duration_ms: number
  message?: string
}

export interface TestResult {
  id: string
  name: string
  status: 'pass' | 'fail' | 'error'
  started_at: string
  finished_at: string
  duration_ms: number
  steps: StepResult[]
}

export interface TestInfo {
  id: string
  name: string
  status: 'ready' | 'running' | 'pass' | 'fail' | 'error'
  description?: string
}

export interface Session {
  vni: number
  src: string
  dst: string
  proto: string
  state: string
  offloaded: boolean
  packets: number
  bytes: string
}

export interface SessionsResponse {
  sessions: Session[]
  total: number
  offloaded_count: number
  software_count: number
}

export interface ReportInfo {
  id: string
  name: string
  created_at: string
  path_html: string
  path_json: string
}

type WsStatus = 'connected' | 'disconnected' | 'connecting'

export class PrismClient {
  private ws: WebSocket | null = null
  private metricsListeners: Set<(data: MetricsSnapshot) => void> = new Set()
  private statusListeners: Set<(status: WsStatus) => void> = new Set()
  private _status: WsStatus = 'disconnected'
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  get status(): WsStatus {
    return this._status
  }

  private setStatus(status: WsStatus) {
    this._status = status
    this.statusListeners.forEach(fn => fn(status))
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return
    this.setStatus('connecting')

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws/metrics`)

    this.ws.onopen = () => {
      this.setStatus('connected')
    }

    this.ws.onmessage = (event) => {
      const data: MetricsSnapshot = JSON.parse(event.data)
      this.metricsListeners.forEach(fn => fn(data))
    }

    this.ws.onclose = () => {
      this.setStatus('disconnected')
      this.reconnectTimer = setTimeout(() => this.connect(), 3000)
    }

    this.ws.onerror = () => {
      this.ws?.close()
    }
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    this.ws?.close()
    this.ws = null
    this.setStatus('disconnected')
  }

  subscribe(fn: (data: MetricsSnapshot) => void): () => void {
    this.metricsListeners.add(fn)
    // Auto-connect on first subscriber
    if (this.metricsListeners.size === 1 && this._status === 'disconnected') {
      this.connect()
    }
    return () => {
      this.metricsListeners.delete(fn)
    }
  }

  onStatusChange(fn: (status: WsStatus) => void): () => void {
    this.statusListeners.add(fn)
    return () => {
      this.statusListeners.delete(fn)
    }
  }

  async getMetricsHistory(): Promise<MetricsSnapshot[]> {
    const res = await fetch('/api/metrics/history')
    return res.json()
  }

  async getTests(): Promise<TestInfo[]> {
    const res = await fetch('/api/tests')
    return res.json()
  }

  async getTestResult(testId: string): Promise<TestResult> {
    const res = await fetch(`/api/tests/${testId}/result`)
    return res.json()
  }

  async runTest(testId: string): Promise<void> {
    await fetch(`/api/tests/${testId}/run`, { method: 'POST' })
  }

  async setOffloadRatio(ratio: number): Promise<void> {
    await fetch('/api/controls/offload-ratio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ratio }),
    })
  }

  async setWanProfile(profile: string): Promise<void> {
    await fetch('/api/controls/wan-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile }),
    })
  }

  async startTraffic(): Promise<void> {
    await fetch('/api/controls/traffic/start', { method: 'POST' })
  }

  async stopTraffic(): Promise<void> {
    await fetch('/api/controls/traffic/stop', { method: 'POST' })
  }

  async getTrafficStatus(): Promise<{ generating: boolean }> {
    const res = await fetch('/api/controls/traffic/status')
    return res.json()
  }

  async getSessions(): Promise<SessionsResponse> {
    const res = await fetch('/api/v1/firewalls/default/sessions')
    return res.json()
  }

  async flushSessions(): Promise<void> {
    await fetch('/api/v1/firewalls/default/sessions', { method: 'DELETE' })
  }

  async generateReport(): Promise<{ path: string }> {
    const res = await fetch('/api/reports/generate', { method: 'POST' })
    return res.json()
  }

  async getReports(): Promise<ReportInfo[]> {
    const res = await fetch('/api/reports')
    return res.json()
  }
}

export const prismClient = new PrismClient()
