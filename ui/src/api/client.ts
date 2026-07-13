// API endpoints for the three services
const TRAFFIC_GEN_BASE = 'http://192.168.9.23:5001/api'
const FIREWALL_BASE = 'http://192.168.0.38:8443/api/v1'
const RECEIVER_BASE = 'http://192.168.9.23:5002/api'
const ORCHESTRATOR_WS = 'ws://192.168.9.16:8000/ws/metrics'

// --- Traffic Generator Types ---

export interface TrafficProfile {
  id: string
  name: string
  description?: string
}

export interface TrafficStats {
  generating: boolean
  tx_pps: number
  tx_bps: number
  total_packets: number
  elapsed_sec: number
  profile?: string
  rate_mbps?: number
}

// --- Firewall Types ---

export interface FirewallRule {
  id: string
  name: string
  action: 'allow' | 'deny'
  protocol: string
  src_port?: number
  dst_port?: number
  src_ip?: string
  dst_ip?: string
  enabled: boolean
  hit_count: number
}

export interface FirewallMetrics {
  offload_ratio_pct: number
  active_sessions: number
  throughput_pps: number
  throughput_gbps: number
  hw_sessions: number
  sw_sessions: number
  drops_pps: number
}

export interface FirewallSession {
  id: string
  src: string
  dst: string
  proto: string
  state: string
  offloaded: boolean
  packets: number
}

// --- Receiver Types ---

export interface PortStats {
  port: number
  rx_pps: number
  rx_bps: number
  total_packets: number
  label?: string
}

export interface ReceiverStats {
  ports: PortStats[]
  total_rx_pps: number
  total_rx_bps: number
}

// --- Aggregated WebSocket message ---

export interface AggregatedMetrics {
  traffic: TrafficStats
  firewall: FirewallMetrics
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

// Traffic Generator API
export const trafficApi = {
  start(profile: string, rateMbps: number): Promise<void> {
    return fetchJson(`${TRAFFIC_GEN_BASE}/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile, rate_mbps: rateMbps }),
    })
  },

  stop(): Promise<void> {
    return fetchJson(`${TRAFFIC_GEN_BASE}/stop`, { method: 'POST' })
  },

  stats(): Promise<TrafficStats> {
    return fetchJson(`${TRAFFIC_GEN_BASE}/stats`)
  },

  profiles(): Promise<TrafficProfile[]> {
    return fetchJson(`${TRAFFIC_GEN_BASE}/profiles`)
  },
}

// Firewall Admin API
export const firewallApi = {
  getRules(): Promise<FirewallRule[]> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/rules`)
  },

  toggleRule(ruleId: string, enabled: boolean): Promise<void> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/rules/${ruleId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    })
  },

  blockPort(port: number): Promise<void> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/rules`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: `Block Port ${port}`,
        action: 'deny',
        protocol: 'tcp',
        dst_port: port,
        enabled: true,
      }),
    })
  },

  getMetrics(): Promise<FirewallMetrics> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/metrics`)
  },

  getSessions(): Promise<FirewallSession[]> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/sessions`)
  },

  flushSessions(): Promise<void> {
    return fetchJson(`${FIREWALL_BASE}/firewalls/default/sessions`, {
      method: 'DELETE',
    })
  },
}

// Receiver API
export const receiverApi = {
  stats(): Promise<ReceiverStats> {
    return fetchJson(`${RECEIVER_BASE}/stats`)
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
