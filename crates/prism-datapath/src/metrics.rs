use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub struct PipelineMetrics {
    pub rx_packets: AtomicU64,
    pub tx_packets: AtomicU64,
    pub dropped_packets: AtomicU64,
    pub offloaded_flows: AtomicU64,
    pub active_flows: AtomicU64,
}

impl PipelineMetrics {
    pub fn snapshot(&self) -> MetricsSnapshot {
        MetricsSnapshot {
            rx_packets: self.rx_packets.load(Ordering::Relaxed),
            tx_packets: self.tx_packets.load(Ordering::Relaxed),
            dropped_packets: self.dropped_packets.load(Ordering::Relaxed),
            offloaded_flows: self.offloaded_flows.load(Ordering::Relaxed),
            active_flows: self.active_flows.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug, Clone, serde::Serialize)]
pub struct MetricsSnapshot {
    pub rx_packets: u64,
    pub tx_packets: u64,
    pub dropped_packets: u64,
    pub offloaded_flows: u64,
    pub active_flows: u64,
}
