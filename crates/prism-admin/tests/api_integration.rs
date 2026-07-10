use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use tower::ServiceExt;

use prism_admin::api;
use prism_admin::models::*;
use prism_admin::store::AppStore;

/// Helper: build a test app with fresh store.
fn test_app() -> Router {
    let store = AppStore::new();
    Router::new()
        .route("/health", axum::routing::get(|| async { "ok" }))
        .nest("/api/v1", api::router(store))
}

/// Helper: send JSON POST and return (status, body bytes).
async fn post_json(app: &Router, uri: &str, body: &str) -> (StatusCode, Vec<u8>) {
    let req = Request::builder()
        .method("POST")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();

    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes().to_vec();
    (status, bytes)
}

/// Helper: send GET and return (status, body bytes).
async fn get_request(app: &Router, uri: &str) -> (StatusCode, Vec<u8>) {
    let req = Request::builder()
        .method("GET")
        .uri(uri)
        .body(Body::empty())
        .unwrap();

    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes().to_vec();
    (status, bytes)
}

/// Helper: send PUT with JSON body.
async fn put_json(app: &Router, uri: &str, body: &str) -> (StatusCode, Vec<u8>) {
    let req = Request::builder()
        .method("PUT")
        .uri(uri)
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();

    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes().to_vec();
    (status, bytes)
}

/// Helper: send DELETE.
async fn delete_request(app: &Router, uri: &str) -> (StatusCode, Vec<u8>) {
    let req = Request::builder()
        .method("DELETE")
        .uri(uri)
        .body(Body::empty())
        .unwrap();

    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes().to_vec();
    (status, bytes)
}

#[tokio::test]
async fn test_create_firewall_and_get() {
    let app = test_app();

    // Create a firewall
    let (status, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{
            "name": "prod-fw-1",
            "tenant_id": "tenant-abc",
            "vpc_id": "vpc-123",
            "vni": 5000,
            "mode": "inline"
        }"#,
    )
    .await;

    assert_eq!(status, StatusCode::CREATED);
    let fw: Firewall = serde_json::from_slice(&body).unwrap();
    assert_eq!(fw.name, "prod-fw-1");
    assert_eq!(fw.vni, 5000);
    assert_eq!(fw.mode, FirewallMode::Inline);
    assert_eq!(fw.admin_state, AdminState::Enabled); // default
    assert!(fw.generation > 0);

    // Get it back
    let (status, body) = get_request(&app, &format!("/api/v1/firewalls/{}", fw.id)).await;
    assert_eq!(status, StatusCode::OK);
    let got: Firewall = serde_json::from_slice(&body).unwrap();
    assert_eq!(got.id, fw.id);
    assert_eq!(got.generation, fw.generation);
}

#[tokio::test]
async fn test_list_firewalls() {
    let app = test_app();

    // Create two firewalls
    post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw1","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw2","tenant_id":"t1","vpc_id":"v2","vni":200,"mode":"tap"}"#,
    )
    .await;

    let (status, body) = get_request(&app, "/api/v1/firewalls").await;
    assert_eq!(status, StatusCode::OK);
    let firewalls: Vec<Firewall> = serde_json::from_slice(&body).unwrap();
    assert_eq!(firewalls.len(), 2);
}

