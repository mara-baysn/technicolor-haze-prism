/*
 * DOCA Flow CT Firewall - Metrics Implementation
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#include "metrics.h"

void metrics_init(struct fw_metrics *m)
{
    atomic_store(&m->pkts_received, 0);
    atomic_store(&m->pkts_allowed, 0);
    atomic_store(&m->pkts_denied, 0);
    atomic_store(&m->sessions_offloaded, 0);
    atomic_store(&m->sessions_aged, 0);
    atomic_store(&m->sessions_active, 0);
    atomic_store(&m->ct_add_errors, 0);
    atomic_store(&m->rx_bursts, 0);
}

char *metrics_to_json(const struct fw_metrics *m)
{
    char *buf = malloc(1024);
    if (!buf)
        return NULL;

    snprintf(buf, 1024,
        "{\n"
        "  \"pkts_received\": %lu,\n"
        "  \"pkts_allowed\": %lu,\n"
        "  \"pkts_denied\": %lu,\n"
        "  \"sessions_offloaded\": %lu,\n"
        "  \"sessions_aged\": %lu,\n"
        "  \"sessions_active\": %lu,\n"
        "  \"ct_add_errors\": %lu,\n"
        "  \"rx_bursts\": %lu\n"
        "}",
        (unsigned long)atomic_load(&m->pkts_received),
        (unsigned long)atomic_load(&m->pkts_allowed),
        (unsigned long)atomic_load(&m->pkts_denied),
        (unsigned long)atomic_load(&m->sessions_offloaded),
        (unsigned long)atomic_load(&m->sessions_aged),
        (unsigned long)atomic_load(&m->sessions_active),
        (unsigned long)atomic_load(&m->ct_add_errors),
        (unsigned long)atomic_load(&m->rx_bursts));

    return buf;
}
