/*
 * DOCA Flow CT Firewall - Flow Initialization Implementation
 * Sets up DOCA Flow in switch mode with CT pipeline.
 */

#include <string.h>
#include <stdlib.h>

#include <doca_log.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>

#include <rte_ethdev.h>

#include "flow_init.h"

DOCA_LOG_REGISTER(FW_FLOW_INIT);

/* Port devargs for switch mode representors */
static const char *port_devargs[NUM_PORTS] = {
    "representor=pf0hpf",   /* uplink PF representor */
    "representor=pf0vf0",   /* VF0 - internet */
    "representor=pf0vf1",   /* VF1 - firewall in */
    "representor=pf0vf2",   /* VF2 - firewall out */
    "representor=pf0vf3",   /* VF3 - client */
};

/* Entry process callback */
static void entry_process_cb(struct doca_flow_pipe_entry *entry,
                             uint16_t pipe_queue,
                             enum doca_flow_entry_status status,
                             enum doca_flow_entry_op op,
                             void *user_ctx)
{
    (void)entry;
    (void)pipe_queue;
    (void)user_ctx;

    if (status != DOCA_FLOW_ENTRY_STATUS_SUCCESS) {
        DOCA_LOG_WARN("Entry operation %d failed with status %d", op, status);
    }
}

/*
 * Create RSS pipe for sending CT MISS traffic to ARM cores.
 */
static doca_error_t create_rss_pipe(struct doca_flow_port *port,
                                    struct doca_flow_pipe **pipe)
{
    struct doca_flow_match match;
    struct doca_flow_pipe_cfg *cfg;
    struct doca_flow_fwd fwd;
    uint16_t rss_queues[1] = {0};
    doca_error_t result;

    memset(&match, 0, sizeof(match));
    memset(&fwd, 0, sizeof(fwd));

    result = doca_flow_pipe_cfg_create(&cfg, port);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create RSS pipe cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_pipe_cfg_set_name(cfg, "RSS_MISS_PIPE");
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_type(cfg, DOCA_FLOW_PIPE_BASIC);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_nr_entries(cfg, 1);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    /* RSS to queue 0 on this port (switch port) */
    fwd.type = DOCA_FLOW_FWD_RSS;
    fwd.rss_type = DOCA_FLOW_RESOURCE_TYPE_NON_SHARED;
    fwd.rss.queues_array = rss_queues;
    fwd.rss.nr_queues = 1;
    fwd.rss.outer_flags = DOCA_FLOW_RSS_IPV4 | DOCA_FLOW_RSS_TCP | DOCA_FLOW_RSS_UDP;

