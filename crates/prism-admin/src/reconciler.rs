use std::sync::Arc;
use tokio::time::{self, Duration};

use crate::store::AppStore;

/// Background reconciler that compares desired vs actual state
/// and logs what would be pushed to the datapath.
pub struct Reconciler {
    store: Arc<AppStore>,
}

impl Reconciler {
    pub fn new(store: Arc<AppStore>) -> Self {
        Self { store }
    }

    /// Run the reconciliation loop as a background task.
    /// Checks every 5 seconds for pending changes.
    pub fn spawn(self) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let mut interval = time::interval(Duration::from_secs(5));
            loop {
                interval.tick().await;
                self.reconcile().await;
            }
        })
    }

    /// Perform one reconciliation pass.
    /// In production this would diff desired state against the datapath
    /// and push changes. For now we just log.
    async fn reconcile(&self) {
        let firewalls = self.store.firewalls.read().await;
        let policies = self.store.policies.read().await;
        let rules = self.store.rules.read().await;

        let fw_count = firewalls.len();
        let policy_count = policies.len();
        let rule_count = rules.len();

        if fw_count == 0 {
            return; // Nothing to reconcile
        }

        tracing::debug!(
            firewalls = fw_count,
            policies = policy_count,
            rules = rule_count,
            generation = self.store.current_generation(),
            "reconciler pass: would push {} rule changes to datapath",
            rule_count
        );
    }
}
