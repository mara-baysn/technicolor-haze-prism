/*
 * DOCA Flow CT Firewall - CT Offload Management
 * Manages connection tracking entries and hardware offload.
 */

#ifndef CT_OFFLOAD_H
#define CT_OFFLOAD_H

#include <stdint.h>
#include <stdbool.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>

#include "flow_init.h"
#include "policy.h"
#include "metrics.h"

/* CT offload context */
struct ct_offload_ctx {
    struct fw_flow_ctx *flow_ctx;
    struct policy_table *policy;
    struct fw_metrics *metrics;
    volatile bool running;
};

/*
 * Initialize the CT offload subsystem.
 */
doca_error_t ct_offload_init(struct ct_offload_ctx *ctx,
                             struct fw_flow_ctx *flow_ctx,
                             struct policy_table *policy,
                             struct fw_metrics *metrics);

/*
 * Process a packet received on the MISS path.
 * Parses the 5-tuple, evaluates policy, and either:
 *   - Offloads to CT (ALLOW) with bidirectional forwarding
 *   - Drops the packet (DENY)
 */
void ct_offload_process_packet(struct ct_offload_ctx *ctx,
                               struct rte_mbuf *pkt,
                               uint16_t port_id);

/*
 * Main packet processing loop (runs on main lcore).
 * Polls RSS queue for CT MISS packets and processes them.
 */
void ct_offload_main_loop(struct ct_offload_ctx *ctx);

/*
 * Signal the main loop to stop.
 */
void ct_offload_stop(struct ct_offload_ctx *ctx);

#endif /* CT_OFFLOAD_H */
