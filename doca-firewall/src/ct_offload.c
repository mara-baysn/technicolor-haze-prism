/*
 * DOCA Flow CT Firewall - CT Offload Implementation
 * Processes CT MISS packets: parse, evaluate policy, offload or drop.
 */

#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>

#include <rte_mbuf.h>
#include <rte_ether.h>
#include <rte_ip.h>
#include <rte_tcp.h>
#include <rte_udp.h>
#include <rte_ethdev.h>

#include <doca_log.h>
#include <doca_flow.h>
#include <doca_flow_ct.h>

#include "ct_offload.h"
#include "flow_init.h"
#include "policy.h"
#include "metrics.h"

DOCA_LOG_REGISTER(FW_CT_OFFLOAD);

#define PACKET_BURST 64

doca_error_t ct_offload_init(struct ct_offload_ctx *ctx,
                             struct fw_flow_ctx *flow_ctx,
                             struct policy_table *policy,
                             struct fw_metrics *metrics)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->flow_ctx = flow_ctx;
    ctx->policy = policy;
    ctx->metrics = metrics;
    ctx->running = true;

    DOCA_LOG_INFO("CT offload subsystem initialized");
    return DOCA_SUCCESS;
}

/*
 * Parse IPv4 packet to extract 5-tuple and fill CT match structures.
 */
static bool parse_ipv4_packet(struct rte_mbuf *pkt,
                              struct doca_flow_ct_match *match_o,
                              struct doca_flow_ct_match *match_r,
                              uint32_t *src_ip, uint32_t *dst_ip,
                              uint16_t *src_port, uint16_t *dst_port,
                              uint8_t *protocol)
{
    struct rte_ether_hdr *eth_hdr;
    struct rte_ipv4_hdr *ipv4_hdr;
    uint16_t ether_type;

    eth_hdr = rte_pktmbuf_mtod(pkt, struct rte_ether_hdr *);
    ether_type = rte_be_to_cpu_16(eth_hdr->ether_type);

    if (ether_type != RTE_ETHER_TYPE_IPV4)
        return false;

    ipv4_hdr = rte_pktmbuf_mtod_offset(pkt, struct rte_ipv4_hdr *,
                                        sizeof(struct rte_ether_hdr));

    *src_ip = ipv4_hdr->src_addr;
    *dst_ip = ipv4_hdr->dst_addr;
    *protocol = ipv4_hdr->next_proto_id;

    /* Fill CT match origin */
    memset(match_o, 0, sizeof(*match_o));
    memset(match_r, 0, sizeof(*match_r));

    match_o->ipv4.src_ip = ipv4_hdr->src_addr;
    match_o->ipv4.dst_ip = ipv4_hdr->dst_addr;
    match_o->ipv4.next_proto = ipv4_hdr->next_proto_id;

    /* Fill CT match reply (reversed) */
    match_r->ipv4.src_ip = ipv4_hdr->dst_addr;
    match_r->ipv4.dst_ip = ipv4_hdr->src_addr;
    match_r->ipv4.next_proto = ipv4_hdr->next_proto_id;

    /* Extract L4 ports */
    uint8_t *l4_hdr = (uint8_t *)ipv4_hdr + (ipv4_hdr->version_ihl & 0x0f) * 4;

    if (ipv4_hdr->next_proto_id == IPPROTO_TCP) {
        struct rte_tcp_hdr *tcp = (struct rte_tcp_hdr *)l4_hdr;
        *src_port = rte_be_to_cpu_16(tcp->src_port);
        *dst_port = rte_be_to_cpu_16(tcp->dst_port);
        match_o->ipv4.l4_port.src_port = tcp->src_port;
        match_o->ipv4.l4_port.dst_port = tcp->dst_port;
        match_r->ipv4.l4_port.src_port = tcp->dst_port;
        match_r->ipv4.l4_port.dst_port = tcp->src_port;
    } else if (ipv4_hdr->next_proto_id == IPPROTO_UDP) {
        struct rte_udp_hdr *udp = (struct rte_udp_hdr *)l4_hdr;
        *src_port = rte_be_to_cpu_16(udp->src_port);
        *dst_port = rte_be_to_cpu_16(udp->dst_port);
        match_o->ipv4.l4_port.src_port = udp->src_port;
        match_o->ipv4.l4_port.dst_port = udp->dst_port;
        match_r->ipv4.l4_port.src_port = udp->dst_port;
        match_r->ipv4.l4_port.dst_port = udp->src_port;
    } else {
        *src_port = 0;
        *dst_port = 0;
    }

    return true;
}

