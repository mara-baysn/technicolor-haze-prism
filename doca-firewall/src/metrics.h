/*
 * DOCA Flow CT Firewall - Metrics
 * Counters and session statistics.
 */

#ifndef METRICS_H
#define METRICS_H

#include <stdint.h>
#include <stdatomic.h>

struct fw_metrics {
    atomic_uint_fast64_t pkts_received;      /* Total packets received on MISS path */
    atomic_uint_fast64_t pkts_allowed;       /* Packets that matched ALLOW rule */
    atomic_uint_fast64_t pkts_denied;        /* Packets that matched DENY rule */
    atomic_uint_fast64_t sessions_offloaded; /* CT entries created */
    atomic_uint_fast64_t sessions_aged;      /* CT entries removed by aging */
    atomic_uint_fast64_t sessions_active;    /* Current active sessions */
    atomic_uint_fast64_t ct_add_errors;      /* Failed CT add operations */
    atomic_uint_fast64_t rx_bursts;          /* Number of rx_burst calls */
};

/*
 * Initialize metrics (zero all counters).
 */
void metrics_init(struct fw_metrics *m);

/*
 * Get a JSON representation of current metrics (caller must free).
 */
char *metrics_to_json(const struct fw_metrics *m);

#endif /* METRICS_H */
