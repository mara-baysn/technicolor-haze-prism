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

#include <flow_common.h>

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

    /* DEBUG: dump first 64 bytes of first 10 packets */
    if (ctx->metrics->pkts_received <= 10) {
        uint8_t *data = rte_pktmbuf_mtod(pkt, uint8_t *);
        uint16_t len = pkt->data_len > 64 ? 64 : pkt->data_len;
        char hexbuf[200];
        memset(hexbuf, 0, sizeof(hexbuf));
        for (int hx = 0; hx < (int)len && hx < 64; hx++)
            sprintf(hexbuf + hx*3, "%02x ", data[hx]);
        DOCA_LOG_INFO("RX pkt[%lu] len=%u data_off=%u: %s",
                      (unsigned long)ctx->metrics->pkts_received, pkt->data_len,
                      pkt->data_off, hexbuf);
    }

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

    uint32_t prepare_flags = DOCA_FLOW_CT_ENTRY_FLAGS_ALLOC_ON_MISS;
    uint32_t entry_flags = DOCA_FLOW_CT_ENTRY_FLAGS_NO_WAIT |
                           DOCA_FLOW_CT_ENTRY_FLAGS_DIR_ORIGIN |
                           DOCA_FLOW_CT_ENTRY_FLAGS_DIR_REPLY;

    /* Step 1: Prepare (allocate) the CT entry */
    struct doca_flow_pipe_entry *entry = NULL;
    bool conn_found = false;

    result = doca_flow_ct_entry_prepare(CT_QUEUE,
                                        ctx->flow_ctx->ct_pipe,
                                        prepare_flags,
                                        &match_o,
                                        0,  /* hash_origin (auto) */
                                        &match_r,
                                        0,  /* hash_reply (auto) */
                                        &entry,
                                        &conn_found);
    if (result != DOCA_SUCCESS) {
        atomic_fetch_add(&ctx->metrics->ct_add_errors, 1);
        DOCA_LOG_WARN("Failed to prepare CT entry: %s", doca_error_get_descr(result));
        rte_pktmbuf_free(pkt);
        return;
    }

    if (conn_found) {
        /* Connection already exists in CT table */
        rte_pktmbuf_free(pkt);
        return;
    }

    /* Step 2: Add the CT entry with explicit per-direction forwarding */
    struct doca_flow_fwd fwd_o, fwd_r;
    memset(&fwd_o, 0, sizeof(fwd_o));
    memset(&fwd_r, 0, sizeof(fwd_r));

    /* Origin (VF0→VF3): forward to port 4 (VF3 rep) */
    fwd_o.type = DOCA_FLOW_FWD_PORT;
    fwd_o.port_id = 4;

    /* Reply (VF3→VF0): forward to port 1 (VF0 rep) */
    fwd_r.type = DOCA_FLOW_FWD_PORT;
    fwd_r.port_id = 1;

    result = doca_flow_ct_add_entry(CT_QUEUE,
                                    ctx->flow_ctx->ct_pipe,
                                    entry_flags,
                                    &match_o,
                                    &match_r,
                                    NULL,  /* actions_origin */
                                    NULL,  /* actions_reply */
                                    &fwd_o,
                                    &fwd_r,
                                    CT_AGING_TIMEOUT_S,
                                    NULL,  /* usr_ctx */
                                    entry);
    if (result != DOCA_SUCCESS) {
        atomic_fetch_add(&ctx->metrics->ct_add_errors, 1);
        DOCA_LOG_WARN("Failed to add CT entry: %s", doca_error_get_descr(result));
        rte_pktmbuf_free(pkt);
        return;
    }

    /* Step 3: Process the pending entry (NO_WAIT requires explicit process) */
    uint32_t queue_room = 0;
    result = doca_flow_ct_entries_process(ctx->flow_ctx->ports[0],
                                          CT_QUEUE,
                                          0,   /* min_room */
                                          0,   /* max_processed: 0 = all */
                                          &queue_room);
    if (result != DOCA_SUCCESS) {
        DOCA_LOG_WARN("Failed to process CT entries: %s", doca_error_get_descr(result));
    }

    atomic_fetch_add(&ctx->metrics->sessions_offloaded, 1);
    atomic_fetch_add(&ctx->metrics->sessions_active, 1);

    inet_ntop(AF_INET, &src_ip, src_str, sizeof(src_str));
    inet_ntop(AF_INET, &dst_ip, dst_str, sizeof(dst_str));
    DOCA_LOG_INFO("CT OFFLOADED: %s:%u -> %s:%u proto=%u (VF0<->VF3)",
                  src_str, src_port, dst_str, dst_port, protocol);

    /* Forward first packet to VF3 via TX on port 4 (relay path).
     * In switch mode, TX on a representor port delivers to the host VF. */
    uint16_t tx_port = 4;  /* VF3 representor */
    uint16_t nb_tx = rte_eth_tx_burst(tx_port, 0, &pkt, 1);
    if (nb_tx == 0) {
        DOCA_LOG_WARN("TX first packet to port %u failed", tx_port);
        rte_pktmbuf_free(pkt);
    }
}

void ct_offload_main_loop(struct ct_offload_ctx *ctx)
{
    struct rte_mbuf *pkts[PACKET_BURST];
    int nb_rx;

    DOCA_LOG_INFO("Starting firewall main loop (MISS on port 0 + relay on ports 1-4)");

    while (ctx->running) {
        /* 1. Poll switch port (port 0) for CT MISS packets — policy decisions */
        nb_rx = rte_eth_rx_burst(0, 0, pkts, PACKET_BURST);
        if (nb_rx > 0) {
            atomic_fetch_add(&ctx->metrics->rx_bursts, 1);
            for (int i = 0; i < nb_rx; i++) {
                ct_offload_process_packet(ctx, pkts[i], 0);
            }
        }

        /* 2. Relay: poll VF representor ports and TX back (delivers to host VF).
         * CT HIT packets arrive at rep port's DPDK RX; TX on same port delivers to host.
         * This gives us 100G for bulk traffic on 1-2 ARM cores. */
        for (uint16_t p = 1; p < ctx->flow_ctx->nb_ports && p < 5; p++) {
            int nb_relay = rte_eth_rx_burst(p, 0, pkts, PACKET_BURST);
            if (nb_relay > 0) {
                uint16_t sent = rte_eth_tx_burst(p, 0, pkts, nb_relay);
                /* Free any unsent packets */
                for (uint16_t u = sent; u < nb_relay; u++)
                    rte_pktmbuf_free(pkts[u]);
            }
        }

        if (nb_rx == 0) {
            /* Brief pause only when no MISS packets (relay is always fast) */
        }

        /* Process any pending CT entries on port 0 (switch port) */
        doca_flow_ct_entries_process(ctx->flow_ctx->ports[0],
                                     CT_QUEUE, 0, 0, NULL);
    }

    DOCA_LOG_INFO("CT offload main loop exiting");
}

void ct_offload_stop(struct ct_offload_ctx *ctx)
{
    ctx->running = false;
}
