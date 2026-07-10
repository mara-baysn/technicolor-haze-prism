use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use dashmap::DashMap;

use prism_proto::SessionKey;

/// Represents an offloaded session entry in the hardware table.
pub struct SessionEntry {
    pub entry_handle: u64,
    pub vni: u32,
    pub key: SessionKey,
    pub action: i32,
    pub packets_matched: AtomicU64,
    pub bytes_matched: AtomicU64,
    pub created_at: Instant,
    pub last_queried: Instant,
    pub bidirectional: bool,
    pub reverse_handle: Option<u64>,
    pub rule_id: String,
}

impl SessionEntry {
    pub fn new(
        entry_handle: u64,
        vni: u32,
        key: SessionKey,
        action: i32,
        bidirectional: bool,
        reverse_handle: Option<u64>,
        rule_id: String,
    ) -> Self {
        let now = Instant::now();
        Self {
            entry_handle,
            vni,
            key,
            action,
            packets_matched: AtomicU64::new(0),
            bytes_matched: AtomicU64::new(0),
            created_at: now,
            last_queried: now,
            bidirectional,
            reverse_handle,
            rule_id,
        }
    }

    pub fn packets(&self) -> u64 {
        self.packets_matched.load(Ordering::Relaxed)
    }

    pub fn bytes(&self) -> u64 {
        self.bytes_matched.load(Ordering::Relaxed)
    }

    pub fn update_counters(&self, packets: u64, bytes: u64) {
        self.packets_matched.store(packets, Ordering::Relaxed);
        self.bytes_matched.store(bytes, Ordering::Relaxed);
    }
}

/// Derives a canonical key string from a SessionKey for map lookups.
fn session_key_string(key: &SessionKey) -> String {
    format!(
        "{}:{}:{}:{}:{}:{}",
        key.vni, key.src_ip, key.dst_ip, key.src_port, key.dst_port, key.protocol
    )
}

/// Thread-safe in-memory index of all programmed sessions.
pub struct SessionTable {
    map: DashMap<String, SessionEntry>,
}

impl SessionTable {
    pub fn new() -> Self {
        Self {
            map: DashMap::new(),
        }
    }

    /// Insert a session entry keyed by its SessionKey.
    pub fn insert(&self, key: &SessionKey, entry: SessionEntry) {
        let k = session_key_string(key);
        self.map.insert(k, entry);
    }

    /// Remove a session entry by its SessionKey. Returns the entry if found.
    pub fn remove(&self, key: &SessionKey) -> Option<SessionEntry> {
        let k = session_key_string(key);
        self.map.remove(&k).map(|(_, v)| v)
    }

    /// Get a reference to a session entry by its SessionKey.
    pub fn get(&self, key: &SessionKey) -> Option<dashmap::mapref::one::Ref<'_, String, SessionEntry>> {
        let k = session_key_string(key);
        self.map.get(&k)
    }

    /// Flush all sessions matching a rule_id. Returns the count of removed entries
    /// and a vec of (entry_handle, Option<reverse_handle>) for hardware removal.
    pub fn flush_by_rule_id(&self, rule_id: &str) -> (u32, Vec<(u64, Option<u64>)>) {
        let keys_to_remove: Vec<String> = self
            .map
            .iter()
            .filter(|entry| entry.value().rule_id == rule_id)
            .map(|entry| entry.key().clone())
            .collect();

        let mut handles = Vec::new();
        for k in &keys_to_remove {
            if let Some((_, entry)) = self.map.remove(k) {
                handles.push((entry.entry_handle, entry.reverse_handle));
            }
        }
        (handles.len() as u32, handles)
    }

    /// Flush all sessions matching a VNI. Returns the count of removed entries
    /// and a vec of (entry_handle, Option<reverse_handle>) for hardware removal.
    pub fn flush_by_vni(&self, vni: u32) -> (u32, Vec<(u64, Option<u64>)>) {
        let keys_to_remove: Vec<String> = self
            .map
            .iter()
            .filter(|entry| entry.value().vni == vni)
            .map(|entry| entry.key().clone())
            .collect();

        let mut handles = Vec::new();
        for k in &keys_to_remove {
            if let Some((_, entry)) = self.map.remove(k) {
                handles.push((entry.entry_handle, entry.reverse_handle));
            }
        }
        (handles.len() as u32, handles)
    }

    /// Flush all sessions. Returns the count of removed entries
    /// and a vec of (entry_handle, Option<reverse_handle>) for hardware removal.
    pub fn flush_all(&self) -> (u32, Vec<(u64, Option<u64>)>) {
        let keys: Vec<String> = self.map.iter().map(|e| e.key().clone()).collect();
        let mut handles = Vec::new();
        for k in &keys {
            if let Some((_, entry)) = self.map.remove(k) {
                handles.push((entry.entry_handle, entry.reverse_handle));
            }
        }
        (handles.len() as u32, handles)
    }

    /// Returns the number of active sessions.
    pub fn active_count(&self) -> u64 {
        self.map.len() as u64
    }

    /// Remove a session by its map key string. Used by the aging loop.
    pub fn remove_by_key_string(&self, key: &str) -> Option<SessionEntry> {
        self.map.remove(key).map(|(_, v)| v)
    }

    /// Iterate over all sessions. Used by the aging loop.
    pub fn iter(&self) -> impl Iterator<Item = dashmap::mapref::multiple::RefMulti<'_, String, SessionEntry>> {
        self.map.iter()
    }
}

