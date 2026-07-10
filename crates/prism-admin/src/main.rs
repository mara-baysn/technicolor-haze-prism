use axum::Router;
use tower_http::cors::CorsLayer;
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    tracing::info!("prism-admin starting on :8443");

    let app = Router::new()
        .nest("/api/v1", api::router())
        .layer(CorsLayer::permissive());

    let listener = tokio::net::TcpListener::bind("0.0.0.0:8443").await?;
    axum::serve(listener, app).await?;

    Ok(())
}

mod api {
    use axum::Router;

    pub fn router() -> Router {
        Router::new()
            .route("/health", axum::routing::get(health))
    }

    async fn health() -> &'static str {
        "ok"
    }
}
