/*
 * DOCA Flow CT Firewall - Flow Initialization
 * Pipe creation for switch-mode CT firewall.
 * Port initialization is handled by the DOCA sample framework.
 */

#ifndef FLOW_INIT_H
#define FLOW_INIT_H

#include <stdint.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>
#include <doca_error.h>

#include <flow_switch_common.h>
#include <flow_ct_common.h>
#include <flow_common.h>

#define NB_QUEUES          2
#define CT_QUEUE           NB_QUEUES  /* CT queue offset after regular queues */
#define MAX_IPV4_SESSIONS  8192
#define MAX_IPV6_SESSIONS  0
#define CT_AGING_TIMEOUT_S 300        /* 5 minutes idle timeout */

/* Number of ports: 1 switch port + representors discovered by framework */
#define MAX_PORTS          8

/* Global pipe and port handles */
struct fw_flow_ctx {
    struct doca_flow_port *ports[MAX_PORTS];
    int nb_ports;

    /* Pipes */
    struct doca_flow_pipe *root_pipe;
    struct doca_flow_pipe *ct_pipe;
    struct doca_flow_pipe *post_ct_fwd_pipe;
    struct doca_flow_pipe *rss_pipe;

    uint16_t nb_queues;
};

/*
 * Initialize DOCA Flow pipes (CT pipeline).
 * Ports are already initialized by the framework (init_doca_flow_switch_ports).
 * This function creates the pipe chain: ROOT -> CT -> [HIT: forward | MISS: RSS]
 *
 * @ctx [out]: firewall flow context to fill
 * @switch_ctx [in]: framework switch context with device/port info
 * @return: DOCA_SUCCESS on success and DOCA_ERROR otherwise
 */
doca_error_t fw_flow_init(struct fw_flow_ctx *ctx, struct flow_switch_ctx *switch_ctx);

/*
 * Destroy all flow resources.
 */
void fw_flow_destroy(struct fw_flow_ctx *ctx);

#endif /* FLOW_INIT_H */
