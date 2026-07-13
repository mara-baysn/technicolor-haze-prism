#!/bin/bash
# Starts all services for the Prism firewall demo
# Run from any machine that can SSH to all hosts

set -e

echo "========================================="
echo "  Prism Virtual Firewall PoC Demo"
echo "========================================="
echo ""

SSH_KEY="${PRISM_SSH_KEY:-~/.ssh/id_rsa_plano_vms}"
HPE_HOST="${PRISM_HPE_HOST:-almalinux@192.168.9.23}"
ARM_HOST="${PRISM_ARM_HOST:-almalinux@192.168.9.16}"
DPU_FIREWALL="${PRISM_FIREWALL_URL:-http://192.168.0.38:8443}"

# 1. Check firewall daemon on DPU
echo "[1/5] Checking firewall daemon on DPU..."
if curl -sf "${DPU_FIREWALL}/health" > /dev/null 2>&1; then
    health=$(curl -s "${DPU_FIREWALL}/health")
    echo "  OK: ${health}"
else
    echo "  WARN: Firewall daemon not reachable at ${DPU_FIREWALL}"
    echo "  (Start it on the DPU: cd ~/prism-tc-firewall && uvicorn src.main:app --host 0.0.0.0 --port 8443)"
fi
echo ""

# 2. Start traffic generator on HPE (ns-inet namespace)
echo "[2/5] Starting traffic generator on HPE (ns-inet)..."
ssh -i "${SSH_KEY}" "${HPE_HOST}" \
  'sudo pkill -f "python3.*prism-traffic-gen" 2>/dev/null || true; \
   sleep 0.5; \
   sudo ip netns exec ns-inet nohup python3 -m src.web \
     --directory ~/prism-traffic-gen \
     > /tmp/prism-gen.log 2>&1 &'
echo "  Started (log: /tmp/prism-gen.log on HPE)"
echo ""

# 3. Start receiver on HPE (ns-client namespace)
echo "[3/5] Starting receiver on HPE (ns-client)..."
ssh -i "${SSH_KEY}" "${HPE_HOST}" \
  'sudo pkill -f "python3.*prism-receiver" 2>/dev/null || true; \
   sleep 0.5; \
   sudo ip netns exec ns-client nohup python3 -m src.web \
     --directory ~/prism-receiver \
     > /tmp/prism-recv.log 2>&1 &'
echo "  Started (log: /tmp/prism-recv.log on HPE)"
echo ""

# 4. Start API orchestrator on ARM server
echo "[4/5] Starting API orchestrator on ARM..."
ssh -i "${SSH_KEY}" "${ARM_HOST}" \
  'pkill -f "uvicorn src.main:app" 2>/dev/null || true; \
   sleep 0.5; \
   cd ~/prism-api-server && \
   nohup uvicorn src.main:app --host 0.0.0.0 --port 8000 \
     > /tmp/prism-api.log 2>&1 &'
echo "  Started (log: /tmp/prism-api.log on ARM)"
echo ""

# 5. Verify services
echo "[5/5] Verifying services..."
sleep 2

check_service() {
    local name=$1
    local url=$2
    if curl -sf "${url}" > /dev/null 2>&1; then
        echo "  [OK] ${name}"
    else
        echo "  [!!] ${name} - not responding at ${url}"
    fi
}

check_service "Firewall (DPU)" "${DPU_FIREWALL}/health"
check_service "Traffic Gen (HPE)" "http://192.168.9.23:5001/api/stats"
check_service "Receiver (HPE)" "http://192.168.9.23:5002/api/stats"
check_service "Orchestrator (ARM)" "http://192.168.9.16:8000/health"

echo ""
echo "========================================="
echo "  Demo Ready!"
echo "========================================="
echo ""
echo "  Dashboard:     http://192.168.9.16:8000"
echo "  Firewall API:  ${DPU_FIREWALL}"
echo "  Generator API: http://192.168.9.23:5001"
echo "  Receiver API:  http://192.168.9.23:5002"
echo ""
echo "  Quick test:"
echo "    curl ${DPU_FIREWALL}/health"
echo "    curl http://192.168.9.16:8000/api/firewall/rules"
echo ""
