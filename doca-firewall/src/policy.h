/*
 * DOCA Flow CT Firewall - Policy Engine
 * 5-tuple ACL rule table for allow/deny decisions.
 */

#ifndef POLICY_H
#define POLICY_H

#include <stdint.h>
#include <stdbool.h>
#include <netinet/in.h>
#include <pthread.h>

#define MAX_POLICY_RULES 256

/* Policy actions */
typedef enum {
    POLICY_ACTION_ALLOW = 0,
    POLICY_ACTION_DENY,
} policy_action_t;

/* 5-tuple rule definition */
struct policy_rule {
    uint32_t id;
    uint32_t src_ip;        /* network byte order, 0 = any */
    uint32_t src_ip_mask;   /* prefix mask */
    uint32_t dst_ip;        /* network byte order, 0 = any */
    uint32_t dst_ip_mask;   /* prefix mask */
    uint16_t src_port_min;  /* host byte order, 0 = any */
    uint16_t src_port_max;
    uint16_t dst_port_min;  /* host byte order, 0 = any */
    uint16_t dst_port_max;
    uint8_t  protocol;      /* IPPROTO_TCP, IPPROTO_UDP, 0 = any */
    policy_action_t action;
    uint32_t priority;      /* lower = higher priority */
    bool     enabled;
    uint64_t hit_count;     /* packets matched */
};

/* Policy table */
struct policy_table {
    struct policy_rule rules[MAX_POLICY_RULES];
    uint32_t nr_rules;
    pthread_rwlock_t lock;
};

/*
 * Initialize the policy table with a default-deny rule.
 */
void policy_init(struct policy_table *table);

/*
 * Destroy the policy table.
 */
void policy_destroy(struct policy_table *table);

/*
 * Add a rule. Returns rule ID or -1 on error.
 */
int policy_add_rule(struct policy_table *table, const struct policy_rule *rule);

/*
 * Remove a rule by ID. Returns 0 on success, -1 if not found.
 */
int policy_remove_rule(struct policy_table *table, uint32_t rule_id);

/*
 * Evaluate a 5-tuple against the policy table.
 * Returns POLICY_ACTION_ALLOW or POLICY_ACTION_DENY.
 */
policy_action_t policy_evaluate(struct policy_table *table,
                                uint32_t src_ip, uint32_t dst_ip,
                                uint16_t src_port, uint16_t dst_port,
                                uint8_t protocol);

/*
 * Get a JSON representation of all rules (caller must free).
 */
char *policy_to_json(struct policy_table *table);

#endif /* POLICY_H */
