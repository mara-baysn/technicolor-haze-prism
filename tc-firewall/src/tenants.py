"""Tenant management for per-tenant rule isolation.

Each tenant gets:
- A namespace that scopes all their firewall and NAT rules
- Resource quotas (max rules, max NAT entries)
- Public IP assignments

Tenant ID is extracted from the X-Tenant-ID header on each request.
In production this will come from the mTLS client certificate.
"""

import logging
from typing import Dict, Optional

from .models import TenantConfig

logger = logging.getLogger(__name__)


class TenantManager:
    """Manages tenant registration, quotas, and lifecycle."""

    def __init__(self):
        self._tenants: Dict[str, TenantConfig] = {}

    def register_tenant(
        self,
        tenant_id: str,
        public_ips: Optional[list[str]] = None,
        max_rules: int = 100,
        max_nat_entries: int = 50,
    ) -> TenantConfig:
        """Register a new tenant with quota configuration.

        Args:
            tenant_id: Unique identifier for the tenant.
            public_ips: List of public IPs assigned to this tenant.
            max_rules: Maximum firewall rules allowed (default: 100).
            max_nat_entries: Maximum NAT entries allowed (default: 50).

        Returns:
            The created TenantConfig.

        Raises:
            ValueError: If tenant_id already exists.
        """
        if tenant_id in self._tenants:
            raise ValueError(f"Tenant '{tenant_id}' already exists")

        config = TenantConfig(
            tenant_id=tenant_id,
            public_ips=public_ips or [],
            max_rules=max_rules,
            max_nat_entries=max_nat_entries,
        )
        self._tenants[tenant_id] = config
        logger.info(
            f"Registered tenant '{tenant_id}' "
            f"(max_rules={max_rules}, max_nat={max_nat_entries})"
        )
        return config

    def get_tenant(self, tenant_id: str) -> Optional[TenantConfig]:
        """Get tenant configuration by ID.

        Returns:
            TenantConfig if found, None otherwise.
        """
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[TenantConfig]:
        """List all registered tenants.

        Returns:
            List of all TenantConfig objects.
        """
        return list(self._tenants.values())

    def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant registration.

        Note: The caller is responsible for flushing the tenant's rules
        before or after calling this method.

        Args:
            tenant_id: The tenant to remove.

        Returns:
            True if tenant was deleted, False if not found.
        """
        if tenant_id not in self._tenants:
            return False

        del self._tenants[tenant_id]
        logger.info(f"Deleted tenant '{tenant_id}'")
        return True

    def tenant_exists(self, tenant_id: str) -> bool:
        """Check if a tenant is registered."""
        return tenant_id in self._tenants


# Module-level singleton instance
tenant_manager = TenantManager()
