# Production Readiness Report: Prism Virtual Firewall on BF3 DPU

**Date:** 2026-07-14
**Scope:** technicolor-haze-prism (tc-firewall daemon, offload daemon, control plane)
**Basis:** 15 expert reviews (security, SRE, performance, NAT, multi-tenancy, API design, testing, operations, compliance, hardware, scalability, DX, HA, business viability, maintainability)

---

## VERDICT: NOT READY

The PoC successfully validates the core architecture (148 Gbps hardware offload, DOCA Flow integration, sub-millisecond failover design). However, the gap between the current implementation and a production service accepting paying tenants is substantial. There are 7 critical blockers that represent security, reliability, and correctness risks incompatible with production operation.

**Estimated total effort to production-ready:** 12-16 engineering weeks (2 senior engineers).

---

## 1. CRITICAL BLOCKERS (must fix before production)

| # | Finding | Effort |
|---|---------|--------|
| C1 | **No API authentication.** Zero auth on the control plane API (no mTLS, no JWT, no API keys). Any network-reachable client can add/delete/flush all firewall and NAT rules. Complete firewall bypass. | 3-5 days |
| C2 | **Race condition in handle tracking.** tc_manager handle allocation is not thread-safe. Concurrent rule creation can corrupt the handle map, causing rules to silently fail or overwrite each other. | 2-3 days |
| C3 | **No idempotency on rule creation.** POST /rules always creates a new tc-flower entry. Network retries consume hardware TCAM slots (BF3 has finite capacity). Resource exhaustion vector in production. | 2-3 days |
| C4 | **Split-brain in HA failover.** No fencing, quorum, or leader election defined. Network partition causes dual-active: both VMs program their DPUs, edge router receives conflicting BGP, traffic splits unpredictably. | 1-2 weeks |
| C5 | **No audit trail.** Stdout-only logging with no actor identity, no old/new state diffs, no immutable log store. Non-compliant with SOC2/PCI-DSS/ISO 27001. Cannot attribute WHO changed WHAT. | 3-5 days |
| C6 | **SNAT port allocation missing.** NAT implementation does static 1:1 IP rewrite only. No ephemeral port tracking, no port exhaustion handling, no concurrent session disambiguation. Multiple tenants behind same public IP will collide. | 1-2 weeks |
| C7 | **Per-tenant isolation not implemented.** Architecture claims mTLS tenant validation on ProgramSession calls; actual code has zero tenant scoping. Any tenant can manipulate any other tenant's rules. | 1 week |

---

## 2. HIGH PRIORITY (fix within 2 weeks of launch)

| # | Finding | Effort |
|---|---------|--------|
| H1 | **No API versioning.** All routes are bare (/rules, /nat, /health). Breaking changes force atomic client updates. Add /api/v1/ prefix. | 4 hours |
| H2 | **No integration or E2E tests.** All 151 tests mock subprocess. No test ever runs a real tc command or validates command syntax. Regressions in tc-flower argument ordering are invisible. | 1-2 weeks |
| H3 | **No deployment automation.** Zero IaC (no Ansible, Terraform, Helm). Deployment is manual SSH + nohup per DEMO.md. Cannot scale beyond a handful of hosts. | 1 week |
| H4 | **TCAM capacity conflation.** Architecture conflates 2-16M CT flow entries (DOCA hash-match) with tc-flower TCAM (512K-1M entries). PoC path (tc-flower) will hit limits well before documented thresholds. | 3-5 days |
| H5 | **Error messages are opaque.** Subprocess failures return raw tc stderr to the API caller. No structured error codes, no remediation hints, no correlation IDs. | 3-5 days |
| H6 | **No rate limiting or request validation bounds.** API accepts unbounded rule counts. A single API call loop can exhaust all TCAM entries or OOM the daemon. | 2-3 days |
| H7 | **Health endpoint is shallow.** /health returns 200 if the process is alive. Does not verify tc subsystem responsiveness, DPU reachability, or rule-table consistency. | 1-2 days |

---

