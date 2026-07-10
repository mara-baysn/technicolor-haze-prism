"""TRex STL profile: Offload Ratio Sweep.

Generates traffic where a configurable percentage of flows match pre-programmed
eSwitch sessions. Used by PoC-3 T3 (Offload Ratio Sweep).

Usage:
    start -f traffic-profiles/stl_offload_sweep.py -t offload_ratio=80,flow_count=1000
"""

from trex_stl_lib.api import (
    STLProfile, STLStream, STLPktBuilder, STLFlowLatencyStats,
    STLTXCont, STLScVmRaw, STLVmFlowVar, STLVmWrFlowVar,
    STLVmFixChecksumHw,
)
from scapy.layers.l2 import Ether
from scapy.layers.inet import IP, UDP


class STLS1:
    def get_streams(self, tunables, **kwargs):
        offload_ratio = int(tunables.get("offload_ratio", 80))
        flow_count = int(tunables.get("flow_count", 1000))
        pkt_size = int(tunables.get("pkt_size", 1400))

        # Base packet template
        base_pkt = (
            Ether() /
            IP(src="10.0.0.1", dst="10.1.0.1") /
            UDP(sport=1024, dport=80)
        )
        pad = max(0, pkt_size - len(base_pkt)) * "x"

        # Offloaded flows: fixed src_port range (will match pre-programmed sessions)
        offloaded_count = int(flow_count * offload_ratio / 100)
        new_count = flow_count - offloaded_count

        streams = []

        if offloaded_count > 0:
            vm_offloaded = STLScVmRaw([
                STLVmFlowVar(name="src_port", min_value=10000, max_value=10000 + offloaded_count - 1, size=2, op="inc"),
                STLVmWrFlowVar(fv_name="src_port", pkt_offset="UDP.sport"),
                STLVmFixChecksumHw(l3_offset="IP", l4_offset="UDP", l4_type=1),
            ])
            streams.append(STLStream(
                packet=STLPktBuilder(pkt=base_pkt / pad, vm=vm_offloaded),
                mode=STLTXCont(percentage=offload_ratio),
                name="offloaded_flows",
            ))

        if new_count > 0:
            vm_new = STLScVmRaw([
                STLVmFlowVar(name="src_port", min_value=20000, max_value=20000 + new_count - 1, size=2, op="inc"),
                STLVmWrFlowVar(fv_name="src_port", pkt_offset="UDP.sport"),
                STLVmFixChecksumHw(l3_offset="IP", l4_offset="UDP", l4_type=1),
            ])
            streams.append(STLStream(
                packet=STLPktBuilder(pkt=base_pkt / pad, vm=vm_new),
                mode=STLTXCont(percentage=100 - offload_ratio),
                name="new_flows",
            ))

        return streams


def register():
    return STLS1()