#[tokio::test]
async fn test_delete_firewall() {
    let app = test_app();

    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw-del","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Delete it
    let (status, _) = delete_request(&app, &format!("/api/v1/firewalls/{}", fw.id)).await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    // Verify it's gone
    let (status, _) = get_request(&app, &format!("/api/v1/firewalls/{}", fw.id)).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn test_generation_conflict_returns_409() {
    let app = test_app();

    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw-conflict","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Try to update with wrong generation
    let (status, body) = put_json(
        &app,
        &format!("/api/v1/firewalls/{}", fw.id),
        r#"{"name":"new-name","generation":9999}"#,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
    let err: ApiErrorBody = serde_json::from_slice(&body).unwrap();
    assert_eq!(err.error, "conflict");

    // Update with correct generation should work
    let (status, body) = put_json(
        &app,
        &format!("/api/v1/firewalls/{}", fw.id),
        &format!(r#"{{"name":"new-name","generation":{}}}"#, fw.generation),
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let updated: Firewall = serde_json::from_slice(&body).unwrap();
    assert_eq!(updated.name, "new-name");
    assert!(updated.generation > fw.generation);
}

#[tokio::test]
async fn test_create_policy_and_rule() {
    let app = test_app();

    // Create firewall
    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw-policy","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Create policy
    let (status, body) = post_json(
        &app,
        &format!("/api/v1/firewalls/{}/policies", fw.id),
        r#"{"name":"ingress-policy","priority":100,"default_action":"deny"}"#,
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let policy: Policy = serde_json::from_slice(&body).unwrap();
    assert_eq!(policy.name, "ingress-policy");
    assert_eq!(policy.firewall_id, fw.id);
    assert_eq!(policy.default_action, RuleAction::Deny);

    // List policies
    let (status, body) = get_request(&app, &format!("/api/v1/firewalls/{}/policies", fw.id)).await;
    assert_eq!(status, StatusCode::OK);
    let policies: Vec<Policy> = serde_json::from_slice(&body).unwrap();
    assert_eq!(policies.len(), 1);

    // Create rule
    let (status, body) = post_json(
        &app,
        &format!("/api/v1/policies/{}/rules", policy.id),
        r#"{
            "priority": 10,
            "src_cidr": "10.0.0.0/8",
            "dst_cidr": "192.168.1.0/24",
            "protocol": "tcp",
            "dst_port_range": [80, 443],
            "action": "allow",
            "offload_eligible": true
        }"#,
    )
    .await;
    assert_eq!(status, StatusCode::CREATED);
    let rule: Rule = serde_json::from_slice(&body).unwrap();
    assert_eq!(rule.policy_id, policy.id);
    assert_eq!(rule.src_cidr, Some("10.0.0.0/8".into()));
    assert_eq!(rule.dst_port_range, Some((80, 443)));
    assert!(rule.offload_eligible);

    // List rules
    let (status, body) = get_request(&app, &format!("/api/v1/policies/{}/rules", policy.id)).await;
    assert_eq!(status, StatusCode::OK);
    let rules: Vec<Rule> = serde_json::from_slice(&body).unwrap();
    assert_eq!(rules.len(), 1);
}

#[tokio::test]
async fn test_delete_rule() {
    let app = test_app();

    // Create firewall -> policy -> rule
    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    let (_, body) = post_json(
        &app,
        &format!("/api/v1/firewalls/{}/policies", fw.id),
        r#"{"name":"p1","priority":1,"default_action":"allow"}"#,
    )
    .await;
    let policy: Policy = serde_json::from_slice(&body).unwrap();

    let (_, body) = post_json(
        &app,
        &format!("/api/v1/policies/{}/rules", policy.id),
        r#"{"priority":1,"action":"deny","offload_eligible":false}"#,
    )
    .await;
    let rule: Rule = serde_json::from_slice(&body).unwrap();

    // Delete rule
    let (status, _) = delete_request(&app, &format!("/api/v1/rules/{}", rule.id)).await;
    assert_eq!(status, StatusCode::NO_CONTENT);

    // Verify rules are empty
    let (_, body) = get_request(&app, &format!("/api/v1/policies/{}/rules", policy.id)).await;
    let rules: Vec<Rule> = serde_json::from_slice(&body).unwrap();
    assert_eq!(rules.len(), 0);
}

#[tokio::test]
async fn test_not_found_returns_404() {
    let app = test_app();

    let (status, _) = get_request(&app, "/api/v1/firewalls/nonexistent-id").await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn test_sessions_and_metrics_mock() {
    let app = test_app();

    // Create firewall
    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Sessions
    let (status, body) = get_request(&app, &format!("/api/v1/firewalls/{}/sessions", fw.id)).await;
    assert_eq!(status, StatusCode::OK);
    let sessions: Vec<Session> = serde_json::from_slice(&body).unwrap();
    assert_eq!(sessions.len(), 2);

    // Metrics
    let (status, body) = get_request(&app, &format!("/api/v1/firewalls/{}/metrics", fw.id)).await;
    assert_eq!(status, StatusCode::OK);
    let metrics: MetricsResponse = serde_json::from_slice(&body).unwrap();
    assert!(metrics.throughput_bps > 0);
    assert!(metrics.offload_ratio_percent > 0.0);
}

#[tokio::test]
async fn test_desired_state_bulk_push() {
    let app = test_app();

    // Create firewall
    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Push desired state
    let desired = format!(
        r#"{{
            "expected_generation": {},
            "policies": [
                {{
                    "name": "ingress",
                    "priority": 100,
                    "default_action": "deny",
                    "rules": [
                        {{"priority": 10, "src_cidr": "10.0.0.0/8", "action": "allow", "offload_eligible": true}},
                        {{"priority": 20, "protocol": "udp", "action": "deny", "offload_eligible": false}}
                    ]
                }},
                {{
                    "name": "egress",
                    "priority": 200,
                    "default_action": "allow",
                    "rules": []
                }}
            ]
        }}"#,
        fw.generation
    );

    let (status, body) = post_json(
        &app,
        &format!("/api/v1/firewalls/{}/desired-state", fw.id),
        &desired,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let resp: serde_json::Value = serde_json::from_slice(&body).unwrap();
    assert_eq!(resp["policies_created"], 2);
    assert!(resp["new_generation"].as_u64().unwrap() > fw.generation);

    // Verify policies were created
    let (_, body) = get_request(&app, &format!("/api/v1/firewalls/{}/policies", fw.id)).await;
    let policies: Vec<Policy> = serde_json::from_slice(&body).unwrap();
    assert_eq!(policies.len(), 2);
}

#[tokio::test]
async fn test_desired_state_conflict() {
    let app = test_app();

    // Create firewall
    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let _fw: Firewall = serde_json::from_slice(&body).unwrap();

    // Push desired state with wrong generation
    let (status, _) = post_json(
        &app,
        &format!("/api/v1/firewalls/{}/desired-state", _fw.id),
        r#"{"expected_generation": 9999, "policies": []}"#,
    )
    .await;
    assert_eq!(status, StatusCode::CONFLICT);
}

#[tokio::test]
async fn test_policy_on_nonexistent_firewall_returns_404() {
    let app = test_app();

    let (status, _) = post_json(
        &app,
        "/api/v1/firewalls/nonexistent/policies",
        r#"{"name":"p","priority":1,"default_action":"allow"}"#,
    )
    .await;
    assert_eq!(status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn test_flush_sessions() {
    let app = test_app();

    let (_, body) = post_json(
        &app,
        "/api/v1/firewalls",
        r#"{"name":"fw","tenant_id":"t1","vpc_id":"v1","vni":100,"mode":"inline"}"#,
    )
    .await;
    let fw: Firewall = serde_json::from_slice(&body).unwrap();

    let (status, _) = post_json(
        &app,
        &format!("/api/v1/firewalls/{}/sessions/flush", fw.id),
        "{}",
    )
    .await;
    assert_eq!(status, StatusCode::NO_CONTENT);
}
