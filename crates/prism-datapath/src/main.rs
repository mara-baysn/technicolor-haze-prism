use std::sync::atomic::Ordering;
use std::sync::Arc;

use tokio::sync::mpsc;
use tracing_subscriber::EnvFilter;

use prism_datapath::acl::{AclAction, AclEngine, AclRule};
use prism_datapath::config::DatapathConfig;
use prism_datapath::metrics::PipelineMetrics;
use prism_datapath::offload::OffloadRequest;
use prism_datapath::offload_client::OffloadClient;
use prism_datapath::pipeline::Pipeline;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    let config = DatapathConfig::from_env();
    tracing::info!(mock_mode = config.mock_mode, "prism-datapath starting");

    // Load ACL rules
    let acl = if config.mock_mode || config.acl_rules_path.is_empty() {
        tracing::info!("using default allow-all ACL rules (mock mode)");
        AclEngine::new(vec![AclRule {
            id: "default-allow".to_string(),
            priority: 1000,
            src_cidr: None,
            dst_cidr: None,
            src_port_range: None,
            dst_port_range: None,
            protocol: None,
            action: AclAction::Allow,
            offload_eligible: true,
        }])
    } else {
        let rules_json = std::fs::read_to_string(&config.acl_rules_path)?;
        AclEngine::from_json(&rules_json)?
    };

    // Create metrics
    let metrics = Arc::new(PipelineMetrics::default());

    // Create offload channel
    let (offload_tx, mut offload_rx) = mpsc::channel::<OffloadRequest>(1024);

    // Create pipeline
    let mut pipeline = Pipeline::new(
        acl,
        config.offload_min_packets,
        config.idle_timeout_secs,
        Arc::clone(&metrics),
        offload_tx,
    );

    // Spawn offload sender task
    let offload_addr = config.offload_daemon_addr.clone();
    let mock_mode = config.mock_mode;
    tokio::spawn(async move {
        if mock_mode {
            while let Some(req) = offload_rx.recv().await {
                tracing::info!(
                    vni = req.vni,
                    src_ip = req.tuple.src_ip,
                    dst_ip = req.tuple.dst_ip,
                    "mock offload: would program session"
                );
            }
        } else {
            let mut client = match OffloadClient::connect(&offload_addr).await {
                Ok(c) => c,
                Err(e) => {
                    tracing::error!(error = %e, "failed to connect to offload daemon");
                    // Drain remaining requests so senders don't block
                    while offload_rx.recv().await.is_some() {}
                    return;
                }
            };
            while let Some(req) = offload_rx.recv().await {
                match client.program_session(&req).await {
                    Ok(handle) => {
                        tracing::debug!(
                            vni = req.vni,
                            entry_handle = %handle,
                            "session offloaded"
                        );
                    }
                    Err(e) => {
                        tracing::warn!(
                            vni = req.vni,
                            error = %e,
                            "failed to program session"
                        );
                    }
                }
            }
        }
    });

    // Spawn periodic stats reporter (every 1 second)
    let stats_metrics = Arc::clone(&metrics);
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
        loop {
            interval.tick().await;
            let snap = stats_metrics.snapshot();
            tracing::info!(
                metrics = %serde_json::to_string(&snap).unwrap_or_default(),
                "pipeline stats"
            );
        }
    });

    // Spawn conntrack eviction task (every 30 seconds)
    // Note: conntrack is owned by pipeline which is not Send-safe due to HashMap.
    // In production this would use a separate thread or DashMap. For now the eviction
    // runs inline in mock mode's packet loop below.

    // Handle SIGTERM
    let shutdown = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install ctrl+c handler");
        tracing::info!("received shutdown signal");
    };

    if config.mock_mode {
        tracing::info!("mock mode: generating synthetic packets");
        let packet_loop = async {
            let mut tick = 0u64;
            let mut evict_counter = 0u32;
            loop {
                // Generate synthetic packet every 1ms
                tokio::time::sleep(std::time::Duration::from_millis(1)).await;

                let src_ip = 0x0A000001u32.wrapping_add((tick % 10) as u32);
                let dst_ip = 0xC0A80001;
                let dst_port = 443u16;
                let src_port = 10000 + (tick % 1000) as u16;

                let _verdict = pipeline.process_packet(
                    100,     // vni
                    src_ip,  // src_ip
                    dst_ip,  // dst_ip
                    src_port,
                    dst_port,
                    6,   // TCP
                    128, // pkt_len
                );

                tick += 1;

                // Evict idle entries every ~30 seconds (30000 ticks at 1ms)
                evict_counter += 1;
                if evict_counter >= 30000 {
                    let evicted = pipeline.conntrack.evict_idle();
                    if evicted > 0 {
                        tracing::debug!(evicted, "evicted idle conntrack entries");
                    }
                    metrics.active_flows.store(
                        pipeline.conntrack.active_count() as u64,
                        Ordering::Relaxed,
                    );
                    evict_counter = 0;
                }
            }
        };

        tokio::select! {
            _ = packet_loop => {}
            _ = shutdown => {}
        }
    } else {
        // In production, DPDK EAL would drive packet processing on lcores.
        // For now, just wait for shutdown.
        tracing::info!("waiting for DPDK EAL initialization (not implemented)");
        shutdown.await;
    }

    tracing::info!("prism-datapath shutting down");
    let final_snap = metrics.snapshot();
    tracing::info!(
        metrics = %serde_json::to_string(&final_snap).unwrap_or_default(),
        "final pipeline stats"
    );

    Ok(())
}
