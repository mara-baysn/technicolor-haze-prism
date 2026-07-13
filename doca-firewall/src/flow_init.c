/*
 * DOCA Flow CT Firewall - Flow Initialization
 *
 * Creates the DOCA Flow pipe chain for the CT firewall.
 * Port initialization is handled by the DOCA sample framework
 * (init_doca_flow_switch_ports via flow_switch_common).
 *
 * Pipeline: ROOT (control) -> CT pipe -> [HIT: post-CT fwd | MISS: RSS to ARM]
 */

#include <string.h>
#include <stdlib.h>

#include <doca_log.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>

#include <rte_ethdev.h>

#include <flow_common.h>
#include <flow_switch_common.h>
#include <flow_ct_common.h>
#include <dpdk_utils.h>

#include "flow_init.h"

DOCA_LOG_REGISTER(FW_FLOW_INIT);

/*
 * Create RSS pipe for sending CT MISS traffic to ARM cores.
 */
static doca_error_t create_rss_pipe(struct doca_flow_port *port,
                                    struct entries_status *status,
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

    result = set_flow_pipe_cfg(cfg, "RSS_MISS_PIPE", DOCA_FLOW_PIPE_BASIC, false);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set RSS pipe cfg: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set RSS pipe match: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    /* RSS to queue 0 - use only IPv4 hash (TCP/UDP cannot be combined) */
    fwd.type = DOCA_FLOW_FWD_RSS;
    fwd.rss_type = DOCA_FLOW_RESOURCE_TYPE_NON_SHARED;
    fwd.rss.queues_array = rss_queues;
    fwd.rss.nr_queues = 1;
    fwd.rss.outer_flags = DOCA_FLOW_RSS_IPV4 | DOCA_FLOW_RSS_TCP;

    result = doca_flow_pipe_create(cfg, &fwd, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create RSS pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Add wildcard entry */
    result = doca_flow_pipe_basic_add_entry(0, *pipe, &match, 0, NULL, NULL, &fwd, 0, status, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add RSS pipe entry: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_entries_process(port, 0, DEFAULT_TIMEOUT_US, 0);
    if (result != DOCA_SUCCESS)
        DOCA_LOG_ERR("Failed to process RSS entry: %s", doca_error_get_descr(result));

    DOCA_LOG_INFO("RSS pipe created successfully");
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Create post-CT forwarding pipe.
 * CT HIT traffic goes here. Forward to port 1 (first representor) by default.
 */
static doca_error_t create_post_ct_pipe(struct doca_flow_port *port,
                                        int fwd_port_id,
                                        struct entries_status *status,
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

    result = set_flow_pipe_cfg(cfg, "POST_CT_FWD_PIPE", DOCA_FLOW_PIPE_BASIC, false);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set post-CT pipe cfg: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set post-CT pipe match: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    /* Forward CT HIT traffic to the representor port */
    fwd.type = DOCA_FLOW_FWD_PORT;
    fwd.port_id = fwd_port_id;

    result = doca_flow_pipe_create(cfg, &fwd, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create post-CT pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Add wildcard entry */
    result = doca_flow_pipe_basic_add_entry(0, *pipe, &match, 0, NULL, NULL, &fwd, 0, status, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to add post-CT pipe entry: %s", doca_error_get_descr(result));
        return result;
    }

    result = doca_flow_entries_process(port, 0, DEFAULT_TIMEOUT_US, 0);
    if (result != DOCA_SUCCESS)
        DOCA_LOG_ERR("Failed to process post-CT entry: %s", doca_error_get_descr(result));

    DOCA_LOG_INFO("Post-CT forwarding pipe created (fwd to port %d)", fwd_port_id);
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

/*
 * Create the CT pipe.
 * HIT -> post_ct_pipe (forwarding)
 * MISS -> rss_pipe (to ARM for policy evaluation)
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

    result = set_flow_pipe_cfg(cfg, "CT_PIPE", DOCA_FLOW_PIPE_CT, false);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT pipe cfg: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    result = doca_flow_pipe_cfg_set_ct_connections(cfg, MAX_IPV4_SESSIONS, MAX_IPV6_SESSIONS, 0);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT connections: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    result = doca_flow_pipe_cfg_set_match(cfg, &match, NULL);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set CT pipe match: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    /* CT HIT -> post-CT forwarding pipe */
    fwd.type = DOCA_FLOW_FWD_PIPE;
    fwd.next_pipe = post_ct_pipe;

    /* CT MISS -> RSS to ARM for policy eval */
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
                                     struct entries_status *status,
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

    result = set_flow_pipe_cfg(cfg, "ROOT_CONTROL_PIPE", DOCA_FLOW_PIPE_CONTROL, true);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to set root pipe cfg: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }

    result = doca_flow_pipe_create(cfg, NULL, NULL, pipe);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to create root pipe: %s", doca_error_get_descr(result));
        goto destroy_cfg;
    }
    doca_flow_pipe_cfg_destroy(cfg);

    /* Entry: Match IPv4 TCP -> CT pipe (priority 1) */
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

    /* Entry: Match IPv4 UDP -> CT pipe (priority 2) */
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
    if (result != DOCA_SUCCESS)
        DOCA_LOG_ERR("Failed to process root entries: %s", doca_error_get_descr(result));

    DOCA_LOG_INFO("Root control pipe created: IPv4 TCP+UDP -> CT");
    return result;

destroy_cfg:
    doca_flow_pipe_cfg_destroy(cfg);
    return result;
}

doca_error_t fw_flow_init(struct fw_flow_ctx *ctx, struct flow_switch_ctx *switch_ctx)
{
    doca_error_t result;
    const int nb_entries = 4;  /* RSS + post-CT + 2 root entries */
    struct flow_resources resource;
    uint32_t nr_shared_resources[SHARED_RESOURCE_NUM_VALUES] = {0};
    struct doca_flow_meta o_zone_mask, r_zone_mask;
    struct doca_flow_ct_meta o_modify_mask, r_modify_mask;
    struct entries_status ctrl_status;
    uint32_t ct_flags;
    uint32_t nb_arm_queues = 1, nb_ctrl_queues = 1, ct_actions_mem_size = 0;

    memset(ctx, 0, sizeof(*ctx));
    memset(&ctrl_status, 0, sizeof(ctrl_status));
    memset(&resource, 0, sizeof(resource));

    ctx->nb_queues = NB_QUEUES;

    /* Determine number of ports based on discovered devices */
    /* Start all ports: 1 (switch/PF) + N representors */
    int nb_reps = switch_ctx->devs_ctx.devs_manager[0].nb_reps;
    ctx->nb_ports = 1 + (nb_reps > 0 ? nb_reps : 0);
    DOCA_LOG_INFO("Configuring %d flow ports", ctx->nb_ports);

    /* Configure resources in port mode */
    resource.mode = DOCA_FLOW_RESOURCE_MODE_PORT;
    resource.nr_counters = 1024;
    resource.nr_ct_counters = MAX_IPV4_SESSIONS + MAX_IPV6_SESSIONS;
    resource.nr_rss = 1;

    /* 1. Initialize DOCA Flow library */
    result = init_doca_flow(ctx->nb_queues, "switch,hws,expert", &resource, nr_shared_resources);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DOCA Flow: %s", doca_error_get_descr(result));
        return result;
    }

    /* 2. Initialize DOCA Flow CT */
    memset(&o_zone_mask, 0, sizeof(o_zone_mask));
    memset(&o_modify_mask, 0, sizeof(o_modify_mask));
    memset(&r_zone_mask, 0, sizeof(r_zone_mask));
    memset(&r_modify_mask, 0, sizeof(r_modify_mask));

    ct_flags = DOCA_FLOW_CT_FLAG_NO_AGING;
    result = init_doca_flow_ct(ct_flags,
                               nb_arm_queues,
                               nb_ctrl_queues,
                               ct_actions_mem_size,
                               NULL,
                               false,
                               &o_zone_mask,
                               &o_modify_mask,
                               false,
                               &r_zone_mask,
                               &r_modify_mask);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init DOCA Flow CT: %s", doca_error_get_descr(result));
        doca_flow_destroy();
        return result;
    }

    /* 3. Start ports using framework (handles doca_dev / doca_dev_rep properly) */
    uint32_t actions_mem_size[MAX_PORTS];
    ARRAY_INIT(actions_mem_size, ACTIONS_MEM_SIZE(nb_entries));

    result = init_doca_flow_switch_ports(switch_ctx->devs_ctx.devs_manager,
                                         switch_ctx->devs_ctx.nb_devs,
                                         ctx->ports,
                                         ctx->nb_ports,
                                         actions_mem_size,
                                         &resource);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to init switch ports: %s", doca_error_get_descr(result));
        doca_flow_ct_destroy();
        doca_flow_destroy();
        return result;
    }

    DOCA_LOG_INFO("Switch ports started successfully");

    /* 4. Create pipe chain on port 0 (the switch port) */

    /* Create RSS pipe (CT MISS target) */
    result = create_rss_pipe(ctx->ports[0], &ctrl_status, &ctx->rss_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create post-CT forwarding pipe (CT HIT target)
     * Forward to port 4 (VF3 representor = client) for origin direction.
     * Port indices: 0=switch, 1=pf0vf0(inet), 2=pf0vf1, 3=pf0vf2, 4=pf0vf3(client) */
    int fwd_port_id = ctx->nb_ports > 4 ? 4 : 1;
    result = create_post_ct_pipe(ctx->ports[0], fwd_port_id, &ctrl_status, &ctx->post_ct_fwd_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create CT pipe */
    result = create_ct_pipe(ctx->ports[0], ctx->post_ct_fwd_pipe,
                            ctx->rss_pipe, &ctx->ct_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Create root control pipe steering into CT */
    result = create_root_pipe(ctx->ports[0], ctx->ct_pipe, &ctrl_status, &ctx->root_pipe);
    if (result != DOCA_SUCCESS)
        goto cleanup;

    /* Process all pending entries */
    result = doca_flow_entries_process(ctx->ports[0], 0, DEFAULT_TIMEOUT_US, nb_entries);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_ERR("Failed to process pipe entries: %s", doca_error_get_descr(result));
        goto cleanup;
    }

    DOCA_LOG_INFO("Flow pipeline initialized: ROOT -> CT -> [HIT: forward | MISS: RSS to ARM]");
    return DOCA_SUCCESS;

cleanup:
    fw_flow_destroy(ctx);
    return result;
}

void fw_flow_destroy(struct fw_flow_ctx *ctx)
{
    DOCA_LOG_INFO("Destroying flow resources...");

    /* Stop ports in reverse order (switch port last) */
    for (int i = ctx->nb_ports - 1; i >= 0; i--) {
        if (ctx->ports[i]) {
            doca_flow_port_stop(ctx->ports[i]);
            ctx->ports[i] = NULL;
        }
    }

    doca_flow_ct_destroy();
    doca_flow_destroy();

    DOCA_LOG_INFO("Flow resources destroyed");
}
