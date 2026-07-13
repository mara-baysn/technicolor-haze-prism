/**
 * API client for the Prism orchestrator.
 *
 * All calls go through the orchestrator at http://192.168.9.16:8000/api/
 * which proxies to the real DPU firewall, traffic generator, and receiver.
 */

const API_BASE = 'http://192.168.9.16:8000/api'
const ORCHESTRATOR_WS = 'ws://192.168.9.16:8000/ws/metrics'

// --- Firewall Types ---

export interface FirewallRule {
  id: string
  dst_port?: number
  src_port?: number
  dst_ip?: string
  src_ip?: string
  protocol: string
  action: string
  priority: number
  in_hw?: boolean
  packets?: number
  bytes?: number
}

export interface FirewallMetrics {
  packets_forwarded?: number
  packets_dropped?: number
  bytes_forwarded?: number
  bytes_dropped?: number
  active_rules?: number
  [key: string]: unknown
}

// --- Traffic Generator Types ---

export interface GeneratorPortStats {
  port: number
  attempted: number
  succeeded: number
  failed: number
  bytes_sent: number
}

export interface GeneratorStats {
  running: boolean
  profile: string
  rate_cps: number
  aggregate: {
    total_attempted: number
    total_succeeded: number
    total_failed: number
    total_bytes_sent: number
    elapsed_s: number
    connections_per_sec: number
  }
  per_port: GeneratorPortStats[]
}

// --- Receiver Types ---

export interface ReceiverPortStats {
  port: number
  protocol: string
  connections: number
  packets: number
  bytes_received: number
  last_seen_ago_s: number | null
  active: boolean
}

export interface ReceiverStats {
  running: boolean
  bind_ip: string
  interface: string
  elapsed_s: number
  total_packets: number
  total_bytes: number
  total_connections: number
  ports: ReceiverPortStats[]
}

// --- WebSocket aggregated snapshot ---

export interface AggregatedMetrics {
  firewall: FirewallMetrics
  generator: GeneratorStats
  receiver: ReceiverStats
  timestamp: number
}

// --- WebSocket connection status ---

type WsStatus = 'connected' | 'disconnected' | 'connecting'

// --- API Client ---

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${res.statusText}`)
  }
  return res.json()
}

// Firewall API (proxied through orchestrator -> DPU)
export const firewallApi = {
  getRules(): Promise<FirewallRule[]> {
    return fetchJson(`${API_BASE}/firewall/rules`)
  },

  addRule(rule: {
    dst_port?: number
    protocol?: string
    action?: string
    priority?: number
  }): Promise<FirewallRule> {
    return fetchJson(`${API_BASE}/firewall/rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(rule),
    })
  },

  deleteRule(ruleId: string): Promise<unknown> {
    return fetchJson(`${API_BASE}/firewall/rules/${ruleId}`, {
      method: 'DELETE',
    })
  },

  getMetrics(): Promise<FirewallMetrics> {
    return fetchJson(`${API_BASE}/firewall/metrics`)
  },
}

// Traffic Generator API (proxied through orchestrator -> HPE ns-inet)
export const trafficApi = {
  stats(): Promise<GeneratorStats> {
    return fetchJson(`${API_BASE}/generator/stats`)
  },

  start(profile?: string, rate?: number): Promise<unknown> {
    const body: Record<string, unknown> = {}
    if (profile) body.profile = profile
    if (rate) body.rate = rate
    return fetchJson(`${API_BASE}/generator/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  },

  stop(): Promise<unknown> {
    return fetchJson(`${API_BASE}/generator/stop`, { method: 'POST' })
  },
}

// Receiver API (proxied through orchestrator -> HPE ns-client)
export const receiverApi = {
  stats(): Promise<ReceiverStats> {
    return fetchJson(`${API_BASE}/receiver/stats`)
  },
}

// Orchestrator WebSocket for aggregated real-time metrics
export class MetricsStream {
  private ws: WebSocket | null = null
  private listeners: Set<(data: AggregatedMetrics) => void> = new Set()
  private statusListeners: Set<(status: WsStatus) => void> = new Set()
  private _status: WsStatus = 'disconnected'
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null

  get status(): WsStatus {
    return this._status
  }

  private setStatus(status: WsStatus) {
    this._status = status
    this.statusListeners.forEach((fn) => fn(status))
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) return
    this.setStatus('connecting')

    this.ws = new WebSocket(ORCHESTRATOR_WS)

    this.ws.onopen = () => {
      this.setStatus('connected')
    }

    this.ws.onmessage = (event) => {
      const data: AggregatedMetrics = JSON.parse(event.data)
      this.listeners.forEach((fn) => fn(data))
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

  subscribe(fn: (data: AggregatedMetrics) => void): () => void {
    this.listeners.add(fn)
    if (this.listeners.size === 1 && this._status === 'disconnected') {
      this.connect()
    }
    return () => {
      this.listeners.delete(fn)
      if (this.listeners.size === 0) {
        this.disconnect()
      }
    }
  }

  onStatusChange(fn: (status: WsStatus) => void): () => void {
    this.statusListeners.add(fn)
    return () => {
      this.statusListeners.delete(fn)
    }
  }
}

export const metricsStream = new MetricsStream()