## 3. MEDIUM PRIORITY (fix within 1 month)

| # | Finding | Effort |
|---|---------|--------|
| M1 | **Code duplication in nat_manager.** _reapply_snat duplicates add_snat; _reapply_nat_rule duplicates add_dnat/add_port_forward. Divergence risk on future changes. | 1-2 days |
| M2 | **No graceful shutdown or rule persistence.** Daemon restart loses in-memory rule state. Rules must be re-synced from an external source of truth (not yet defined). | 3-5 days |
| M3 | **No observability stack.** No Prometheus metrics, no OpenTelemetry traces, no structured JSON logging. Debugging production incidents requires SSH and grep. | 1 week |
| M4 | **No load/stress testing.** Zero concurrency tests, zero scale tests. 83-tenant-per-DPU model is unvalidated under contention. | 1 week |
| M5 | **Conntrack state not replicated in HA.** Failover preserves rules but drops all connection tracking. Established TCP sessions break on failover despite sub-ms switchover. | 1-2 weeks |
| M6 | **TLS not configured.** API listens on :8443 (implying TLS intent) but serves plain HTTP. Data in transit is unencrypted. | 1-2 days |
| M7 | **No resource quotas per tenant.** No TCAM budget, no rule-count ceiling, no bandwidth cap per tenant. One tenant can monopolize shared hardware resources. | 3-5 days |
| M8 | **Dockerfile runs as root.** No USER directive, no capability dropping. Container compromise gives full host network namespace access on DPU. | 4 hours |

---

## 4. LOW PRIORITY (nice to have)

| # | Finding | Effort |
|---|---------|--------|
| L1 | **OpenAPI docs not mentioned in any documentation.** FastAPI auto-generates /docs but DEMO.md only shows curl examples. New engineers miss the interactive API explorer. | 1 hour |
| L2 | **No client SDK or CLI tool.** All interaction is raw HTTP. A thin Python/Go client would reduce integration errors. | 3-5 days |
| L3 | **No chaos/fault-injection testing framework.** HA claims are unverified under real network partitions. | 1 week |
| L4 | **No runbook or incident playbook.** Operations team has no documented procedures for common failure modes. | 2-3 days |
| L5 | **Mixed sync/async patterns.** FastAPI endpoints are sync (blocking) despite the framework supporting async. Under load, thread pool exhaustion is possible. | 1-2 days |
| L6 | **No configuration management.** Hardcoded values (interface names, IP ranges) scattered through source. Should externalize to env vars or config file with validation. | 1-2 days |

---

## Summary: Path to Production

```
Phase 1 (Weeks 1-4): Fix Critical Blockers
  - mTLS + tenant auth (C1, C7)
  - Handle race condition fix (C2)
  - Idempotency layer (C3)
  - Audit logging with structured events (C5)
  - SNAT port allocation (C6)

Phase 2 (Weeks 5-8): HA + Reliability
  - Split-brain prevention with fencing (C4)
  - Integration test suite against network namespaces (H2)
  - Deployment automation (H3)
  - Observability stack (M3)

Phase 3 (Weeks 9-12): Hardening
  - Rate limiting + resource quotas (H6, M7)
  - Conntrack replication (M5)
  - Load testing at 83-tenant scale (M4)
  - TLS configuration (M6)

Phase 4 (Weeks 13-16): Polish
  - API versioning (H1)
  - Error handling improvements (H5)
  - Graceful shutdown + persistence (M2)
  - Documentation + runbooks (L4)
```

---

## What the PoC Proved (credit where due)

- 148 Gbps sustained throughput with hardware offload -- validates the DPU-based architecture
- DOCA Flow integration design is sound for production-scale session tables (2-16M entries)
- Per-tenant cost model ($5-8/month hardware amortization) is commercially viable
- Sub-millisecond failover design is architecturally feasible
- Service function chaining concept works (proven in poc-herd-sf-integration)

The engineering work is real and the architecture is validated. The gap is entirely in production hardening -- security, reliability, and operational tooling that separates a successful PoC from a service customers trust with their traffic.
