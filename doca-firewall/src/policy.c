/*
 * DOCA Flow CT Firewall - Policy Engine Implementation
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <arpa/inet.h>

#include <doca_log.h>

#include "policy.h"

DOCA_LOG_REGISTER(FW_POLICY);

static uint32_t next_rule_id = 1;

void policy_init(struct policy_table *table)
{
    memset(table, 0, sizeof(*table));
    pthread_rwlock_init(&table->lock, NULL);

    /* Default rule: deny all (lowest priority = highest number) */
    struct policy_rule default_deny = {
        .id = 0,
        .src_ip = 0,
        .src_ip_mask = 0,
        .dst_ip = 0,
        .dst_ip_mask = 0,
        .src_port_min = 0,
        .src_port_max = 65535,
        .dst_port_min = 0,
        .dst_port_max = 65535,
        .protocol = 0,
        .action = POLICY_ACTION_DENY,
        .priority = UINT32_MAX,
        .enabled = true,
        .hit_count = 0,
    };
    table->rules[0] = default_deny;
    table->nr_rules = 1;

    DOCA_LOG_INFO("Policy table initialized with default-deny rule");
}

void policy_destroy(struct policy_table *table)
{
    pthread_rwlock_destroy(&table->lock);
}

int policy_add_rule(struct policy_table *table, const struct policy_rule *rule)
{
    pthread_rwlock_wrlock(&table->lock);

    if (table->nr_rules >= MAX_POLICY_RULES) {
        pthread_rwlock_unlock(&table->lock);
        DOCA_LOG_ERR("Policy table full (%u rules)", MAX_POLICY_RULES);
        return -1;
    }

    uint32_t id = next_rule_id++;
    uint32_t idx = table->nr_rules;
    table->rules[idx] = *rule;
    table->rules[idx].id = id;
    table->rules[idx].hit_count = 0;
    table->nr_rules++;

    pthread_rwlock_unlock(&table->lock);

    DOCA_LOG_INFO("Added policy rule id=%u, action=%s, priority=%u",
                  id, rule->action == POLICY_ACTION_ALLOW ? "ALLOW" : "DENY",
                  rule->priority);
    return (int)id;
}

int policy_remove_rule(struct policy_table *table, uint32_t rule_id)
{
    pthread_rwlock_wrlock(&table->lock);

    for (uint32_t i = 0; i < table->nr_rules; i++) {
        if (table->rules[i].id == rule_id) {
            /* Don't allow removing the default deny rule (id=0) */
            if (rule_id == 0) {
                pthread_rwlock_unlock(&table->lock);
                DOCA_LOG_WARN("Cannot remove default deny rule");
                return -1;
            }
            /* Shift remaining rules down */
            for (uint32_t j = i; j < table->nr_rules - 1; j++) {
                table->rules[j] = table->rules[j + 1];
            }
            table->nr_rules--;
            pthread_rwlock_unlock(&table->lock);
            DOCA_LOG_INFO("Removed policy rule id=%u", rule_id);
            return 0;
        }
    }

    pthread_rwlock_unlock(&table->lock);
    DOCA_LOG_WARN("Rule id=%u not found", rule_id);
    return -1;
}

policy_action_t policy_evaluate(struct policy_table *table,
                                uint32_t src_ip, uint32_t dst_ip,
                                uint16_t src_port, uint16_t dst_port,
                                uint8_t protocol)
{
    policy_action_t result = POLICY_ACTION_DENY;
    uint32_t best_priority = UINT32_MAX;

    pthread_rwlock_rdlock(&table->lock);

    for (uint32_t i = 0; i < table->nr_rules; i++) {
        struct policy_rule *r = &table->rules[i];

        if (!r->enabled)
            continue;

        /* Check protocol */
        if (r->protocol != 0 && r->protocol != protocol)
            continue;

        /* Check source IP (network byte order comparison with mask) */
        if (r->src_ip != 0) {
            if ((src_ip & r->src_ip_mask) != (r->src_ip & r->src_ip_mask))
                continue;
        }

        /* Check destination IP */
        if (r->dst_ip != 0) {
            if ((dst_ip & r->dst_ip_mask) != (r->dst_ip & r->dst_ip_mask))
                continue;
        }

        /* Check source port range (host byte order) */
        if (r->src_port_min != 0 || r->src_port_max != 65535) {
            if (src_port < r->src_port_min || src_port > r->src_port_max)
                continue;
        }

        /* Check destination port range (host byte order) */
        if (r->dst_port_min != 0 || r->dst_port_max != 65535) {
            if (dst_port < r->dst_port_min || dst_port > r->dst_port_max)
                continue;
        }

        /* This rule matches; check priority */
        if (r->priority < best_priority) {
            best_priority = r->priority;
            result = r->action;
            /* Increment hit count (non-atomic, best effort under rdlock) */
            r->hit_count++;
        }
    }

    pthread_rwlock_unlock(&table->lock);
    return result;
}

char *policy_to_json(struct policy_table *table)
{
    /* Allocate a generous buffer */
    size_t buf_sz = 256 * (table->nr_rules + 1);
    char *buf = malloc(buf_sz);
    if (!buf)
        return NULL;

    int off = 0;
    off += snprintf(buf + off, buf_sz - off, "{ \"rules\": [\n");

    pthread_rwlock_rdlock(&table->lock);

    for (uint32_t i = 0; i < table->nr_rules; i++) {
        struct policy_rule *r = &table->rules[i];
        char src_str[INET_ADDRSTRLEN], dst_str[INET_ADDRSTRLEN];

        inet_ntop(AF_INET, &r->src_ip, src_str, sizeof(src_str));
        inet_ntop(AF_INET, &r->dst_ip, dst_str, sizeof(dst_str));

        off += snprintf(buf + off, buf_sz - off,
            "  { \"id\": %u, \"src_ip\": \"%s\", \"dst_ip\": \"%s\", "
            "\"src_port_min\": %u, \"src_port_max\": %u, "
            "\"dst_port_min\": %u, \"dst_port_max\": %u, "
            "\"protocol\": %u, \"action\": \"%s\", "
            "\"priority\": %u, \"enabled\": %s, \"hit_count\": %lu }%s\n",
            r->id, src_str, dst_str,
            r->src_port_min, r->src_port_max,
            r->dst_port_min, r->dst_port_max,
            r->protocol,
            r->action == POLICY_ACTION_ALLOW ? "ALLOW" : "DENY",
            r->priority,
            r->enabled ? "true" : "false",
            (unsigned long)r->hit_count,
            (i < table->nr_rules - 1) ? "," : "");
    }

    pthread_rwlock_unlock(&table->lock);

    off += snprintf(buf + off, buf_sz - off, "] }");
    return buf;
}
