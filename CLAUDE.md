# Prism Virtual Firewall PoC

DPU-accelerated virtual firewall with hardware session offload via BlueField-3 eSwitch.

## Build Commands

```bash
just build          # Build all Rust crates
just build-dpu      # Cross-compile offload daemon for aarch64-musl
just test           # Run all tests
just clippy         # Lint
just fmt            # Format
just ci             # Full local CI (fmt + clippy + test)
just ui-dev         # Start React dev server (:3000)
just api-dev        # Start FastAPI server (:8000)
```

## Architecture

- `crates/prism-datapath` — DPDK packet pipeline (x86 VM, 16 cores)
- `crates/prism-offload-daemon` — DPU ARM daemon, programs eSwitch via DOCA Flow
- `crates/prism-admin` — REST API on :8443 (Blue plane)
- `crates/prism-proto` — Protobuf/tonic gRPC types for session offload
- `crates/prism-doca-sys` — Bindgen FFI to DOCA Flow C API
- `ui/` — React + Vite interactive dashboard
- `api-server/` — Python FastAPI test orchestration backend

## Lab Hardware (Plano, air-gapped)

| Device | IP | Role |
|--------|-----|------|
| HPE x86 | 192.168.9.23 | Prism VM host |
| BF3 DPU #1 | 192.168.0.38 | eSwitch offload |
| BF3 DPU #2 | 192.168.0.32 | Traffic source |
| Fabric Switch | 192.168.0.29 | Green plane |
| Gigabyte ARM | 192.168.9.16 | Control plane |

## Development Without Hardware

All Rust crates compile and test without DOCA SDK or DPDK:
- `prism-doca-sys` falls back to mock bindings when DOCA_ROOT is not found
- `prism-datapath` has `--mock-dpdk` mode using in-memory packet buffers
- `api-server` streams synthetic mock metrics when hardware is unreachable
