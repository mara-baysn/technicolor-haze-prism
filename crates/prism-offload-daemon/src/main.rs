use std::sync::Arc;
use std::time::Duration;

use tonic::transport::Server;
use tracing_subscriber::EnvFilter;

use prism_proto::session_offload_service_server::SessionOffloadServiceServer;

use prism_offload_daemon::aging::SessionAger;
use prism_offload_daemon::doca_flow::DocaFlowManager;
use prism_offload_daemon::grpc_server::OffloadServiceImpl;
use prism_offload_daemon::session_table::SessionTable;

/// Parse --listen-addr from command line args, defaulting to [::]:50051.
fn parse_listen_addr() -> String {
    let args: Vec<String> = std::env::args().collect();
    for i in 0..args.len() {
        if args[i] == "--listen-addr" {
            if let Some(addr) = args.get(i + 1) {
                return addr.clone();
            }
        }
    }
    "[::]:50051".to_string()
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    tracing::info!("prism-offload-daemon starting");

    let listen_addr = parse_listen_addr();
    tracing::info!(listen_addr = %listen_addr, "configuration loaded");

    // Initialize DOCA Flow manager
    let doca_manager = Arc::new(
        DocaFlowManager::init().expect("failed to initialize DOCA Flow manager"),
    );
    tracing::info!("DOCA Flow manager initialized");

    // Create session table
    let session_table = Arc::new(SessionTable::new());
    tracing::info!("session table initialized");

    // Set up graceful shutdown
    let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);

    // Spawn aging task
    let ager = SessionAger::new(Duration::from_secs(60), Duration::from_secs(30));
    let aging_table = session_table.clone();
    let aging_doca = doca_manager.clone();
    tokio::spawn(async move {
        ager.run(aging_table, aging_doca, shutdown_rx).await;
    });
    tracing::info!("session aging loop spawned");

    // Build gRPC service
    let service = OffloadServiceImpl::new(session_table.clone(), doca_manager.clone());
    let svc = SessionOffloadServiceServer::new(service);

    let addr = listen_addr.parse()?;
    tracing::info!(addr = %addr, "starting gRPC server");

    // Start server with graceful shutdown on SIGTERM
    Server::builder()
        .add_service(svc)
        .serve_with_shutdown(addr, async {
            tokio::signal::ctrl_c()
                .await
                .expect("failed to listen for ctrl_c");
            tracing::info!("received shutdown signal");
            let _ = shutdown_tx.send(true);
        })
        .await?;

    tracing::info!("prism-offload-daemon shutting down");
    Ok(())
}
