use tracing_subscriber::EnvFilter;

fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(EnvFilter::from_default_env())
        .json()
        .init();

    tracing::info!("prism-datapath starting");

    // TODO: DPDK EAL initialization
    // TODO: Port configuration (In VF + Out VF)
    // TODO: Pipeline launch on lcores

    tracing::info!("prism-datapath shutting down");
    Ok(())
}
