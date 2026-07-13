/*
 * DOCA Flow CT Firewall - Flow Initialization
 * Port initialization and pipe creation for switch-mode CT firewall.
 */

#ifndef FLOW_INIT_H
#define FLOW_INIT_H

#include <stdint.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>
#include <doca_error.h>

#define NUM_PORTS 5          /* uplink PF + 4 VF representors */
#define PORT_UPLINK 0        /* pf0hpf */
#define PORT_VF0    1        /* pf0vf0 - internet */
#define PORT_VF1    2        /* pf0vf1 - firewall in */
#define PORT_VF2    3        /* pf0vf2 - firewall out */
#define PORT_VF3    4        /* pf0vf3 - client */

#define NB_QUEUES          2
#define CT_QUEUE           NB_QUEUES  /* CT queue offset after regular queues */
#define MAX_IPV4_SESSIONS  8192
#define MAX_IPV6_SESSIONS  0
#define CT_AGING_TIMEOUT_S 300        /* 5 minutes idle timeout */
#define DEFAULT_TIMEOUT_US 10000

/* Global pipe and port handles */
struct fw_flow_ctx {
    struct doca_flow_port *ports[NUM_PORTS];
    struct doca_flow_port *switch_port;

    /* Pipes */
    struct doca_flow_pipe *root_pipe;
    struct doca_flow_pipe *ct_pipe;
    struct doca_flow_pipe *post_ct_fwd_pipe;
    struct doca_flow_pipe *rss_pipe;

    uint16_t nb_queues;
};

/*
 * Initialize DOCA Flow in switch mode.
 * Creates ports, pipes, and sets up the CT pipeline.
 */
doca_error_t fw_flow_init(struct fw_flow_ctx *ctx);

/*
 * Destroy all flow resources.
 */
void fw_flow_destroy(struct fw_flow_ctx *ctx);

#endif /* FLOW_INIT_H */
