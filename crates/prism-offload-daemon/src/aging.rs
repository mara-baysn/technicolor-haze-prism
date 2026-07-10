use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

use tokio::time;

use crate::doca_flow::DocaFlowManager;
use crate::session_table::SessionTable;

/// Background session aging loop that evicts idle sessions.
///
/// A session is considered idle if its packet counter has not changed
/// for 2 consecutive poll intervals.
pub struct SessionAger {
    idle_timeout: Duration,
    poll_interval: Duration,
}

impl SessionAger {
    /// Create a new SessionAger with configurable timeouts.
    ///
    /// - `idle_timeout`: how long a session can be idle before eviction (default 60s)
    /// - `poll_interval`: how often to check for idle sessions (default 30s)
    pub fn new(idle_timeout: Duration, poll_interval: Duration) -> Self {
        Self {
            idle_timeout,
            poll_interval,
        }
    }

    /// Run the aging loop. This should be spawned as a background task.
    ///
    /// The loop tracks the last-known packet count for each session.
    /// If a session's packet count hasn't changed for longer than idle_timeout,
    /// the session is evicted from both the hardware and the session table.
    pub async fn run(
        &self,
        session_table: Arc<SessionTable>,
        doca_manager: Arc<DocaFlowManager>,
        mut shutdown: tokio::sync::watch::Receiver<bool>,
    ) {
        // Track (last_known_packets, first_seen_idle_at)
        let mut idle_tracker: HashMap<String, (u64, tokio::time::Instant)> = HashMap::new();
        let mut interval = time::interval(self.poll_interval);

        tracing::info!(
            poll_interval_ms = self.poll_interval.as_millis() as u64,
            idle_timeout_ms = self.idle_timeout.as_millis() as u64,
            "session aging loop started"
        );

        loop {
            tokio::select! {
                _ = interval.tick() => {}
                Ok(()) = shutdown.changed() => {
                    if *shutdown.borrow() {
                        tracing::info!("session aging loop shutting down");
                        return;
                    }
                }
            }

            let mut to_evict: Vec<String> = Vec::new();
            let now = tokio::time::Instant::now();

            // Collect current state of all sessions
            let session_keys: Vec<(String, u64)> = session_table
                .iter()
                .map(|entry| {
                    let key = entry.key().clone();
                    let handle = entry.value().entry_handle;
                    (key, handle)
                })
                .collect();

            for (key, handle) in &session_keys {
                // Query hardware counters (non-incrementing)
                let current_packets = match doca_manager.query_session_counters(*handle) {
                    Ok((pkts, _)) => pkts,
                    Err(_) => {
                        // Entry no longer in hardware, mark for eviction
                        to_evict.push(key.clone());
                        continue;
                    }
                };

                match idle_tracker.get(key) {
                    Some((last_packets, first_idle_at)) => {
                        if current_packets == *last_packets {
                            // Still idle — check if timeout exceeded
                            if now.duration_since(*first_idle_at) >= self.idle_timeout {
                                to_evict.push(key.clone());
                            }
                        } else {
                            // Activity detected — reset tracker
                            idle_tracker.insert(key.clone(), (current_packets, now));
                        }
                    }
                    None => {
                        // First time seeing this session — record baseline
                        idle_tracker.insert(key.clone(), (current_packets, now));
                    }
                }
            }

            // Evict idle sessions
            for key in &to_evict {
                if let Some(entry) = session_table.remove_by_key_string(key) {
                    // Remove from hardware
                    if let Err(e) = doca_manager.remove_session(entry.entry_handle) {
                        tracing::warn!(handle = entry.entry_handle, error = %e, "failed to remove aged session from hardware");
                    }
                    // Remove reverse entry if bidirectional
                    if let Some(rev_handle) = entry.reverse_handle {
                        if let Err(e) = doca_manager.remove_session(rev_handle) {
                            tracing::warn!(handle = rev_handle, error = %e, "failed to remove reverse aged session from hardware");
                        }
                    }
                    tracing::debug!(key = %key, handle = entry.entry_handle, "evicted idle session");
                }
                idle_tracker.remove(key);
            }

            // Clean up tracker entries for sessions that no longer exist
            let active_keys: std::collections::HashSet<String> = session_table
                .iter()
                .map(|entry| entry.key().clone())
                .collect();
            idle_tracker.retain(|k, _| active_keys.contains(k));

            if !to_evict.is_empty() {
                tracing::info!(
                    evicted = to_evict.len(),
                    remaining = session_table.active_count(),
                    "aging sweep complete"
                );
            }
        }
    }
}

impl Default for SessionAger {
    fn default() -> Self {
        Self::new(Duration::from_secs(60), Duration::from_secs(30))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::doca_flow::DocaFlowManager;
    use crate::session_table::{SessionEntry, SessionTable};
    use prism_proto::SessionKey;

    fn make_key(vni: u32, src_port: u32) -> SessionKey {
        SessionKey {
            vni,
            src_ip: "10.0.0.1".to_string(),
            dst_ip: "10.0.0.2".to_string(),
            src_port,
            dst_port: 80,
            protocol: 1,
        }
    }

    #[tokio::test]
    async fn test_aging_evicts_idle_sessions() {
        let session_table = Arc::new(SessionTable::new());
        let doca_manager = Arc::new(DocaFlowManager::init().unwrap());

        // Program a session in hardware but do NOT generate traffic
        // We'll use query_session_counters which doesn't increment
        let handle = doca_manager
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();

        let key = make_key(100, 5000);
        let entry = SessionEntry::new(handle, 100, key.clone(), 1, false, None, "rule-1".to_string());
        session_table.insert(&key, entry);

        assert_eq!(session_table.active_count(), 1);

        // Create ager with very short timeouts for testing
        let ager = SessionAger::new(Duration::from_millis(50), Duration::from_millis(20));

        let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

        let table_clone = session_table.clone();
        let doca_clone = doca_manager.clone();

        let handle_task = tokio::spawn(async move {
            ager.run(table_clone, doca_clone, shutdown_rx).await;
        });

        // Wait long enough for the idle timeout to trigger
        tokio::time::sleep(Duration::from_millis(200)).await;

        // Session should have been evicted
        assert_eq!(session_table.active_count(), 0);

        // Shutdown the ager
        shutdown_tx.send(true).unwrap();
        let _ = handle_task.await;
    }

    #[tokio::test]
    async fn test_aging_keeps_active_sessions() {
        let session_table = Arc::new(SessionTable::new());
        let doca_manager = Arc::new(DocaFlowManager::init().unwrap());

        let handle = doca_manager
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();

        let key = make_key(100, 5000);
        let entry = SessionEntry::new(handle, 100, key.clone(), 1, false, None, "rule-1".to_string());
        session_table.insert(&key, entry);

        // Use regular query_session to simulate traffic (increments counters)
        let ager = SessionAger::new(Duration::from_millis(100), Duration::from_millis(30));

        let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

        let table_clone = session_table.clone();
        let doca_clone = doca_manager.clone();

        let ager_handle = tokio::spawn(async move {
            ager.run(table_clone, doca_clone, shutdown_rx).await;
        });

        // Simulate traffic by querying (which increments mock counters)
        for _ in 0..5 {
            tokio::time::sleep(Duration::from_millis(25)).await;
            let _ = doca_manager.query_session(handle);
        }

        // Session should still be active since counters are changing
        assert_eq!(session_table.active_count(), 1);

        shutdown_tx.send(true).unwrap();
        let _ = ager_handle.await;
    }
}