impl Default for SessionTable {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use prism_proto::SessionKey;

    fn make_key(vni: u32, src_port: u32) -> SessionKey {
        SessionKey {
            vni,
            src_ip: "10.0.0.1".to_string(),
            dst_ip: "10.0.0.2".to_string(),
            src_port,
            dst_port: 80,
            protocol: 1, // TCP
        }
    }

    #[test]
    fn test_insert_and_get() {
        let table = SessionTable::new();
        let key = make_key(100, 5000);
        let entry = SessionEntry::new(1, 100, key.clone(), 1, false, None, "rule-1".to_string());
        table.insert(&key, entry);

        let got = table.get(&key);
        assert!(got.is_some());
        let got = got.unwrap();
        assert_eq!(got.entry_handle, 1);
        assert_eq!(got.vni, 100);
        assert_eq!(got.rule_id, "rule-1");
    }

    #[test]
    fn test_remove() {
        let table = SessionTable::new();
        let key = make_key(100, 5000);
        let entry = SessionEntry::new(1, 100, key.clone(), 1, false, None, "rule-1".to_string());
        table.insert(&key, entry);

        let removed = table.remove(&key);
        assert!(removed.is_some());
        assert_eq!(removed.unwrap().entry_handle, 1);
        assert!(table.get(&key).is_none());
    }

    #[test]
    fn test_flush_by_rule_id() {
        let table = SessionTable::new();

        for i in 0..5 {
            let key = make_key(100, 5000 + i);
            let entry = SessionEntry::new(i as u64, 100, key.clone(), 1, false, None, "rule-A".to_string());
            table.insert(&key, entry);
        }
        for i in 5..8 {
            let key = make_key(200, 5000 + i);
            let entry = SessionEntry::new(i as u64, 200, key.clone(), 1, false, None, "rule-B".to_string());
            table.insert(&key, entry);
        }

        let (count, handles) = table.flush_by_rule_id("rule-A");
        assert_eq!(count, 5);
        assert_eq!(handles.len(), 5);
        assert_eq!(table.active_count(), 3);
    }

    #[test]
    fn test_flush_by_vni() {
        let table = SessionTable::new();

        for i in 0..3 {
            let key = make_key(100, 5000 + i);
            let entry = SessionEntry::new(i as u64, 100, key.clone(), 1, false, None, "rule-A".to_string());
            table.insert(&key, entry);
        }
        for i in 3..7 {
            let key = make_key(200, 5000 + i);
            let entry = SessionEntry::new(i as u64, 200, key.clone(), 1, false, None, "rule-B".to_string());
            table.insert(&key, entry);
        }

        let (count, _handles) = table.flush_by_vni(200);
        assert_eq!(count, 4);
        assert_eq!(table.active_count(), 3);
    }

    #[test]
    fn test_flush_all() {
        let table = SessionTable::new();

        for i in 0..10 {
            let key = make_key(100, 5000 + i);
            let entry = SessionEntry::new(i as u64, 100, key.clone(), 1, false, None, "rule-A".to_string());
            table.insert(&key, entry);
        }

        let (count, _) = table.flush_all();
        assert_eq!(count, 10);
        assert_eq!(table.active_count(), 0);
    }

    #[test]
    fn test_active_count() {
        let table = SessionTable::new();
        assert_eq!(table.active_count(), 0);

        let key = make_key(100, 5000);
        let entry = SessionEntry::new(1, 100, key.clone(), 1, false, None, "rule-1".to_string());
        table.insert(&key, entry);
        assert_eq!(table.active_count(), 1);
    }

    #[test]
    fn test_bidirectional_entry() {
        let table = SessionTable::new();
        let key = make_key(100, 5000);
        let entry = SessionEntry::new(1, 100, key.clone(), 1, true, Some(2), "rule-1".to_string());
        table.insert(&key, entry);

        let got = table.get(&key).unwrap();
        assert!(got.bidirectional);
        assert_eq!(got.reverse_handle, Some(2));
    }
}
