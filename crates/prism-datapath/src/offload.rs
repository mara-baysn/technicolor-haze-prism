use crate::conntrack::{ConnState, FiveTuple};
use crate::acl::AclVerdict;

pub struct OffloadDecision {
    pub should_offload: bool,
    pub rule_id_index: Option<usize>,
}

pub struct OffloadEligibility {
    pub min_packets: u64,
}

impl Default for OffloadEligibility {
    fn default() -> Self {
        Self { min_packets: 10 }
    }
}

impl OffloadEligibility {
    pub fn evaluate(
        &self,
        conn_state: ConnState,
        verdict: &AclVerdict,
        packet_count: u64,
    ) -> OffloadDecision {
        let should_offload = conn_state == ConnState::Established
            && verdict.offload_eligible
            && packet_count >= self.min_packets;

        OffloadDecision {
            should_offload,
            rule_id_index: verdict.rule_id_index,
        }
    }
}

#[derive(Debug)]
pub struct OffloadRequest {
    pub vni: u32,
    pub tuple: FiveTuple,
    pub action: OffloadAction,
    pub bidirectional: bool,
    pub rule_id_index: Option<usize>,
}

#[derive(Debug, Clone, Copy)]
pub enum OffloadAction {
    Forward,
    Drop,
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::acl::AclAction;

    #[test]
    fn offloads_established_eligible_flow() {
        let oe = OffloadEligibility { min_packets: 5 };
        let verdict = AclVerdict {
            action: AclAction::Allow,
            offload_eligible: true,
            rule_id_index: Some(0),
        };
        let decision = oe.evaluate(ConnState::Established, &verdict, 10);
        assert!(decision.should_offload);
    }

    #[test]
    fn does_not_offload_new_flow() {
        let oe = OffloadEligibility { min_packets: 5 };
        let verdict = AclVerdict {
            action: AclAction::Allow,
            offload_eligible: true,
            rule_id_index: Some(0),
        };
        let decision = oe.evaluate(ConnState::New, &verdict, 10);
        assert!(!decision.should_offload);
    }

    #[test]
    fn does_not_offload_below_threshold() {
        let oe = OffloadEligibility { min_packets: 10 };
        let verdict = AclVerdict {
            action: AclAction::Allow,
            offload_eligible: true,
            rule_id_index: Some(0),
        };
        let decision = oe.evaluate(ConnState::Established, &verdict, 5);
        assert!(!decision.should_offload);
    }

    #[test]
    fn does_not_offload_ineligible_rule() {
        let oe = OffloadEligibility { min_packets: 5 };
        let verdict = AclVerdict {
            action: AclAction::Allow,
            offload_eligible: false,
            rule_id_index: Some(0),
        };
        let decision = oe.evaluate(ConnState::Established, &verdict, 100);
        assert!(!decision.should_offload);
    }
}
