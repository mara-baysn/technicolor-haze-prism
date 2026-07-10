use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatapathConfig {
    pub acl_rules_path: String,
    pub offload_daemon_addr: String,
    pub offload_min_packets: u64,
    pub idle_timeout_secs: u64,
    pub mock_mode: bool,
}

impl Default for DatapathConfig {
    fn default() -> Self {
        Self {
            acl_rules_path: String::new(),
            offload_daemon_addr: "http://[::1]:50051".to_string(),
            offload_min_packets: 10,
            idle_timeout_secs: 300,
            mock_mode: false,
        }
    }
}

impl DatapathConfig {
    pub fn from_env() -> Self {
        let mut config = Self::default();

        if let Ok(val) = std::env::var("PRISM_ACL_RULES_PATH") {
            config.acl_rules_path = val;
        }
        if let Ok(val) = std::env::var("PRISM_OFFLOAD_DAEMON_ADDR") {
            config.offload_daemon_addr = val;
        }
        if let Ok(val) = std::env::var("PRISM_OFFLOAD_MIN_PACKETS") {
            if let Ok(n) = val.parse::<u64>() {
                config.offload_min_packets = n;
            }
        }
        if let Ok(val) = std::env::var("PRISM_IDLE_TIMEOUT_SECS") {
            if let Ok(n) = val.parse::<u64>() {
                config.idle_timeout_secs = n;
            }
        }
        if let Ok(val) = std::env::var("PRISM_MOCK_MODE") {
            config.mock_mode = val == "1" || val.eq_ignore_ascii_case("true");
        }

        config
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_config_values() {
        let config = DatapathConfig::default();
        assert_eq!(config.offload_daemon_addr, "http://[::1]:50051");
        assert_eq!(config.offload_min_packets, 10);
        assert_eq!(config.idle_timeout_secs, 300);
        assert!(!config.mock_mode);
        assert!(config.acl_rules_path.is_empty());
    }

    #[test]
    fn config_from_env() {
        // Set env vars for this test
        std::env::set_var("PRISM_ACL_RULES_PATH", "/tmp/rules.json");
        std::env::set_var("PRISM_OFFLOAD_DAEMON_ADDR", "http://localhost:9999");
        std::env::set_var("PRISM_OFFLOAD_MIN_PACKETS", "20");
        std::env::set_var("PRISM_IDLE_TIMEOUT_SECS", "600");
        std::env::set_var("PRISM_MOCK_MODE", "true");

        let config = DatapathConfig::from_env();
        assert_eq!(config.acl_rules_path, "/tmp/rules.json");
        assert_eq!(config.offload_daemon_addr, "http://localhost:9999");
        assert_eq!(config.offload_min_packets, 20);
        assert_eq!(config.idle_timeout_secs, 600);
        assert!(config.mock_mode);

        // Clean up
        std::env::remove_var("PRISM_ACL_RULES_PATH");
        std::env::remove_var("PRISM_OFFLOAD_DAEMON_ADDR");
        std::env::remove_var("PRISM_OFFLOAD_MIN_PACKETS");
        std::env::remove_var("PRISM_IDLE_TIMEOUT_SECS");
        std::env::remove_var("PRISM_MOCK_MODE");
    }
}