    result = doca_flow_pipe_create(cfg, &fwd, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create RSS pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Add a wildcard entry to match all traffic */
    result = doca_flow_pipe_basic_add_entry(0, *pipe, &match, 0, NULL, NULL, &fwd, 0, NULL, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add RSS pipe entry: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_entries_process(port, 0, DEFAULT_TIMEOUT_US, 0);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to process RSS entry: %s", doca_error_get_descr(result));
    }

    DOCA_LOG_INFO("RSS pipe created successfully");
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Create post-CT forwarding pipe.
 * CT HIT traffic goes here: VF0->VF3 or VF3->VF0 based on port_meta.
 * For simplicity, forward all CT HIT to VF3 (client) in origin direction.
 */
static doca_error_t create_post_ct_pipe(struct doca_flow_port *port,
                                        struct doca_flow_pipe **pipe)
{
    struct doca_flow_match match;
    struct doca_flow_pipe_cfg *cfg;
    struct doca_flow_fwd fwd;
    doca_error_t result;

    memset(&match, 0, sizeof(match));
    memset(&fwd, 0, sizeof(fwd));

    result = doca_flow_pipe_cfg_create(&cfg, port);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create post-CT pipe cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_pipe_cfg_set_name(cfg, "POST_CT_FWD_PIPE");
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_type(cfg, DOCA_FLOW_PIPE_BASIC);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_nr_entries(cfg, 4);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    /* Default forward to VF3 (client) port_id=4 */
    fwd.type = DOCA_FLOW_FWD_PORT;
    fwd.port_id = PORT_VF3;

    result = doca_flow_pipe_create(cfg, &fwd, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create post-CT pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Add wildcard entry */
    result = doca_flow_pipe_basic_add_entry(0, *pipe, &match, 0, NULL, NULL, &fwd, 0, NULL, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add post-CT pipe entry: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_entries_process(port, 0, DEFAULT_TIMEOUT_US, 0);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to process post-CT entry: %s", doca_error_get_descr(result));
    }

    DOCA_LOG_INFO("Post-CT forwarding pipe created successfully");
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Create the CT pipe.
 * HIT → post_ct_pipe (forwarding)
 * MISS → rss_pipe (to ARM for policy evaluation)
 */
static doca_error_t create_ct_pipe(struct doca_flow_port *port,
                                   struct doca_flow_pipe *post_ct_pipe,
                                   struct doca_flow_pipe *rss_pipe,
                                   struct doca_flow_pipe **pipe)
{
    struct doca_flow_match match;
    struct doca_flow_pipe_cfg *cfg;
    struct doca_flow_fwd fwd;
    struct doca_flow_fwd fwd_miss;
    doca_error_t result;

    memset(&match, 0, sizeof(match));
    memset(&fwd, 0, sizeof(fwd));
    memset(&fwd_miss, 0, sizeof(fwd_miss));

    result = doca_flow_pipe_cfg_create(&cfg, port);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create CT pipe cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_pipe_cfg_set_name(cfg, "CT_PIPE");
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_type(cfg, DOCA_FLOW_PIPE_CT);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_ct_connections(cfg, MAX_IPV4_SESSIONS, MAX_IPV6_SESSIONS, 0);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT connections: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    /* CT HIT → post-CT forwarding pipe */
    fwd.type = DOCA_FLOW_FWD_PIPE;
    fwd.next_pipe = post_ct_pipe;

    /* CT MISS → RSS to ARM for policy eval */
    fwd_miss.type = DOCA_FLOW_FWD_PIPE;
    fwd_miss.next_pipe = rss_pipe;

    result = doca_flow_pipe_create(cfg, &fwd, &fwd_miss, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create CT pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    DOCA_LOG_INFO("CT pipe created: HIT->forward, MISS->RSS to ARM");
    return DOCA_SUCCESS;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Create root control pipe that steers IPv4 TCP/UDP into CT pipe.
 */
static doca_error_t create_root_pipe(struct doca_flow_port *port,
                                     struct doca_flow_pipe *ct_pipe,
                                     struct doca_flow_pipe **pipe)
{
    struct doca_flow_pipe_cfg *cfg;
    struct doca_flow_match match;
    struct doca_flow_match mask;
    struct doca_flow_fwd fwd;
    doca_error_t result;

    memset(&match, 0, sizeof(match));
    memset(&mask, 0, sizeof(mask));
    memset(&fwd, 0, sizeof(fwd));

    result = doca_flow_pipe_cfg_create(&cfg, port);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create root pipe cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_pipe_cfg_set_name(cfg, "ROOT_CONTROL_PIPE");
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_type(cfg, DOCA_FLOW_PIPE_CONTROL);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_cfg_set_is_root(cfg, true);
    if (result != DOCA_SUCCESS)
        goto destroy_cfg;

    result = doca_flow_pipe_create(cfg, NULL, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create root pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Entry: Match IPv4 TCP → CT pipe (priority 1) */
    struct doca_flow_pipe_entry *entry = NULL;

    memset(&match, 0, sizeof(match));
    memset(&mask, 0, sizeof(mask));
    match.outer.l3_type = DOCA_FLOW_L3_TYPE_IP4;
    mask.outer.l3_type = DOCA_FLOW_L3_TYPE_IP4;
    match.outer.l4_type_ext = DOCA_FLOW_L4_TYPE_EXT_TCP;
    mask.outer.l4_type_ext = DOCA_FLOW_L4_TYPE_EXT_TCP;

    fwd.type = DOCA_FLOW_FWD_PIPE;
    fwd.next_pipe = ct_pipe;

    result = doca_flow_pipe_control_add_entry(0, *pipe, &match, &mask,
                                              NULL, NULL, NULL, NULL,
                                              NULL, 1, &fwd, NULL, &entry);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add root TCP entry: %s", doca_error_get_descr(result));
        return result;
    }

    /* Entry: Match IPv4 UDP → CT pipe (priority 2) */
    memset(&match, 0, sizeof(match));
    memset(&mask, 0, sizeof(mask));
    match.outer.l3_type = DOCA_FLOW_L3_TYPE_IP4;
    mask.outer.l3_type = DOCA_FLOW_L3_TYPE_IP4;
    match.outer.l4_type_ext = DOCA_FLOW_L4_TYPE_EXT_UDP;
    mask.outer.l4_type_ext = DOCA_FLOW_L4_TYPE_EXT_UDP;

    result = doca_flow_pipe_control_add_entry(0, *pipe, &match, &mask,
                                              NULL, NULL, NULL, NULL,
                                              NULL, 2, &fwd, NULL, &entry);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add root UDP entry: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_entries_process(port, 0, DEFAULT_TIMEOUT_US, 0);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to process root entries: %s", doca_error_get_descr(result));
    }

    DOCA_LOG_INFO("Root control pipe created with TCP+UDP -> CT steering");
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Initialize DOCA Flow global configuration.
 */
static doca_error_t init_doca_flow(uint16_t nb_queues)
{
    struct doca_flow_cfg *cfg;
    doca_error_t result;

    result = doca_flow_cfg_create(&cfg);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create flow cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_cfg_set_pipe_queues(cfg, nb_queues);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set pipe queues: %s", doca_error_get_descr(result));
        goto destroy;
    }

    /* Switch mode with HWS (hardware steering) */
    result = doca_flow_cfg_set_mode_args(cfg, "switch,hws,expert");
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set mode args: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_cfg_set_nr_counters(cfg, 1024);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set nr_counters: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_cfg_set_cb_entry_process(cfg, entry_process_cb);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set entry callback: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_init(cfg);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DOCA Flow: %s", doca_error_get_descr(result));
        goto destroy;
    }

    doca_flow_cfg_destroy(cfg);
    DOCA_LOG_INFO("DOCA Flow initialized in switch mode (HWS)");
    return DOCA_SUCCESS;

destroy:
    doca_flow_cfg_destroy(cfg);
    return result;
}

/*
 * Initialize DOCA Flow CT subsystem.
 */
static doca_error_t init_doca_flow_ct(uint16_t nb_queues)
{
    struct doca_flow_ct_cfg *cfg;
    struct doca_flow_meta zone_mask;
    struct doca_flow_ct_meta modify_mask;
    doca_error_t result;

    result = doca_flow_ct_cfg_create(&cfg);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create CT cfg: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_ct_cfg_set_flags(cfg, DOCA_FLOW_CT_FLAG_NO_AGING);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT flags: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_ct_cfg_set_queues(cfg, nb_queues);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT queues: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_ct_cfg_set_ctrl_queues(cfg, 1);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT ctrl queues: %s", doca_error_get_descr(result));
        goto destroy;
    }

    /* No zone masking */
    memset(&zone_mask, 0, sizeof(zone_mask));
    memset(&modify_mask, 0, sizeof(modify_mask));

    result = doca_flow_ct_cfg_set_direction(cfg, false, false, &zone_mask, &modify_mask);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT origin direction: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_ct_cfg_set_direction(cfg, true, false, &zone_mask, &modify_mask);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT reply direction: %s", doca_error_get_descr(result));
        goto destroy;
    }

    result = doca_flow_ct_init(cfg);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DOCA Flow CT: %s", doca_error_get_descr(result));
        goto destroy;
    }

    doca_flow_ct_cfg_destroy(cfg);
    DOCA_LOG_INFO("DOCA Flow CT initialized (no aging, %u queues)", nb_queues);
    return DOCA_SUCCESS;

destroy:
    doca_flow_ct_cfg_destroy(cfg);
    return result;
}

/*
 * Start ports in switch mode.
 */
static doca_error_t start_ports(struct fw_flow_ctx *ctx)
{
    doca_error_t result;

    for (int i = 0; i < NUM_PORTS; i++) {
        struct doca_flow_port_cfg *port_cfg;

        result = doca_flow_port_cfg_create(&port_cfg);
        if (result != DOCA_SUCCESS) {
            DOCA_LOG_ERR("Failed to create port cfg for port %d: %s",
                         i, doca_error_get_descr(result));
            return result;
        }

        result = doca_flow_port_cfg_set_devargs(port_cfg, port_devargs[i]);
        if (result != DOCA_SUCCESS) {
            DOCA_LOG_ERR("Failed to set port %d devargs: %s",
                         i, doca_error_get_descr(result));
            doca_flow_port_cfg_destroy(port_cfg);
            return result;
        }

        result = doca_flow_port_start(port_cfg, &ctx->ports[i]);
        if (result != DOCA_SUCCESS) {
            DOCA_LOG_ERR("Failed to start port %d (%s): %s",
                         i, port_devargs[i], doca_error_get_descr(result));
            doca_flow_port_cfg_destroy(port_cfg);
            return result;
        }

        doca_flow_port_cfg_destroy(port_cfg);
        DOCA_LOG_INFO("Port %d started: %s", i, port_devargs[i]);
    }

    /* Get the switch port (port 0 is the switch manager in switch mode) */
    ctx->switch_port = doca_flow_port_switch_get(ctx->ports[0]);
    if (ctx->switch_port == NULL) {
        DOCA_LOG_ERR("Failed to get switch port");
        return DOCA_ERROR_INITIALIZATION;
    }

    DOCA_LOG_INFO("All %d ports started, switch port acquired", NUM_PORTS);
    return DOCA_SUCCESS;
}

doca_error_t fw_flow_init(struct fw_flow_ctx *ctx)
{
    doca_error_t result;

    memset(ctx, 0, sizeof(*ctx));
    ctx->nb_queues = NB_QUEUES;

    /* 1. Initialize DOCA Flow */
    result = init_doca_flow(ctx->nb_queues);
    if (result != DOCA_SUCCESS)
        return result;

    /* 2. Initialize DOCA Flow CT */
    result = init_doca_flow_ct(ctx->nb_queues);
    if (result != DOCA_SUCCESS) {
        doca_flow_destroy();
        return result;
    }

    /* 3. Start all ports */
    result = start_ports(ctx);
    if (result != DOCA_SUCCESS) {
        doca_flow_ct_destroy();
        doca_flow_destroy();
        return result;
    }

    /* 4. Create pipe chain on the switch port:
     *    ROOT (control) → CT pipe → post-CT forwarding
     *    CT MISS → RSS to ARM
     */

    /* Create RSS pipe first (CT MISS target) */
    result = create_rss_pipe(ctx->switch_port, &ctx->rss_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create post-CT forwarding pipe (CT HIT target) */
    result = create_post_ct_pipe(ctx->switch_port, &ctx->post_ct_fwd_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create CT pipe */
    result = create_ct_pipe(ctx->switch_port, ctx->post_ct_fwd_pipe,
                            ctx->rss_pipe, &ctx->ct_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create root control pipe steering into CT */
    result = create_root_pipe(ctx->switch_port, ctx->ct_pipe, &ctx->root_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    DOCA_LOG_INFO("Flow pipeline initialized: ROOT -> CT -> [HIT: forward | MISS: RSS to ARM]");
    return DOCA_SUCCESS;

cleanup:
    fw_flow_destroy(ctx);
    return result;
}

void fw_flow_destroy(struct fw_flow_ctx *ctx)
{
    DOCA_LOG_INFO("Destroying flow resources...");

    for (int i = 0; i < NUM_PORTS; i++) {
        if (ctx->ports[i]) {
            doca_flow_port_stop(ctx->ports[i]);
            ctx->ports[i] = NULL;
        }
    }

    doca_flow_ct_destroy();
    doca_flow_destroy();

    DOCA_LOG_INFO("Flow resources destroyed");
}
