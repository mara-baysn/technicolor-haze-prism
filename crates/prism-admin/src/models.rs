use serde::{Deserialize, Serialize};

// --- Enums ---

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum RuleAction {
    Allow,
    Deny,
    Reject,
    RateLimit,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum FirewallMode {
    Inline,
    Tap,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum AdminState {
    Enabled,
    Disabled,
}

// --- Core Resources ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Firewall {
    pub id: String,
    pub name: String,
    pub tenant_id: String,
    pub vpc_id: String,
    pub vni: u32,
    pub mode: FirewallMode,
    pub admin_state: AdminState,
    pub generation: u64,
    pub created_at: String,
    pub updated_at: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Policy {
    pub id: String,
    pub firewall_id: String,
    pub name: String,
    pub priority: u32,
    pub default_action: RuleAction,
    pub generation: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Rule {
    pub id: String,
    pub policy_id: String,
    pub priority: u32,
    pub src_cidr: Option<String>,
    pub dst_cidr: Option<String>,
    pub src_port_range: Option<(u16, u16)>,
    pub dst_port_range: Option<(u16, u16)>,
    pub protocol: Option<String>,
    pub action: RuleAction,
    pub offload_eligible: bool,
}

// --- Observability Models ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Session {
    pub key: SessionKeyInfo,
    pub state: String,
    pub offloaded: bool,
    pub packets: u64,
    pub bytes: u64,
    pub age_seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionKeyInfo {
    pub vni: u32,
    pub src_ip: String,
    pub dst_ip: String,
    pub src_port: u16,
    pub dst_port: u16,
    pub protocol: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MetricsResponse {
    pub throughput_bps: u64,
    pub packets_per_sec: u64,
    pub offload_ratio_percent: f64,
    pub active_sessions: u64,
    pub vm_cpu_percent: f64,
}

// --- Request Models ---

#[derive(Debug, Clone, Deserialize)]
pub struct CreateFirewallRequest {
    pub name: String,
    pub tenant_id: String,
    pub vpc_id: String,
    pub vni: u32,
    pub mode: FirewallMode,
    #[serde(default = "default_admin_state")]
    pub admin_state: AdminState,
}

fn default_admin_state() -> AdminState {
    AdminState::Enabled
}

#[derive(Debug, Clone, Deserialize)]
pub struct UpdateFirewallRequest {
    pub name: Option<String>,
    pub mode: Option<FirewallMode>,
    pub admin_state: Option<AdminState>,
    /// Required for optimistic concurrency control
    pub generation: u64,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CreatePolicyRequest {
    pub name: String,
    pub priority: u32,
    pub default_action: RuleAction,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CreateRuleRequest {
    pub priority: u32,
    pub src_cidr: Option<String>,
    pub dst_cidr: Option<String>,
    pub src_port_range: Option<(u16, u16)>,
    pub dst_port_range: Option<(u16, u16)>,
    pub protocol: Option<String>,
    pub action: RuleAction,
    #[serde(default)]
    pub offload_eligible: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DesiredStateRequest {
    /// The generation the client last saw. Used for optimistic concurrency.
    pub expected_generation: u64,
    pub policies: Vec<DesiredPolicy>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DesiredPolicy {
    pub name: String,
    pub priority: u32,
    pub default_action: RuleAction,
    pub rules: Vec<CreateRuleRequest>,
}

// --- Error Model ---

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiErrorBody {
    pub error: String,
    pub message: String,
}
