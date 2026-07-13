/*
 * DOCA Flow CT Firewall - REST API Implementation
 * Simple embedded HTTP server for rule CRUD and metrics on port 8443.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <pthread.h>

#include <doca_log.h>

#include "rest_api.h"
#include "policy.h"
#include "metrics.h"

DOCA_LOG_REGISTER(FW_REST_API);

#define MAX_REQUEST_SIZE 4096
#define MAX_RESPONSE_SIZE 65536

/* Simple HTTP response helpers */
static void send_response(int client_fd, int status_code, const char *status_text,
                          const char *content_type, const char *body)
{
    char header[512];
    int body_len = body ? (int)strlen(body) : 0;

    snprintf(header, sizeof(header),
             "HTTP/1.1 %d %s\r\n"
             "Content-Type: %s\r\n"
             "Content-Length: %d\r\n"
             "Connection: close\r\n"
             "Access-Control-Allow-Origin: *\r\n"
             "\r\n",
             status_code, status_text, content_type, body_len);

    send(client_fd, header, strlen(header), 0);
    if (body && body_len > 0) {
        send(client_fd, body, body_len, 0);
    }
}

static void send_json(int client_fd, int status_code, const char *body)
{
    const char *status_text = (status_code == 200) ? "OK" :
                              (status_code == 201) ? "Created" :
                              (status_code == 400) ? "Bad Request" :
                              (status_code == 404) ? "Not Found" :
                              (status_code == 405) ? "Method Not Allowed" :
                              "Internal Server Error";
    send_response(client_fd, status_code, status_text, "application/json", body);
}

/*
 * Parse a simple JSON rule from request body.
 * Expects format:
 * {
 *   "src_ip": "10.0.0.0/24",
 *   "dst_ip": "0.0.0.0/0",
 *   "src_port_min": 0, "src_port_max": 65535,
 *   "dst_port_min": 80, "dst_port_max": 80,
 *   "protocol": 6,
 *   "action": "ALLOW",
 *   "priority": 100
 * }
 */
static int parse_rule_json(const char *body, struct policy_rule *rule)
{
    char src_ip_str[64] = "0.0.0.0/0";
    char dst_ip_str[64] = "0.0.0.0/0";
    char action_str[16] = "DENY";
    const char *p;

    memset(rule, 0, sizeof(*rule));
    rule->enabled = true;
    rule->src_port_max = 65535;
    rule->dst_port_max = 65535;
    rule->priority = 100;

    /* Very simple JSON parsing (no external deps) */
    p = strstr(body, "\"src_ip\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p++;
            while (*p == ' ' || *p == '"') p++;
            sscanf(p, "%63[^\"]", src_ip_str);
        }
    }

    p = strstr(body, "\"dst_ip\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p++;
            while (*p == ' ' || *p == '"') p++;
            sscanf(p, "%63[^\"]", dst_ip_str);
        }
    }

    p = strstr(body, "\"src_port_min\"");
    if (p) {
        p = strchr(p, ':');
        if (p) sscanf(p + 1, " %hu", &rule->src_port_min);
    }

    p = strstr(body, "\"src_port_max\"");
    if (p) {
        p = strchr(p, ':');
        if (p) sscanf(p + 1, " %hu", &rule->src_port_max);
    }

    p = strstr(body, "\"dst_port_min\"");
    if (p) {
        p = strchr(p, ':');
        if (p) sscanf(p + 1, " %hu", &rule->dst_port_min);
    }

    p = strstr(body, "\"dst_port_max\"");
    if (p) {
        p = strchr(p, ':');
        if (p) sscanf(p + 1, " %hu", &rule->dst_port_max);
    }

    p = strstr(body, "\"protocol\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            int proto;
            sscanf(p + 1, " %d", &proto);
            rule->protocol = (uint8_t)proto;
        }
    }

    p = strstr(body, "\"priority\"");
    if (p) {
        p = strchr(p, ':');
        if (p) sscanf(p + 1, " %u", &rule->priority);
    }

    p = strstr(body, "\"action\"");
    if (p) {
        p = strchr(p, ':');
        if (p) {
            p++;
            while (*p == ' ' || *p == '"') p++;
            sscanf(p, "%15[^\"]", action_str);
        }
    }

    /* Parse src_ip with CIDR */
    char *slash = strchr(src_ip_str, '/');
    if (slash) {
        *slash = '\0';
        int prefix = atoi(slash + 1);
        if (prefix > 0 && prefix <= 32)
            rule->src_ip_mask = htonl(0xFFFFFFFF << (32 - prefix));
        else
            rule->src_ip_mask = 0;
    } else {
        rule->src_ip_mask = 0xFFFFFFFF;
    }
    inet_pton(AF_INET, src_ip_str, &rule->src_ip);

    /* Parse dst_ip with CIDR */
    slash = strchr(dst_ip_str, '/');
    if (slash) {
        *slash = '\0';
        int prefix = atoi(slash + 1);
        if (prefix > 0 && prefix <= 32)
            rule->dst_ip_mask = htonl(0xFFFFFFFF << (32 - prefix));
        else
            rule->dst_ip_mask = 0;
    } else {
        rule->dst_ip_mask = 0xFFFFFFFF;
    }
    inet_pton(AF_INET, dst_ip_str, &rule->dst_ip);

    /* Parse action */
    if (strcasecmp(action_str, "ALLOW") == 0 || strcasecmp(action_str, "allow") == 0)
        rule->action = POLICY_ACTION_ALLOW;
    else
        rule->action = POLICY_ACTION_DENY;

    return 0;
}

