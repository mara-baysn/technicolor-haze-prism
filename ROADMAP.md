# Prism Virtual Firewall — Production Roadmap

## PoC Results (Completed)
- 148 Gbps throughput (tc-flower, BF3 eSwitch)
- Hardware-offloaded deny/allow (in_hw=true)
- REST API for rule management
- Zero CPU for forwarded traffic
- Sub-millisecond latency

## Phase 2: DOCA Flow CT (Hardware Connection Tracking)
- Fix EGRESS domain pipe forwarding (deliver CT HIT to host VF)
- Replace tc-flower with DOCA Flow CT for stateful tracking
- First-packet inspection on ARM, session offload to silicon
- Target: same 148 Gbps but with per-flow stateful decisions

## Phase 3: Tier 3 Inspection VM
- Deploy DPDK-based inspection VM on x86 host
- 3-interface model: Admin(Blue) + In(Green) + Out(Green/Red)
- DPU steers new flows to VM via VF passthrough (VFIO)
- VM runs conntrack + ACL + AppID (first N packets)
- After classification: offload to eSwitch (session table)
- Target: 100 Gbps aggregate with 80% offload ratio

## Phase 4: Multi-Tenancy
- Per-tenant VF pairs (In + Out) with isolation
- Per-tenant rule namespaces in the firewall daemon
- API: /tenants/{tenant_id}/rules
- Tenant quotas (max rules, max sessions, bandwidth limits)
- VNI-based isolation for overlapping CIDRs
- Scale: 125 tenants per BF3 (250 VFs / 2)

## Phase 5: Production Hardening
- mTLS on Admin API (Blue plane only)
- Prometheus metrics exporter (sessions, throughput, latency histograms)
- Grafana dashboards for operations
- High Availability (active-standby Prism instances)
- Session state replication (for failover)
- Audit logging (all rule changes → immutable log)
- Integration with DPU Orchestrator (automated VF provisioning)

## Phase 6: L7 / NGFW Features (Scale-Out)
- Application identification (AppID, first N packets)
- TLS inspection (with tenant opt-in)
- IDS/IPS signature matching (Hyperscan on x86)
- DNS filtering
- Rate limiting per tenant/flow

## Installation Requirements (per node)
- Host: AlmaLinux 10.2+, DPDK 25.11, hugepages
- DPU: Ubuntu 24.04, DOCA 3.4+, switchdev mode
- Firewall daemon: systemd service on DPU
- Inspection VM: Cloud Hypervisor via herd-handler
- Orchestrator: runs on control plane (ARM server or K8s)
