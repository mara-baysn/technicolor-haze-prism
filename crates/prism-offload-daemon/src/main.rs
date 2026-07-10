use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    tracing::info!("prism-offload-daemon starting");

    // TODO: Initialize DOCA Flow
    // TODO: Start gRPC server on :50051
    // TODO: Start session aging loop

    tracing::info!("prism-offload-daemon shutting down");
    Ok(())
}
