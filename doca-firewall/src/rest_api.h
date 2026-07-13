/*
 * DOCA Flow CT Firewall - REST API
 * Embedded HTTP server for rule CRUD and metrics.
 */

#ifndef REST_API_H
#define REST_API_H

#include <stdint.h>
#include <stdbool.h>
#include <pthread.h>

#include "policy.h"
#include "metrics.h"

#define REST_API_PORT 8443

struct rest_api_ctx {
    struct policy_table *policy;
    struct fw_metrics *metrics;
    pthread_t thread;
    volatile bool running;
    int server_fd;
};

/*
 * Start the REST API server in a background thread.
 */
int rest_api_start(struct rest_api_ctx *ctx,
                   struct policy_table *policy,
                   struct fw_metrics *metrics);

/*
 * Stop the REST API server and join the thread.
 */
void rest_api_stop(struct rest_api_ctx *ctx);

#endif /* REST_API_H */
