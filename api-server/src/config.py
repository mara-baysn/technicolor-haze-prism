"""Lab hardware inventory for the Plano PoC environment."""

from dataclasses import dataclass


@dataclass
class LabHost:
    id: str
    host: str
    user: str
    auth_method: str  # "key" or "password"
    key_path: str | None = None
    role: str = ""


LAB_INVENTORY = [
    LabHost(
        id="hpe_server",
        host="192.168.9.23",
        user="almalinux",
        auth_method="key",
        key_path="~/.ssh/id_rsa_plano_vms",
        role="Prism VM host (Tier 3)",
    ),
    LabHost(
        id="dpu1",
        host="192.168.0.38",
        user="ubuntu",
        auth_method="password",
        role="BF3 DPU #1 — eSwitch programming",
    ),
    LabHost(
        id="dpu2",
        host="192.168.0.32",
        user="ubuntu",
        auth_method="password",
        role="BF3 DPU #2 — traffic source",
    ),
    LabHost(
        id="fabric_switch",
        host="192.168.0.29",
        user="admin",
        auth_method="key",
        key_path="~/.ssh/id_rsa_fs_switch",
        role="Green fabric switch",
    ),
    LabHost(
        id="arm_server",
        host="192.168.9.16",
        user="almalinux",
        auth_method="key",
        key_path="~/.ssh/id_rsa_plano_vms",
        role="Control plane / orchestrator",
    ),
]
