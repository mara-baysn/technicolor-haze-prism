use crate::conntrack::ConnTracker;
use crate::acl::AclEngine;

pub struct Pipeline {
    pub conntrack: ConnTracker,
    pub acl: AclEngine,
}

impl Pipeline {
    pub fn new(acl: AclEngine) -> Self {
        Self {
            conntrack: ConnTracker::new(300), // 300s idle timeout
            acl,
        }
    }
}
