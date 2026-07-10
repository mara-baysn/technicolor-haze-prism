use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::models::*;

/// In-memory state store with generation tracking for optimistic concurrency.
#[derive(Debug)]
pub struct AppStore {
    pub firewalls: RwLock<HashMap<String, Firewall>>,
    pub policies: RwLock<HashMap<String, Policy>>,
    pub rules: RwLock<HashMap<String, Rule>>,
    generation: AtomicU64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StoreError {
    NotFound(String),
    Conflict { expected: u64, actual: u64 },
}

impl AppStore {
    pub fn new() -> Arc<Self> {
        Arc::new(Self {
            firewalls: RwLock::new(HashMap::new()),
            policies: RwLock::new(HashMap::new()),
            rules: RwLock::new(HashMap::new()),
            generation: AtomicU64::new(1),
        })
    }

    /// Bump and return new generation number.
    fn next_generation(&self) -> u64 {
        self.generation.fetch_add(1, Ordering::SeqCst)
    }

    /// Get current generation without bumping.
    pub fn current_generation(&self) -> u64 {
        self.generation.load(Ordering::SeqCst)
    }

    // --- Firewall CRUD ---

    pub async fn create_firewall(&self, req: CreateFirewallRequest) -> Firewall {
        let gen = self.next_generation();
        let now = chrono_now();
        let fw = Firewall {
            id: uuid::Uuid::new_v4().to_string(),
            name: req.name,
            tenant_id: req.tenant_id,
            vpc_id: req.vpc_id,
            vni: req.vni,
            mode: req.mode,
            admin_state: req.admin_state,
            generation: gen,
            created_at: now.clone(),
            updated_at: now,
        };
        self.firewalls.write().await.insert(fw.id.clone(), fw.clone());
        fw
    }

    pub async fn get_firewall(&self, id: &str) -> Result<Firewall, StoreError> {
        self.firewalls
            .read()
            .await
            .get(id)
            .cloned()
            .ok_or_else(|| StoreError::NotFound(format!("firewall {id} not found")))
    }

    pub async fn list_firewalls(&self) -> Vec<Firewall> {
        self.firewalls.read().await.values().cloned().collect()
    }

    pub async fn update_firewall(
        &self,
        id: &str,
        req: UpdateFirewallRequest,
    ) -> Result<Firewall, StoreError> {
        let mut map = self.firewalls.write().await;
        let fw = map
            .get_mut(id)
            .ok_or_else(|| StoreError::NotFound(format!("firewall {id} not found")))?;

        // Optimistic concurrency check
        if fw.generation != req.generation {
            return Err(StoreError::Conflict {
                expected: req.generation,
                actual: fw.generation,
            });
        }

        if let Some(name) = req.name {
            fw.name = name;
        }
        if let Some(mode) = req.mode {
            fw.mode = mode;
        }
        if let Some(admin_state) = req.admin_state {
            fw.admin_state = admin_state;
        }

        fw.generation = self.next_generation();
        fw.updated_at = chrono_now();

        Ok(fw.clone())
    }

    pub async fn delete_firewall(&self, id: &str) -> Result<Firewall, StoreError> {
        self.firewalls
            .write()
            .await
            .remove(id)
            .ok_or_else(|| StoreError::NotFound(format!("firewall {id} not found")))
    }

    // --- Policy CRUD ---

    pub async fn create_policy(
        &self,
        firewall_id: &str,
        req: CreatePolicyRequest,
    ) -> Result<Policy, StoreError> {
        // Verify firewall exists
        if !self.firewalls.read().await.contains_key(firewall_id) {
            return Err(StoreError::NotFound(format!(
                "firewall {firewall_id} not found"
            )));
        }

        let gen = self.next_generation();
        let policy = Policy {
            id: uuid::Uuid::new_v4().to_string(),
            firewall_id: firewall_id.to_string(),
            name: req.name,
            priority: req.priority,
            default_action: req.default_action,
            generation: gen,
        };
        self.policies
            .write()
            .await
            .insert(policy.id.clone(), policy.clone());
        Ok(policy)
    }

    pub async fn list_policies(&self, firewall_id: &str) -> Result<Vec<Policy>, StoreError> {
        if !self.firewalls.read().await.contains_key(firewall_id) {
            return Err(StoreError::NotFound(format!(
                "firewall {firewall_id} not found"
            )));
        }
        let policies: Vec<Policy> = self
            .policies
            .read()
            .await
            .values()
            .filter(|p| p.firewall_id == firewall_id)
            .cloned()
            .collect();
        Ok(policies)
    }

    // --- Rule CRUD ---

    pub async fn create_rule(
        &self,
        policy_id: &str,
        req: CreateRuleRequest,
    ) -> Result<Rule, StoreError> {
        // Verify policy exists
        if !self.policies.read().await.contains_key(policy_id) {
            return Err(StoreError::NotFound(format!(
                "policy {policy_id} not found"
            )));
        }

        let rule = Rule {
            id: uuid::Uuid::new_v4().to_string(),
            policy_id: policy_id.to_string(),
            priority: req.priority,
            src_cidr: req.src_cidr,
            dst_cidr: req.dst_cidr,
            src_port_range: req.src_port_range,
            dst_port_range: req.dst_port_range,
            protocol: req.protocol,
            action: req.action,
            offload_eligible: req.offload_eligible,
        };
        self.rules
            .write()
            .await
            .insert(rule.id.clone(), rule.clone());
        Ok(rule)
    }

