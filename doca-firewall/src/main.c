/*
 * DOCA Flow CT Firewall Daemon
 *
 * A connection-tracking firewall running on BlueField-3 DPU ARM cores.
 * Uses DOCA Flow CT for hardware-offloaded session management.
 *
 * Pipeline: ROOT (control) -> CT pipe -> [HIT: forward bypass | MISS: RSS to ARM]
 * On MISS: ARM evaluates 5-tuple ACL policy, offloads allowed sessions to CT.
 *
 * Uses the DOCA 3.4 sample framework (doca_argp, flow_switch_common) for
 * device discovery and port initialization.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>

#include <doca_argp.h>
#include <doca_log.h>
#include <doca_dpdk.h>

#include <flow_ct_common.h>
#include <flow_switch_common.h>
#include <common.h>
#include <dpdk_utils.h>

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

/*
 * Main entry point.
 * Uses the DOCA sample framework pattern:
 *   1. doca_argp for CLI parsing (EAL args handled via doca_argp_set_dpdk_program)
 *   2. init_doca_flow_devs() for device discovery
 *   3. dpdk_queues_and_ports_init() for DPDK port setup
 *   4. fw_flow_init() for our pipe creation
 */
int main(int argc, char **argv)
{
    doca_error_t result;
    struct doca_log_backend *sdk_log;
    int exit_status = EXIT_FAILURE;
    struct flow_switch_ctx switch_ctx = {0};
    struct application_dpdk_config dpdk_config = {
        .port_config.nb_ports = 1,
        .port_config.nb_queues = NB_QUEUES,
        .port_config.switch_mode = 1,
        .port_config.enable_mbuf_metadata = 1,
        .reserve_main_thread = false,
    };

    /* Setup DOCA logging */
    result = doca_log_backend_create_standard();
    if (result != DOCA_SUCCESS) {
        fprintf(stderr, "Failed to create log backend\n");
        goto sample_exit;
    }

    result = doca_log_backend_create_with_file_sdk(stderr, &sdk_log);
    if (result != DOCA_SUCCESS) {
        fprintf(stderr, "Failed to create SDK log backend\n");
        goto sample_exit;
    }
    result = doca_log_backend_set_sdk_level(sdk_log, DOCA_LOG_LEVEL_WARNING);
    if (result != DOCA_SUCCESS)
        goto sample_exit;

    DOCA_LOG_INFO("=== DOCA Flow CT Firewall Daemon ===");
    DOCA_LOG_INFO("Starting initialization...");

    /* Signal handling */
    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    /* Initialize DOCA ARGP (argument parser) */
    result = doca_argp_init(NULL, &switch_ctx);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init ARGP: %s", doca_error_get_descr(result));
        goto sample_exit;
    }

    /* Set DPDK initialization callback (called by doca_argp_start) */
    doca_argp_set_dpdk_program(flow_init_dpdk);

    /* Configure device context for switch mode */
    switch_ctx.devs_ctx.default_dev_args = FLOW_SWITCH_DEV_ARGS;
    switch_ctx.devs_ctx.port_cap = flow_ct_capable;

    /* Register CT parameters (allows --ct-flags etc on CLI) */
    result = flow_ct_register_params();
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to register CT params: %s", doca_error_get_descr(result));
        goto argp_cleanup;
    }

    /* Parse command line (this also triggers DPDK EAL init) */
    result = doca_argp_start(argc, argv);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to parse args / init DPDK: %s", doca_error_get_descr(result));
        goto argp_cleanup;
    }

    /* Discover and open DOCA devices (PF + representors) */
    result = init_doca_flow_devs(&switch_ctx.devs_ctx);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DOCA Flow devices: %s", doca_error_get_descr(result));
        goto dpdk_cleanup;
    }

    /* Initialize DPDK queues and ports */
    result = dpdk_queues_and_ports_init(&dpdk_config);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DPDK queues and ports: %s", doca_error_get_descr(result));
        goto dpdk_cleanup;
    }

    DOCA_LOG_INFO("Device discovery and DPDK init complete");

    /* Initialize metrics */
    metrics_init(&g_metrics);

    /* Initialize policy engine */
    policy_init(&g_policy);
    setup_default_rules(&g_policy);

    /* Initialize DOCA Flow pipes (our firewall logic) */
    result = fw_flow_init(&g_flow_ctx, &switch_ctx);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Flow pipeline init failed: %s", doca_error_get_descr(result));
        goto dpdk_ports_queues_cleanup;
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
    DOCA_LOG_INFO("Pipeline: ROOT -> CT -> [HIT: forward | MISS: RSS to ARM]");

    /* Enter main packet processing loop */
    ct_offload_main_loop(&g_ct_ctx);

    /* Shutdown */
    DOCA_LOG_INFO("Shutting down...");
    rest_api_stop(&g_rest_ctx);
    exit_status = EXIT_SUCCESS;

cleanup_flow:
    fw_flow_destroy(&g_flow_ctx);

dpdk_ports_queues_cleanup:
    dpdk_queues_and_ports_fini(&dpdk_config);

dpdk_cleanup:
    dpdk_fini();

argp_cleanup:
    doca_argp_destroy();

sample_exit:
    destroy_doca_flow_devs(&switch_ctx.devs_ctx);
    policy_destroy(&g_policy);

    if (exit_status == EXIT_SUCCESS)
        DOCA_LOG_INFO("=== Firewall daemon stopped cleanly ===");
    else
        DOCA_LOG_ERR("=== Firewall daemon stopped with errors ===");
    return exit_status;
}
