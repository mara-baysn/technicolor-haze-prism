# Prism Virtual Firewall PoC — Task Runner

default:
    @just --list

# Build all Rust crates
build:
    cargo build --workspace

# Build DPU offload daemon for aarch64
build-dpu:
    cargo build -p prism-offload-daemon --target aarch64-unknown-linux-musl --release

# Run all Rust tests
test:
    cargo test --workspace

# Run clippy lints
clippy:
    cargo clippy --workspace -- -D warnings

# Format all Rust code
fmt:
    cargo fmt --all

# Check formatting
fmt-check:
    cargo fmt --all -- --check

# Build UI
ui-build:
    cd ui && npm run build

# Start UI dev server
ui-dev:
    cd ui && npm run dev

# Start api-server
api-dev:
    cd api-server && uvicorn src.main:app --reload --port 8000

# Deploy offload daemon to DPU #1
deploy-dpu:
    scp target/aarch64-unknown-linux-musl/release/prism-offload-daemon ubuntu@192.168.0.38:/usr/local/bin/
    scp deploy/systemd/prism-offload-daemon.service ubuntu@192.168.0.38:/etc/systemd/system/
    ssh ubuntu@192.168.0.38 'systemctl daemon-reload && systemctl restart prism-offload-daemon'

# Deploy datapath + admin to HPE VM
deploy-vm:
    scp target/release/prism-datapath almalinux@192.168.9.23:/usr/local/bin/
    scp target/release/prism-admin almalinux@192.168.9.23:/usr/local/bin/

# Deploy observability stack to ARM server
deploy-observability:
    rsync -avz api-server/ almalinux@192.168.9.16:~/prism-api-server/
    rsync -avz ui/dist/ almalinux@192.168.9.16:~/prism-ui/
    ssh almalinux@192.168.9.16 'cd ~/prism-api-server && pip install -e . && systemctl --user restart prism-api-server'

# Full CI check (local)
ci: fmt-check clippy test
