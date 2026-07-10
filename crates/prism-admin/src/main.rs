use axum::Router;
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;
use tracing_subscriber::EnvFilter;

use prism_admin::reconciler::Reconciler;
use prism_admin::store::AppStore;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    tracing::info!("prism-admin starting on :8443");

    let store = AppStore::new();

    // Spawn the background reconciler
    let reconciler = Reconciler::new(store.clone());
    reconciler.spawn();

    let app = Router::new()
        .route("/health", axum::routing::get(health))
        .nest("/api/v1", prism_admin::api::router(store))
        .layer(TraceLayer::new_for_http())
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8443").await?;
    axum::serve(listener, app).await?;

    Ok(())
}

async fn health() -> &'static str {
    "ok"
}
