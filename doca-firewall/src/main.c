/*
 * DOCA Flow CT Firewall Daemon
 *
 * A connection-tracking firewall running on BlueField-3 DPU ARM cores.
 * Uses DOCA Flow CT for hardware-offloaded session management.
 *
 * Pipeline: ROOT (control) -> CT pipe -> [HIT: forward bypass | MISS: RSS to ARM]
 * On MISS: ARM evaluates 5-tuple ACL policy, offloads allowed sessions to CT.
 *
 * Port mapping:
 *   port 0: uplink PF (pf0hpf)
 *   port 1: VF0 (pf0vf0) - internet
 *   port 2: VF1 (pf0vf1) - firewall in
 *   port 3: VF2 (pf0vf2) - firewall out
 *   port 4: VF3 (pf0vf3) - client
 *
 * PoC flow: VF0 (internet) <-> CT check <-> VF3 (client)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>

#include <rte_eal.h>
#include <rte_ethdev.h>
#include <rte_mbuf.h>

#include <doca_log.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>

#include "flow_init.h"
#include "policy.h"
#include "ct_offload.h"
#include "rest_api.h"
#include "metrics.h"

DOCA_LOG_REGISTER(FW_MAIN);

/* Global state */
static volatile bool g_running = true;
static struct fw_flow_ctx g_flow_ctx;
static struct policy_table g_policy;
static struct ct_offload_ctx g_ct_ctx;
static struct rest_api_ctx g_rest_ctx;
static struct fw_metrics g_metrics;

static void signal_handler(int signum)
{
    (void)signum;
    DOCA_LOG_INFO("Signal %d received, shutting down...", signum);
    g_running = false;
    ct_offload_stop(&g_ct_ctx);
}

/*
 * Initialize DPDK EAL with parameters appropriate for DPU switch mode.
 */
static int init_dpdk(int argc, char **argv)
{
    int ret;

    ret = rte_eal_init(argc, argv);
    if (ret < 0) {
        DOCA_LOG_ERR("Failed to init DPDK EAL: %s", rte_strerror(-ret));
        return ret;
    }

    DOCA_LOG_INFO("DPDK EAL initialized with %d args consumed", ret);
    return ret;
}

/*
 * Setup initial firewall rules (PoC defaults).
 * In production these would come from the REST API or a config file.
 */
static void setup_default_rules(struct policy_table *table)
{
    struct policy_rule rule;

    /* Rule 1: Allow all TCP port 80 (HTTP) */
    memset(&rule, 0, sizeof(rule));
    rule.protocol = IPPROTO_TCP;
    rule.dst_port_min = 80;
    rule.dst_port_max = 80;
    rule.src_port_max = 65535;
    rule.action = POLICY_ACTION_ALLOW;
    rule.priority = 10;
    rule.enabled = true;
    policy_add_rule(table, &rule);

    /* Rule 2: Allow all TCP port 443 (HTTPS) */
    memset(&rule, 0, sizeof(rule));
    rule.protocol = IPPROTO_TCP;
    rule.dst_port_min = 443;
    rule.dst_port_max = 443;
    rule.src_port_max = 65535;
    rule.action = POLICY_ACTION_ALLOW;
    rule.priority = 10;
    rule.enabled = true;
    policy_add_rule(table, &rule);

    /* Rule 3: Allow ICMP (protocol 1) for ping */
    memset(&rule, 0, sizeof(rule));
    rule.protocol = IPPROTO_ICMP;
    rule.src_port_max = 65535;
    rule.dst_port_max = 65535;
    rule.action = POLICY_ACTION_ALLOW;
    rule.priority = 20;
    rule.enabled = true;
    policy_add_rule(table, &rule);

    /* Rule 4: Allow DNS (UDP 53) */
    memset(&rule, 0, sizeof(rule));
    rule.protocol = IPPROTO_UDP;
    rule.dst_port_min = 53;
    rule.dst_port_max = 53;
    rule.src_port_max = 65535;
    rule.action = POLICY_ACTION_ALLOW;
    rule.priority = 10;
    rule.enabled = true;
    policy_add_rule(table, &rule);

    DOCA_LOG_INFO("Default policy rules loaded (HTTP, HTTPS, ICMP, DNS allowed)");
}

int main(int argc, char **argv)
{
    struct doca_log_backend *sdk_log;
    doca_error_t result;
    int dpdk_argc;

    /* Setup DOCA logging */
    result = doca_log_backend_create_standard();
    if (result != DOCA_SUCCESS) {
        fprintf(stderr, "Failed to create log backend\n");
        return EXIT_FAILURE;
    }

    result = doca_log_backend_create_with_file_sdk(stderr, &sdk_log);
    if (result != DOCA_SUCCESS) {
        fprintf(stderr, "Failed to create SDK log backend\n");
        return EXIT_FAILURE;
    }
    doca_log_backend_set_sdk_level(sdk_log, DOCA_LOG_LEVEL_WARNING);

    DOCA_LOG_INFO("=== DOCA Flow CT Firewall Daemon ===");
    DOCA_LOG_INFO("Starting initialization...");

    /* Signal handling */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Initialize DPDK EAL */
    dpdk_argc = init_dpdk(argc, argv);
    if (dpdk_argc < 0) {
        DOCA_LOG_ERR("DPDK EAL init failed");
        return EXIT_FAILURE;
    }

    /* Initialize metrics */
    metrics_init(&g_metrics);

    /* Initialize policy engine */
    policy_init(&g_policy);
    setup_default_rules(&g_policy);

    /* Initialize DOCA Flow (switch mode, ports, pipes) */
    result = fw_flow_init(&g_flow_ctx);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Flow initialization failed: %s", doca_error_get_descr(result));
        goto cleanup_policy;
    }

    /* Initialize CT offload */
    result = ct_offload_init(&g_ct_ctx, &g_flow_ctx, &g_policy, &g_metrics);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("CT offload init failed: %s", doca_error_get_descr(result));
        goto cleanup_flow;
    }

    /* Start REST API server */
    if (rest_api_start(&g_rest_ctx, &g_policy, &g_metrics) != 0) {
        DOCA_LOG_ERR("REST API start failed");
        goto cleanup_flow;
    }

    DOCA_LOG_INFO("=== Firewall daemon running ===");
    DOCA_LOG_INFO("REST API: http://0.0.0.0:%d", REST_API_PORT);
    DOCA_LOG_INFO("Endpoints: GET /health, GET /metrics, GET /rules, POST /rules, DELETE /rules/<id>");
    DOCA_LOG_INFO("Pipeline: VF0 (internet) <-> CT <-> VF3 (client)");

    /* Enter main packet processing loop */
    ct_offload_main_loop(&g_ct_ctx);

    /* Shutdown */
    DOCA_LOG_INFO("Shutting down...");

    rest_api_stop(&g_rest_ctx);

cleanup_flow:
    fw_flow_destroy(&g_flow_ctx);

cleanup_policy:
    policy_destroy(&g_policy);

    rte_eal_cleanup();

    DOCA_LOG_INFO("=== Firewall daemon stopped ===");
    return EXIT_SUCCESS;
}
