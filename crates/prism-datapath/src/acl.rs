use serde::{Deserialize, Serialize};
use std::net::Ipv4Addr;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AclRule {
    pub id: String,
    pub priority: u32,
    pub src_cidr: Option<String>,
    pub dst_cidr: Option<String>,
    pub src_port_range: Option<(u16, u16)>,
    pub dst_port_range: Option<(u16, u16)>,
    pub protocol: Option<u8>,
    pub action: AclAction,
    pub offload_eligible: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AclAction {
    Allow,
    Deny,
    RateLimit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct AclVerdict {
    pub action: AclAction,
    pub offload_eligible: bool,
    pub rule_id_index: Option<usize>,
}

pub struct AclEngine {
    rules: Vec<AclRule>,
}

impl AclEngine {
    pub fn new(mut rules: Vec<AclRule>) -> Self {
        rules.sort_by(|a, b| a.priority.cmp(&b.priority));
        Self { rules }
    }

    pub fn from_json(json: &str) -> anyhow::Result<Self> {
        let rules: Vec<AclRule> = serde_json::from_str(json)?;
        Ok(Self::new(rules))
    }

    pub fn evaluate(&self, src_ip: u32, dst_ip: u32, src_port: u16, dst_port: u16, protocol: u8) -> AclVerdict {
        for (idx, rule) in self.rules.iter().enumerate() {
            if self.matches_rule(rule, src_ip, dst_ip, src_port, dst_port, protocol) {
                return AclVerdict {
                    action: rule.action,
                    offload_eligible: rule.offload_eligible,
                    rule_id_index: Some(idx),
                };
            }
        }
        // Default deny
        AclVerdict {
            action: AclAction::Deny,
            offload_eligible: false,
            rule_id_index: None,
        }
    }

    fn matches_rule(&self, rule: &AclRule, src_ip: u32, dst_ip: u32, src_port: u16, dst_port: u16, protocol: u8) -> bool {
        if let Some(ref cidr) = rule.src_cidr {
            if !Self::ip_matches_cidr(src_ip, cidr) {
                return false;
            }
        }
        if let Some(ref cidr) = rule.dst_cidr {
            if !Self::ip_matches_cidr(dst_ip, cidr) {
                return false;
            }
        }
        if let Some((lo, hi)) = rule.src_port_range {
            if src_port < lo || src_port > hi {
                return false;
            }
        }
        if let Some((lo, hi)) = rule.dst_port_range {
            if dst_port < lo || dst_port > hi {
                return false;
            }
        }
        if let Some(proto) = rule.protocol {
            if protocol != proto {
                return false;
            }
        }
        true
    }

    fn ip_matches_cidr(ip: u32, cidr: &str) -> bool {
        let parts: Vec<&str> = cidr.split('/').collect();
        if parts.len() != 2 {
            return false;
        }
        let Ok(network) = parts[0].parse::<Ipv4Addr>() else { return false };
        let Ok(prefix_len) = parts[1].parse::<u32>() else { return false };

        if prefix_len == 0 {
            return true;
        }
        let mask = !0u32 << (32 - prefix_len);
        let network_bits = u32::from(network) & mask;
        (ip & mask) == network_bits
    }

    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_rules() -> Vec<AclRule> {
        vec![
            AclRule {
                id: "allow-https".to_string(),
                priority: 10,
                src_cidr: Some("10.0.0.0/8".to_string()),
                dst_cidr: None,
                src_port_range: None,
                dst_port_range: Some((443, 443)),
                protocol: Some(6),
                action: AclAction::Allow,
                offload_eligible: true,
            },
            AclRule {
                id: "deny-all".to_string(),
                priority: 1000,
                src_cidr: None,
                dst_cidr: None,
                src_port_range: None,
                dst_port_range: None,
                protocol: None,
                action: AclAction::Deny,
                offload_eligible: false,
            },
        ]
    }

    #[test]
    fn matches_allow_https() {
        let engine = AclEngine::new(make_rules());
        // 10.0.0.1 -> 192.168.1.1:443 TCP
        let v = engine.evaluate(0x0A000001, 0xC0A80101, 54321, 443, 6);
        assert_eq!(v.action, AclAction::Allow);
        assert!(v.offload_eligible);
    }

    #[test]
    fn denies_non_https() {
        let engine = AclEngine::new(make_rules());
        // 10.0.0.1 -> 192.168.1.1:80 TCP
        let v = engine.evaluate(0x0A000001, 0xC0A80101, 54321, 80, 6);
        assert_eq!(v.action, AclAction::Deny);
    }

    #[test]
    fn denies_wrong_source() {
        let engine = AclEngine::new(make_rules());
        // 172.16.0.1 -> 192.168.1.1:443 TCP (not in 10.0.0.0/8)
        let v = engine.evaluate(0xAC100001, 0xC0A80101, 54321, 443, 6);
        assert_eq!(v.action, AclAction::Deny);
    }

    #[test]
    fn priority_ordering() {
        let engine = AclEngine::new(make_rules());
        // Lower priority number = higher precedence
        assert_eq!(engine.rule_count(), 2);
    }
}
