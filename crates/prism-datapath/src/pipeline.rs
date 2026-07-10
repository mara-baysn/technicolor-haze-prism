use std::collections::HashSet;
use std::sync::atomic::Ordering;
use std::sync::Arc;

use tokio::sync::mpsc;

use crate::acl::{AclAction, AclEngine};
use crate::conntrack::{ConnState, ConnTracker, FiveTuple};
use crate::metrics::PipelineMetrics;
use crate::offload::{OffloadAction, OffloadEligibility, OffloadRequest};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PacketVerdict {
    Forward,
    Drop,
    OffloadAndForward { vni: u32, rule_id_index: Option<usize> },
}

pub struct Pipeline {
    pub conntrack: ConnTracker,
    pub acl: AclEngine,
    pub offload_eligibility: OffloadEligibility,
    pub metrics: Arc<PipelineMetrics>,
    offload_tx: mpsc::Sender<OffloadRequest>,
    offloaded_flows: HashSet<(u32, FiveTuple)>,
}

impl Pipeline {
    pub fn new(
        acl: AclEngine,
        offload_min_packets: u64,
        idle_timeout_secs: u64,
        metrics: Arc<PipelineMetrics>,
        offload_tx: mpsc::Sender<OffloadRequest>,
    ) -> Self {
        Self {
            conntrack: ConnTracker::new(idle_timeout_secs),
            acl,
            offload_eligibility: OffloadEligibility {
                min_packets: offload_min_packets,
            },
            metrics,
            offload_tx,
            offloaded_flows: HashSet::new(),
        }
    }