void ct_offload_process_packet(struct ct_offload_ctx *ctx,
                               struct rte_mbuf *pkt,
                               uint16_t port_id)
{
    struct doca_flow_ct_match match_o, match_r;
    uint32_t src_ip, dst_ip;
    uint16_t src_port, dst_port;
    uint8_t protocol;
    policy_action_t action;
    doca_error_t result;
    char src_str[INET_ADDRSTRLEN], dst_str[INET_ADDRSTRLEN];

    (void)port_id;

    atomic_fetch_add(&ctx->metrics->pkts_received, 1);

    /* Parse the packet */
    if (!parse_ipv4_packet(pkt, &match_o, &match_r,
                           &src_ip, &dst_ip, &src_port, &dst_port, &protocol)) {
        /* Not IPv4, drop */
        rte_pktmbuf_free(pkt);
        return;
    }

    /* Evaluate policy */
    action = policy_evaluate(ctx->policy, src_ip, dst_ip,
                             src_port, dst_port, protocol);

    if (action == POLICY_ACTION_DENY) {
        atomic_fetch_add(&ctx->metrics->pkts_denied, 1);
        inet_ntop(AF_INET, &src_ip, src_str, sizeof(src_str));
        inet_ntop(AF_INET, &dst_ip, dst_str, sizeof(dst_str));
        DOCA_LOG_DBG("DENY: %s:%u -> %s:%u proto=%u",
                     src_str, src_port, dst_str, dst_port, protocol);
        rte_pktmbuf_free(pkt);
        return;
    }

    /* ALLOW: offload to CT */
    atomic_fetch_add(&ctx->metrics->pkts_allowed, 1);

    uint32_t entry_flags = DOCA_FLOW_CT_ENTRY_FLAGS_NO_WAIT |
                           DOCA_FLOW_CT_ENTRY_FLAGS_DIR_ORIGIN |
                           DOCA_FLOW_CT_ENTRY_FLAGS_DIR_REPLY;

    /* Set per-direction forwarding:
     * Origin (VF0->VF3): forward to VF3 (port 4)
     * Reply (VF3->VF0): forward to VF0 (port 1)
     */
    struct doca_flow_fwd fwd_origin, fwd_reply;
    memset(&fwd_origin, 0, sizeof(fwd_origin));
    memset(&fwd_reply, 0, sizeof(fwd_reply));

    fwd_origin.type = DOCA_FLOW_FWD_PORT;
    fwd_origin.port_id = PORT_VF3;

    fwd_reply.type = DOCA_FLOW_FWD_PORT;
    fwd_reply.port_id = PORT_VF0;

    struct doca_flow_pipe_entry *entry = NULL;
    result = doca_flow_ct_add_entry(CT_QUEUE,
                                    ctx->flow_ctx->ct_pipe,
                                    entry_flags,
                                    &match_o,
                                    &match_r,
                                    NULL,  /* actions_origin */
                                    NULL,  /* actions_reply */
                                    &fwd_origin,
                                    &fwd_reply,
                                    CT_AGING_TIMEOUT_S,
                                    NULL,  /* usr_ctx */
                                    entry);
    if (result != DOCA_SUCCESS) {
        atomic_fetch_add(&ctx->metrics->ct_add_errors, 1);
        DOCA_LOG_WARN("Failed to add CT entry: %s", doca_error_get_descr(result));
        rte_pktmbuf_free(pkt);
        return;
    }

    atomic_fetch_add(&ctx->metrics->sessions_offloaded, 1);
    atomic_fetch_add(&ctx->metrics->sessions_active, 1);

    inet_ntop(AF_INET, &src_ip, src_str, sizeof(src_str));
    inet_ntop(AF_INET, &dst_ip, dst_str, sizeof(dst_str));
    DOCA_LOG_INFO("CT OFFLOADED: %s:%u -> %s:%u proto=%u (VF0<->VF3)",
                  src_str, src_port, dst_str, dst_port, protocol);

    /* Forward this first packet manually since CT was just created */
    rte_eth_tx_burst(0, 0, &pkt, 1);
}

void ct_offload_main_loop(struct ct_offload_ctx *ctx)
{
    struct rte_mbuf *pkts[PACKET_BURST];
    int nb_rx;

    DOCA_LOG_INFO("Starting CT offload main loop (polling switch port queue 0)");

    while (ctx->running) {
        /* Poll for packets on the switch port (port 0) queue 0 */
        nb_rx = rte_eth_rx_burst(0, 0, pkts, PACKET_BURST);
        if (nb_rx == 0) {
            /* No packets, brief pause to avoid busy-spin burning CPU */
            usleep(10);
            continue;
        }

        atomic_fetch_add(&ctx->metrics->rx_bursts, 1);

        for (int i = 0; i < nb_rx; i++) {
            ct_offload_process_packet(ctx, pkts[i], 0);
        }

        /* Process any pending CT entries */
        doca_flow_ct_entries_process(ctx->flow_ctx->switch_port,
                                     CT_QUEUE, 0, 0, NULL);
    }

    DOCA_LOG_INFO("CT offload main loop exiting");
}

void ct_offload_stop(struct ct_offload_ctx *ctx)
{
    ctx->running = false;
}