/*
 * Handle a single HTTP request.
 */
static void handle_request(int client_fd, struct rest_api_ctx *ctx)
{
    char request[MAX_REQUEST_SIZE];
    ssize_t n = recv(client_fd, request, sizeof(request) - 1, 0);
    if (n <= 0) {
        close(client_fd);
        return;
    }
    request[n] = '\0';

    /* Parse method and path */
    char method[16], path[256];
    sscanf(request, "%15s %255s", method, path);

    /* Find body (after \r\n\r\n) */
    char *body = strstr(request, "\r\n\r\n");
    if (body) body += 4;

    /* Route: GET /metrics */
    if (strcmp(method, "GET") == 0 && strcmp(path, "/metrics") == 0) {
        char *json = metrics_to_json(ctx->metrics);
        if (json) {
            send_json(client_fd, 200, json);
            free(json);
        } else {
            send_json(client_fd, 500, "{\"error\": \"out of memory\"}");
        }
    }
    /* Route: GET /rules */
    else if (strcmp(method, "GET") == 0 && strcmp(path, "/rules") == 0) {
        char *json = policy_to_json(ctx->policy);
        if (json) {
            send_json(client_fd, 200, json);
            free(json);
        } else {
            send_json(client_fd, 500, "{\"error\": \"out of memory\"}");
        }
    }
    /* Route: POST /rules */
    else if (strcmp(method, "POST") == 0 && strcmp(path, "/rules") == 0) {
        if (!body || strlen(body) == 0) {
            send_json(client_fd, 400, "{\"error\": \"empty body\"}");
        } else {
            struct policy_rule rule;
            if (parse_rule_json(body, &rule) != 0) {
                send_json(client_fd, 400, "{\"error\": \"invalid JSON\"}");
            } else {
                int id = policy_add_rule(ctx->policy, &rule);
                if (id < 0) {
                    send_json(client_fd, 500, "{\"error\": \"table full\"}");
                } else {
                    char resp[128];
                    snprintf(resp, sizeof(resp), "{\"id\": %d, \"status\": \"created\"}", id);
                    send_json(client_fd, 201, resp);
                }
            }
        }
    }
    /* Route: DELETE /rules/<id> */
    else if (strcmp(method, "DELETE") == 0 && strncmp(path, "/rules/", 7) == 0) {
        uint32_t rule_id = (uint32_t)atoi(path + 7);
        if (policy_remove_rule(ctx->policy, rule_id) == 0) {
            send_json(client_fd, 200, "{\"status\": \"deleted\"}");
        } else {
            send_json(client_fd, 404, "{\"error\": \"rule not found\"}");
        }
    }
    /* Route: GET /health */
    else if (strcmp(method, "GET") == 0 && strcmp(path, "/health") == 0) {
        send_json(client_fd, 200, "{\"status\": \"ok\"}");
    }
    /* Unknown route */
    else {
        send_json(client_fd, 404, "{\"error\": \"not found\"}");
    }

    close(client_fd);
}

/*
 * REST API server thread.
 */
static void *rest_api_thread(void *arg)
{
    struct rest_api_ctx *ctx = (struct rest_api_ctx *)arg;
    struct sockaddr_in addr;
    int opt = 1;

    ctx->server_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (ctx->server_fd < 0) {
        DOCA_LOG_ERR("Failed to create REST API socket: %s", strerror(errno));
        return NULL;
    }

    setsockopt(ctx->server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(REST_API_PORT);

    if (bind(ctx->server_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        DOCA_LOG_ERR("Failed to bind REST API port %d: %s",
                     REST_API_PORT, strerror(errno));
        close(ctx->server_fd);
        return NULL;
    }

    if (listen(ctx->server_fd, 16) < 0) {
        DOCA_LOG_ERR("Failed to listen on REST API port: %s", strerror(errno));
        close(ctx->server_fd);
        return NULL;
    }

    DOCA_LOG_INFO("REST API server listening on port %d", REST_API_PORT);

    while (ctx->running) {
        struct sockaddr_in client_addr;
        socklen_t client_len = sizeof(client_addr);

        /* Use a timeout so we can check ctx->running */
        struct timeval tv = {.tv_sec = 1, .tv_usec = 0};
        setsockopt(ctx->server_fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

        int client_fd = accept(ctx->server_fd, (struct sockaddr *)&client_addr, &client_len);
        if (client_fd < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK)
                continue;  /* Timeout, check running flag */
            if (ctx->running)
                DOCA_LOG_WARN("Accept failed: %s", strerror(errno));
            continue;
        }

        handle_request(client_fd, ctx);
    }

    close(ctx->server_fd);
    DOCA_LOG_INFO("REST API server stopped");
    return NULL;
}

int rest_api_start(struct rest_api_ctx *ctx,
                   struct policy_table *policy,
                   struct fw_metrics *metrics)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->policy = policy;
    ctx->metrics = metrics;
    ctx->running = true;

    if (pthread_create(&ctx->thread, NULL, rest_api_thread, ctx) != 0) {
        DOCA_LOG_ERR("Failed to create REST API thread");
        return -1;
    }

    DOCA_LOG_INFO("REST API thread started");
    return 0;
}

void rest_api_stop(struct rest_api_ctx *ctx)
{
    ctx->running = false;

    /* Close server socket to unblock accept() */
    if (ctx->server_fd > 0) {
        shutdown(ctx->server_fd, SHUT_RDWR);
    }

    pthread_join(ctx->thread, NULL);
    DOCA_LOG_INFO("REST API thread joined");
}
