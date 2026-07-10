use std::collections::HashMap;
use std::time::Instant;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ConnState {
    New,
    Established,
    FinWait,
    Closed,
}

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub struct FiveTuple {
    pub src_ip: u32,
    pub dst_ip: u32,
    pub src_port: u16,
    pub dst_port: u16,
    pub protocol: u8,
}

#[derive(Debug)]
pub struct ConnEntry {
    pub state: ConnState,
    pub packets: u64,
    pub bytes: u64,
    pub last_seen: Instant,
    pub vni: u32,
}

pub struct ConnTracker {
    entries: HashMap<(u32, FiveTuple), ConnEntry>,
    idle_timeout_secs: u64,
}

impl ConnTracker {
    pub fn new(idle_timeout_secs: u64) -> Self {
        Self {
            entries: HashMap::new(),
            idle_timeout_secs,
        }
    }

    pub fn lookup(&mut self, vni: u32, tuple: &FiveTuple, pkt_len: u64) -> ConnState {
        let key = (vni, tuple.clone());
        let now = Instant::now();

        let entry = self.entries.entry(key).or_insert_with(|| ConnEntry {
            state: ConnState::New,
            packets: 0,
            bytes: 0,
            last_seen: now,
            vni,
        });

        entry.packets += 1;
        entry.bytes += pkt_len;
        entry.last_seen = now;

        if entry.state == ConnState::New && entry.packets > 1 {
            entry.state = ConnState::Established;
        }

        entry.state
    }

    pub fn get_packet_count(&self, vni: u32, tuple: &FiveTuple) -> u64 {
        self.entries
            .get(&(vni, tuple.clone()))
            .map(|e| e.packets)
            .unwrap_or(0)
    }

    pub fn evict_idle(&mut self) -> usize {
        let timeout = std::time::Duration::from_secs(self.idle_timeout_secs);
        let now = Instant::now();
        let before = self.entries.len();
        self.entries.retain(|_, e| now.duration_since(e.last_seen) < timeout);
        before - self.entries.len()
    }

    pub fn active_count(&self) -> usize {
        self.entries.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_tuple() -> FiveTuple {
        FiveTuple {
            src_ip: 0x0A000001,
            dst_ip: 0x0A000002,
            src_port: 12345,
            dst_port: 443,
            protocol: 6, // TCP
        }
    }

    #[test]
    fn new_flow_starts_as_new() {
        let mut ct = ConnTracker::new(300);
        let state = ct.lookup(100, &test_tuple(), 64);
        assert_eq!(state, ConnState::New);
    }

    #[test]
    fn second_packet_transitions_to_established() {
        let mut ct = ConnTracker::new(300);
        let tuple = test_tuple();
        ct.lookup(100, &tuple, 64);
        let state = ct.lookup(100, &tuple, 64);
        assert_eq!(state, ConnState::Established);
    }

    #[test]
    fn different_vni_separate_flows() {
        let mut ct = ConnTracker::new(300);
        let tuple = test_tuple();
        ct.lookup(100, &tuple, 64);
        ct.lookup(100, &tuple, 64);
        let state = ct.lookup(200, &tuple, 64);
        assert_eq!(state, ConnState::New); // different VNI = new flow
    }

    #[test]
    fn packet_count_increments() {
        let mut ct = ConnTracker::new(300);
        let tuple = test_tuple();
        ct.lookup(100, &tuple, 64);
        ct.lookup(100, &tuple, 128);
        ct.lookup(100, &tuple, 256);
        assert_eq!(ct.get_packet_count(100, &tuple), 3);
    }
}
