use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use thiserror::Error;

#[derive(Error, Debug)]
pub enum DocaFlowError {
    #[error("DOCA Flow initialization failed")]
    InitFailed,
    #[error("Failed to program session: {0}")]
    ProgramFailed(String),
    #[error("Failed to remove session: handle {0} not found")]
    RemoveFailed(u64),
    #[error("Failed to query session: handle {0} not found")]
    QueryFailed(u64),
}

/// Simulated hardware entry for the mock DOCA Flow backend.
struct MockHwEntry {
    _vni: u32,
    _src_ip: String,
    _dst_ip: String,
    _src_port: u32,
    _dst_port: u32,
    _protocol: i32,
    _action: i32,
    _forward_port: u32,
    packets: AtomicU64,
    bytes: AtomicU64,
}

/// Safe wrapper over the DOCA Flow API (mock implementation).
///
/// In production, this would call into prism-doca-sys FFI functions.
/// For development, it uses in-memory HashMaps to simulate hardware behavior.
pub struct DocaFlowManager {
    next_handle: AtomicU64,
    entries: Mutex<HashMap<u64, MockHwEntry>>,
}

impl DocaFlowManager {
    /// Initialize the DOCA Flow manager.
    pub fn init() -> Result<Self, DocaFlowError> {
        // In production, this would call doca_flow_init and doca_flow_port_start.
        // For mock, we just create the in-memory state.
        tracing::info!("DocaFlowManager initialized (mock mode)");
        Ok(Self {
            next_handle: AtomicU64::new(1),
            entries: Mutex::new(HashMap::new()),
        })
    }

    /// Program a session into the eSwitch hardware table.
    ///
    /// Returns a handle that can be used to remove or query the session.
    #[allow(clippy::too_many_arguments)]
    pub fn program_session(
        &self,
        vni: u32,
        src_ip: &str,
        dst_ip: &str,
        src_port: u32,
        dst_port: u32,
        protocol: i32,
        action: i32,
        forward_port: u32,
    ) -> Result<u64, DocaFlowError> {
        let handle = self.next_handle.fetch_add(1, Ordering::Relaxed);

        let entry = MockHwEntry {
            _vni: vni,
            _src_ip: src_ip.to_string(),
            _dst_ip: dst_ip.to_string(),
            _src_port: src_port,
            _dst_port: dst_port,
            _protocol: protocol,
            _action: action,
            _forward_port: forward_port,
            packets: AtomicU64::new(0),
            bytes: AtomicU64::new(0),
        };

        let mut entries = self.entries.lock().map_err(|e| {
            DocaFlowError::ProgramFailed(format!("lock poisoned: {}", e))
        })?;
        entries.insert(handle, entry);

        tracing::debug!(handle, vni, src_ip, dst_ip, "programmed session in eSwitch");
        Ok(handle)
    }

    /// Remove a session from the eSwitch hardware table.
    pub fn remove_session(&self, handle: u64) -> Result<(), DocaFlowError> {
        let mut entries = self.entries.lock().map_err(|_| {
            DocaFlowError::RemoveFailed(handle)
        })?;

        if entries.remove(&handle).is_none() {
            return Err(DocaFlowError::RemoveFailed(handle));
        }

        tracing::debug!(handle, "removed session from eSwitch");
        Ok(())
    }

    /// Query a session's packet/byte counters from the hardware.
    ///
    /// In mock mode, counters increment by a small amount on each query
    /// to simulate traffic flowing through the hardware.
    pub fn query_session(&self, handle: u64) -> Result<(u64, u64), DocaFlowError> {
        let entries = self.entries.lock().map_err(|_| {
            DocaFlowError::QueryFailed(handle)
        })?;

        let entry = entries.get(&handle).ok_or(DocaFlowError::QueryFailed(handle))?;

        // In mock mode, simulate some traffic by incrementing counters
        let prev_packets = entry.packets.fetch_add(10, Ordering::Relaxed);
        let prev_bytes = entry.bytes.fetch_add(15000, Ordering::Relaxed);

        Ok((prev_packets + 10, prev_bytes + 15000))
    }

    /// Query session counters without incrementing (for aging checks).
    /// Returns current counter values without simulating new traffic.
    pub fn query_session_counters(&self, handle: u64) -> Result<(u64, u64), DocaFlowError> {
        let entries = self.entries.lock().map_err(|_| {
            DocaFlowError::QueryFailed(handle)
        })?;

        let entry = entries.get(&handle).ok_or(DocaFlowError::QueryFailed(handle))?;
        let packets = entry.packets.load(Ordering::Relaxed);
        let bytes = entry.bytes.load(Ordering::Relaxed);

        Ok((packets, bytes))
    }

    /// Get the number of entries programmed in hardware.
    pub fn entry_count(&self) -> usize {
        self.entries.lock().map(|e| e.len()).unwrap_or(0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_init() {
        let mgr = DocaFlowManager::init().unwrap();
        assert_eq!(mgr.entry_count(), 0);
    }

    #[test]
    fn test_program_and_query() {
        let mgr = DocaFlowManager::init().unwrap();
        let handle = mgr
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();
        assert!(handle > 0);
        assert_eq!(mgr.entry_count(), 1);

        let (packets, bytes) = mgr.query_session(handle).unwrap();
        assert_eq!(packets, 10);
        assert_eq!(bytes, 15000);

        // Second query should show accumulated counters
        let (packets, bytes) = mgr.query_session(handle).unwrap();
        assert_eq!(packets, 20);
        assert_eq!(bytes, 30000);
    }

    #[test]
    fn test_program_and_remove() {
        let mgr = DocaFlowManager::init().unwrap();
        let handle = mgr
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();
        assert_eq!(mgr.entry_count(), 1);

        mgr.remove_session(handle).unwrap();
        assert_eq!(mgr.entry_count(), 0);

        // Removing again should fail
        let result = mgr.remove_session(handle);
        assert!(result.is_err());
    }

    #[test]
    fn test_query_nonexistent() {
        let mgr = DocaFlowManager::init().unwrap();
        let result = mgr.query_session(999);
        assert!(result.is_err());
    }

    #[test]
    fn test_multiple_sessions() {
        let mgr = DocaFlowManager::init().unwrap();

        let h1 = mgr
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();
        let h2 = mgr
            .program_session(200, "10.0.1.1", "10.0.1.2", 6000, 443, 1, 1, 0)
            .unwrap();

        assert_ne!(h1, h2);
        assert_eq!(mgr.entry_count(), 2);

        mgr.remove_session(h1).unwrap();
        assert_eq!(mgr.entry_count(), 1);

        // h2 should still be queryable
        let (packets, _) = mgr.query_session(h2).unwrap();
        assert_eq!(packets, 10);
    }

    #[test]
    fn test_query_counters_no_increment() {
        let mgr = DocaFlowManager::init().unwrap();
        let handle = mgr
            .program_session(100, "10.0.0.1", "10.0.0.2", 5000, 80, 1, 1, 0)
            .unwrap();

        // Initially zero
        let (packets, bytes) = mgr.query_session_counters(handle).unwrap();
        assert_eq!(packets, 0);
        assert_eq!(bytes, 0);

        // After a regular query (which simulates traffic)
        mgr.query_session(handle).unwrap();
        let (packets, bytes) = mgr.query_session_counters(handle).unwrap();
        assert_eq!(packets, 10);
        assert_eq!(bytes, 15000);
    }
}
