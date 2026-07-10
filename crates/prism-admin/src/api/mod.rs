use std::sync::Arc;

use axum::{
    extract::{Path, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::{delete, get, post, put},
    Json, Router,
};

use crate::models::*;
use crate::store::{AppStore, StoreError};

// --- Error handling ---

impl IntoResponse for StoreError {
    fn into_response(self) -> Response {
        let (status, body) = match self {
            StoreError::NotFound(msg) => (
                StatusCode::NOT_FOUND,
                ApiErrorBody {
                    error: "not_found".into(),
                    message: msg,
                },
            ),
            StoreError::Conflict { expected, actual } => (
                StatusCode::CONFLICT,
                ApiErrorBody {
                    error: "conflict".into(),
                    message: format!(
                        "generation mismatch: expected {expected}, actual {actual}"
                    ),
                },
            ),
        };
        (status, Json(body)).into_response()
    }
}

/// Build the full API router with shared state.
pub fn router(store: Arc<AppStore>) -> Router {
    Router::new()
        // Firewall routes
        .route("/firewalls", post(create_firewall))
        .route("/firewalls", get(list_firewalls))
        .route("/firewalls/:id", get(get_firewall))
        .route("/firewalls/:id", put(update_firewall))
        .route("/firewalls/:id", delete(delete_firewall))
        // Policy routes
        .route("/firewalls/:id/policies", post(create_policy))
        .route("/firewalls/:id/policies", get(list_policies))
        // Rule routes
        .route("/policies/:id/rules", post(create_rule))
        .route("/policies/:id/rules", get(list_rules))
        .route("/rules/:id", delete(delete_rule))
        // Observability routes
        .route("/firewalls/:id/sessions", get(list_sessions))
        .route("/firewalls/:id/metrics", get(get_metrics))
        .route("/firewalls/:id/sessions/flush", post(flush_sessions))
        // Desired state
        .route("/firewalls/:id/desired-state", post(push_desired_state))
        .with_state(store)
}

// --- Firewall Handlers ---

async fn create_firewall(
    State(store): State<Arc<AppStore>>,
    Json(req): Json<CreateFirewallRequest>,
) -> impl IntoResponse {
    let fw = store.create_firewall(req).await;
    (StatusCode::CREATED, Json(fw))
}

async fn list_firewalls(State(store): State<Arc<AppStore>>) -> impl IntoResponse {
    let firewalls = store.list_firewalls().await;
    Json(firewalls)
}

async fn get_firewall(
    State(store): State<Arc<AppStore>>,
    Path(id): Path<String>,
) -> Result<Json<Firewall>, StoreError> {
    let fw = store.get_firewall(&id).await?;
    Ok(Json(fw))
}

async fn update_firewall(
    State(store): State<Arc<AppStore>>,
    Path(id): Path<String>,
    Json(req): Json<UpdateFirewallRequest>,
) -> Result<Json<Firewall>, StoreError> {
    let fw = store.update_firewall(&id, req).await?;
    Ok(Json(fw))
}

async fn delete_firewall(
    State(store): State<Arc<AppStore>>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, StoreError> {
    store.delete_firewall(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}

// --- Policy Handlers ---

async fn create_policy(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
    Json(req): Json<CreatePolicyRequest>,
) -> Result<impl IntoResponse, StoreError> {
    let policy = store.create_policy(&firewall_id, req).await?;
    Ok((StatusCode::CREATED, Json(policy)))
}

async fn list_policies(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
) -> Result<Json<Vec<Policy>>, StoreError> {
    let policies = store.list_policies(&firewall_id).await?;
    Ok(Json(policies))
}

// --- Rule Handlers ---

async fn create_rule(
    State(store): State<Arc<AppStore>>,
    Path(policy_id): Path<String>,
    Json(req): Json<CreateRuleRequest>,
) -> Result<impl IntoResponse, StoreError> {
    let rule = store.create_rule(&policy_id, req).await?;
    Ok((StatusCode::CREATED, Json(rule)))
}

async fn list_rules(
    State(store): State<Arc<AppStore>>,
    Path(policy_id): Path<String>,
) -> Result<Json<Vec<Rule>>, StoreError> {
    let rules = store.list_rules(&policy_id).await?;
    Ok(Json(rules))
}

async fn delete_rule(
    State(store): State<Arc<AppStore>>,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, StoreError> {
    store.delete_rule(&id).await?;
    Ok(StatusCode::NO_CONTENT)
}

// --- Observability Handlers (mock data) ---

async fn list_sessions(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
) -> Result<Json<Vec<Session>>, StoreError> {
    // Verify firewall exists
    store.get_firewall(&firewall_id).await?;

    // Return mock session data
    let sessions = vec![
        Session {
            key: SessionKeyInfo {
                vni: 1000,
                src_ip: "10.0.1.5".into(),
                dst_ip: "10.0.2.10".into(),
                src_port: 44312,
                dst_port: 443,
                protocol: "tcp".into(),
            },
            state: "established".into(),
            offloaded: true,
            packets: 15420,
            bytes: 2_048_000,
            age_seconds: 120,
        },
        Session {
            key: SessionKeyInfo {
                vni: 1000,
                src_ip: "10.0.1.8".into(),
                dst_ip: "10.0.3.20".into(),
                src_port: 55100,
                dst_port: 80,
                protocol: "tcp".into(),
            },
            state: "established".into(),
            offloaded: false,
            packets: 230,
            bytes: 45_000,
            age_seconds: 5,
        },
    ];
    Ok(Json(sessions))
}

async fn get_metrics(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
) -> Result<Json<MetricsResponse>, StoreError> {
    // Verify firewall exists
    store.get_firewall(&firewall_id).await?;

    let metrics = MetricsResponse {
        throughput_bps: 9_500_000_000,
        packets_per_sec: 1_200_000,
        offload_ratio_percent: 87.5,
        active_sessions: 42_000,
        vm_cpu_percent: 12.3,
    };
    Ok(Json(metrics))
}

async fn flush_sessions(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
) -> Result<impl IntoResponse, StoreError> {
    // Verify firewall exists
    store.get_firewall(&firewall_id).await?;
    tracing::info!(firewall_id = %firewall_id, "flushing all sessions (mock)");
    Ok(StatusCode::NO_CONTENT)
}

// --- Desired State Handler ---

#[derive(serde::Serialize)]
struct DesiredStateResponse {
    new_generation: u64,
    policies_created: usize,
}

async fn push_desired_state(
    State(store): State<Arc<AppStore>>,
    Path(firewall_id): Path<String>,
    Json(req): Json<DesiredStateRequest>,
) -> Result<impl IntoResponse, StoreError> {
    let policies_count = req.policies.len();
    let new_gen = store.apply_desired_state(&firewall_id, req).await?;
    Ok((
        StatusCode::OK,
        Json(DesiredStateResponse {
            new_generation: new_gen,
            policies_created: policies_count,
        }),
    ))
}
