export interface MetricsSnapshot {
  tx_gbps: number
  rx_gbps: number
  offload_ratio_pct: number
  active_sessions: number
  vm_cpu_pct: number
}

export class PrismClient {
  private ws: WebSocket | null = null
  private listeners: Set<(data: MetricsSnapshot) => void> = new Set()

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws/metrics`)

    this.ws.onmessage = (event) => {
      const data: MetricsSnapshot = JSON.parse(event.data)
      this.listeners.forEach(fn => fn(data))
    }

    this.ws.onclose = () => {
      setTimeout(() => this.connect(), 3000)
    }
  }

  subscribe(fn: (data: MetricsSnapshot) => void) {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  async startTest(testId: string): Promise<void> {
    await fetch(`/api/tests/${testId}/start`, { method: 'POST' })
  }

  async getTests(): Promise<{ id: string; name: string; status: string }[]> {
    const res = await fetch('/api/tests')
    return res.json()
  }

  async setOffloadRatio(ratio: number): Promise<void> {
    await fetch('/api/controls/offload-ratio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ratio }),
    })
  }
}

export const prismClient = new PrismClient()