    #[allow(clippy::too_many_arguments)]
    pub fn process_packet(
        &mut self,
        vni: u32,
        src_ip: u32,
        dst_ip: u32,
        src_port: u16,
        dst_port: u16,
        protocol: u8,
        pkt_len: u64,
    ) -> PacketVerdict {
        self.metrics.rx_packets.fetch_add(1, Ordering::Relaxed);

        let tuple = FiveTuple {
            src_ip,
            dst_ip,
            src_port,
            dst_port,
            protocol,
        };

        // Step 1: conntrack lookup
        let conn_state = self.conntrack.lookup(vni, &tuple, pkt_len);

        // Step 2: if already offloaded, just forward
        let flow_key = (vni, tuple.clone());
        if conn_state == ConnState::Established && self.offloaded_flows.contains(&flow_key) {
            self.metrics.tx_packets.fetch_add(1, Ordering::Relaxed);
            return PacketVerdict::Forward;
        }

        // Step 3: ACL evaluation
        let acl_verdict = self.acl.evaluate(src_ip, dst_ip, src_port, dst_port, protocol);

        // Step 4: If denied, drop
        if acl_verdict.action == AclAction::Deny {
            self.metrics.dropped_packets.fetch_add(1, Ordering::Relaxed);
            return PacketVerdict::Drop;
        }

        // Step 5: Check offload eligibility
        let packet_count = self.conntrack.get_packet_count(vni, &tuple);
        let decision = self
            .offload_eligibility
            .evaluate(conn_state, &acl_verdict, packet_count);

        if decision.should_offload {
            // Send offload request via channel (best-effort, non-blocking)
            let offload_req = OffloadRequest {
                vni,
                tuple: tuple.clone(),
                action: OffloadAction::Forward,
                bidirectional: true,
                rule_id_index: decision.rule_id_index,
            };
            // Use try_send to avoid blocking the datapath
            let _ = self.offload_tx.try_send(offload_req);
            self.offloaded_flows.insert(flow_key);
            self.metrics.offloaded_flows.fetch_add(1, Ordering::Relaxed);
            self.metrics.tx_packets.fetch_add(1, Ordering::Relaxed);
            return PacketVerdict::OffloadAndForward {
                vni,
                rule_id_index: decision.rule_id_index,
            };
        }

        // Step 6: Otherwise forward
        self.metrics.tx_packets.fetch_add(1, Ordering::Relaxed);
        PacketVerdict::Forward
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::acl::{AclAction, AclRule};

    fn allow_all_rules() -> Vec<AclRule> {
        vec![AclRule {
            id: "allow-all".to_string(),
            priority: 1000,
            src_cidr: None,
            dst_cidr: None,
            src_port_range: None,
            dst_port_range: None,
            protocol: None,
            action: AclAction::Allow,
            offload_eligible: true,
        }]
    }

    fn deny_all_rules() -> Vec<AclRule> {
        vec![AclRule {
            id: "deny-all".to_string(),
            priority: 1,
            src_cidr: None,
            dst_cidr: None,
            src_port_range: None,
            dst_port_range: None,
            protocol: None,
            action: AclAction::Deny,
            offload_eligible: false,
        }]
    }

    fn make_pipeline(rules: Vec<AclRule>, min_packets: u64) -> (Pipeline, mpsc::Receiver<OffloadRequest>) {
        let (tx, rx) = mpsc::channel(1024);
        let metrics = Arc::new(PipelineMetrics::default());
        let acl = AclEngine::new(rules);
        let pipeline = Pipeline::new(acl, min_packets, 300, metrics, tx);
        (pipeline, rx)
    }

    #[test]
    fn new_flow_allowed_forwards() {
        let (mut pipeline, _rx) = make_pipeline(allow_all_rules(), 10);
        let verdict = pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        assert_eq!(verdict, PacketVerdict::Forward);
    }

    #[test]
    fn acl_deny_drops_packet() {
        let (mut pipeline, _rx) = make_pipeline(deny_all_rules(), 10);
        let verdict = pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 80, 6, 64);
        assert_eq!(verdict, PacketVerdict::Drop);
        assert_eq!(pipeline.metrics.dropped_packets.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn offload_triggered_after_threshold() {
        let (mut pipeline, mut rx) = make_pipeline(allow_all_rules(), 5);

        // Send packets: packet 1 is New, packet 2+ is Established.
        // At packet 5, count reaches threshold → offload triggered.
        for i in 1..5 {
            let verdict =
                pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
            // Packets 1-4: not yet at threshold
            assert_eq!(verdict, PacketVerdict::Forward, "packet {} should forward", i);
        }

        // 5th packet: established (since packet 2) and count=5 >= threshold=5 → offload
        let verdict = pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        assert_eq!(
            verdict,
            PacketVerdict::OffloadAndForward {
                vni: 100,
                rule_id_index: Some(0),
            }
        );

        // Verify offload request was sent via channel
        let req = rx.try_recv().unwrap();
        assert_eq!(req.vni, 100);
        assert!(req.bidirectional);
    }

    #[test]
    fn offload_not_triggered_below_threshold() {
        let (mut pipeline, mut rx) = make_pipeline(allow_all_rules(), 100);

        // Send 10 packets — well below threshold of 100
        for _ in 0..10 {
            pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        }

        // All should forward without offloading
        assert!(rx.try_recv().is_err());
        assert_eq!(pipeline.metrics.offloaded_flows.load(Ordering::Relaxed), 0);
    }

    #[test]
    fn already_offloaded_flow_forwards() {
        let (mut pipeline, _rx) = make_pipeline(allow_all_rules(), 3);

        // Trigger offload first
        for _ in 0..4 {
            pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        }

        // Now subsequent packets should just Forward
        let verdict = pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        assert_eq!(verdict, PacketVerdict::Forward);
    }

    #[test]
    fn metrics_count_correctly() {
        let (mut pipeline, _rx) = make_pipeline(allow_all_rules(), 1000);

        pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);
        pipeline.process_packet(100, 0x0A000001, 0x0A000002, 12345, 443, 6, 64);

        assert_eq!(pipeline.metrics.rx_packets.load(Ordering::Relaxed), 2);
        assert_eq!(pipeline.metrics.tx_packets.load(Ordering::Relaxed), 2);
        assert_eq!(pipeline.metrics.dropped_packets.load(Ordering::Relaxed), 0);
    }
}