    pub async fn list_rules(&self, policy_id: &str) -> Result<Vec<Rule>, StoreError> {
        if !self.policies.read().await.contains_key(policy_id) {
            return Err(StoreError::NotFound(format!(
                "policy {policy_id} not found"
            )));
        }
        let rules: Vec<Rule> = self
            .rules
            .read()
            .await
            .values()
            .filter(|r| r.policy_id == policy_id)
            .cloned()
            .collect();
        Ok(rules)
    }

    pub async fn delete_rule(&self, id: &str) -> Result<Rule, StoreError> {
        self.rules
            .write()
            .await
            .remove(id)
            .ok_or_else(|| StoreError::NotFound(format!("rule {id} not found")))
    }

    // --- Desired State Bulk Push ---

    pub async fn apply_desired_state(
        &self,
        firewall_id: &str,
        req: DesiredStateRequest,
    ) -> Result<u64, StoreError> {
        // Verify firewall exists and check generation
        {
            let fws = self.firewalls.read().await;
            let fw = fws
                .get(firewall_id)
                .ok_or_else(|| StoreError::NotFound(format!("firewall {firewall_id} not found")))?;
            if fw.generation != req.expected_generation {
                return Err(StoreError::Conflict {
                    expected: req.expected_generation,
                    actual: fw.generation,
                });
            }
        }

        // Remove existing policies and rules for this firewall
        {
            let policies = self.policies.read().await;
            let policy_ids: Vec<String> = policies
                .values()
                .filter(|p| p.firewall_id == firewall_id)
                .map(|p| p.id.clone())
                .collect();
            drop(policies);

            let mut rules_map = self.rules.write().await;
            rules_map.retain(|_, r| !policy_ids.contains(&r.policy_id));
            drop(rules_map);

            let mut policies_map = self.policies.write().await;
            policies_map.retain(|_, p| p.firewall_id != firewall_id);
            drop(policies_map);
        }

        // Create new policies and rules
        for desired_policy in &req.policies {
            let gen = self.next_generation();
            let policy = Policy {
                id: uuid::Uuid::new_v4().to_string(),
                firewall_id: firewall_id.to_string(),
                name: desired_policy.name.clone(),
                priority: desired_policy.priority,
                default_action: desired_policy.default_action.clone(),
                generation: gen,
            };
            let policy_id = policy.id.clone();
            self.policies
                .write()
                .await
                .insert(policy.id.clone(), policy);

            for rule_req in &desired_policy.rules {
                let rule = Rule {
                    id: uuid::Uuid::new_v4().to_string(),
                    policy_id: policy_id.clone(),
                    priority: rule_req.priority,
                    src_cidr: rule_req.src_cidr.clone(),
                    dst_cidr: rule_req.dst_cidr.clone(),
                    src_port_range: rule_req.src_port_range,
                    dst_port_range: rule_req.dst_port_range,
                    protocol: rule_req.protocol.clone(),
                    action: rule_req.action.clone(),
                    offload_eligible: rule_req.offload_eligible,
                };
                self.rules.write().await.insert(rule.id.clone(), rule);
            }
        }

        // Update firewall generation
        let new_gen = self.next_generation();
        {
            let mut fws = self.firewalls.write().await;
            if let Some(fw) = fws.get_mut(firewall_id) {
                fw.generation = new_gen;
                fw.updated_at = chrono_now();
            }
        }

        Ok(new_gen)
    }
}

fn chrono_now() -> String {
    // Simple ISO-8601 timestamp using std
    use std::time::{SystemTime, UNIX_EPOCH};
    let duration = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = duration.as_secs();
    // Format as a basic timestamp — good enough without pulling in chrono
    format!("{secs}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_create_and_get_firewall() {
        let store = AppStore::new();
        let fw = store
            .create_firewall(CreateFirewallRequest {
                name: "test-fw".into(),
                tenant_id: "tenant-1".into(),
                vpc_id: "vpc-1".into(),
                vni: 1000,
                mode: FirewallMode::Inline,
                admin_state: AdminState::Enabled,
            })
            .await;

        let got = store.get_firewall(&fw.id).await.unwrap();
        assert_eq!(got.name, "test-fw");
        assert!(got.generation > 0);
    }

    #[tokio::test]
    async fn test_generation_conflict() {
        let store = AppStore::new();
        let fw = store
            .create_firewall(CreateFirewallRequest {
                name: "test-fw".into(),
                tenant_id: "tenant-1".into(),
                vpc_id: "vpc-1".into(),
                vni: 1000,
                mode: FirewallMode::Inline,
                admin_state: AdminState::Enabled,
            })
            .await;

        // Update with wrong generation
        let result = store
            .update_firewall(
                &fw.id,
                UpdateFirewallRequest {
                    name: Some("new-name".into()),
                    mode: None,
                    admin_state: None,
                    generation: 9999, // wrong
                },
            )
            .await;

        assert!(matches!(result, Err(StoreError::Conflict { .. })));
    }
}
