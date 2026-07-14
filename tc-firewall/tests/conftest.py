"""Shared test fixtures for the tc-firewall test suite.

Provides a default test tenant and header-aware client to maintain
backward compatibility with pre-tenant-isolation tests.
"""

import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.main import app, rules_db, nat_entries_db
from src.tenants import tenant_manager

# Default test tenant ID used across all existing tests
DEFAULT_TEST_TENANT = "test-tenant"


@pytest.fixture(autouse=True)
def setup_default_tenant():
    """Register a default test tenant before each test and clean up after.

    This ensures backward compatibility: existing tests that don't
    explicitly manage tenants get a default tenant registered.
    """
    # Register default tenant with generous quotas
    if not tenant_manager.tenant_exists(DEFAULT_TEST_TENANT):
        tenant_manager.register_tenant(
            tenant_id=DEFAULT_TEST_TENANT,
            public_ips=["203.0.113.100"],
            max_rules=1000,
            max_nat_entries=500,
        )
    yield
    # Clean up tenant state
    tenant_manager._tenants.clear()


class TenantTestClient:
    """Wrapper around TestClient that injects X-Tenant-ID header by default."""

    def __init__(self, client: TestClient, tenant_id: str = DEFAULT_TEST_TENANT):
        self._client = client
        self._tenant_id = tenant_id

    def _merge_headers(self, headers=None):
        merged = {"X-Tenant-ID": self._tenant_id}
        if headers:
            merged.update(headers)
        return merged

    def get(self, url, **kwargs):
        kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
        return self._client.get(url, **kwargs)

    def post(self, url, **kwargs):
        kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
        return self._client.post(url, **kwargs)

    def put(self, url, **kwargs):
        kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
        return self._client.put(url, **kwargs)

    def delete(self, url, **kwargs):
        kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
        return self._client.delete(url, **kwargs)

    def patch(self, url, **kwargs):
        kwargs["headers"] = self._merge_headers(kwargs.get("headers"))
        return self._client.patch(url, **kwargs)

    def __getattr__(self, name):
        """Proxy any other attributes to the underlying client."""
        return getattr(self._client, name)
