"""Structured audit logging for tc-firewall rule changes.

Writes JSON-lines to /var/log/prism-firewall/audit.jsonl with rotation.
Every mutating operation (rule add/delete/flush, NAT changes, daemon lifecycle)
is recorded with timestamp, actor, resource details, and result.
"""

import json
import logging
import logging.handlers
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# Default log path; overridable for testing
DEFAULT_LOG_DIR = "/var/log/prism-firewall"
DEFAULT_LOG_FILE = "audit.jsonl"

# Rotation: max 100MB per file, keep 10 backups
MAX_BYTES = 100 * 1024 * 1024  # 100 MB
BACKUP_COUNT = 10


class AuditLogger:
    """Structured audit logger that writes JSON-lines with file rotation.

    Each entry contains:
      - timestamp (ISO 8601 UTC)
      - action (e.g. "rule.created", "rule.deleted", "nat.snat_created")
      - actor (e.g. "api", later: tenant ID from auth)
      - resource_type (e.g. "firewall_rule", "nat_rule")
      - resource_id
      - details (action-specific payload)
      - result ("success" or "failure")
      - source_ip (from the HTTP request)
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        log_file: Optional[str] = None,
        max_bytes: int = MAX_BYTES,
        backup_count: int = BACKUP_COUNT,
    ):
        self.log_dir = log_dir or DEFAULT_LOG_DIR
        self.log_file = log_file or DEFAULT_LOG_FILE
        self.max_bytes = max_bytes
        self.backup_count = backup_count

        # In-memory ring buffer for GET /audit queries
        self._recent_entries: deque = deque(maxlen=1000)

        # Set up the rotating file handler
        self._logger = logging.getLogger("tc-firewall.audit")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False

        self._setup_handler()

    def _setup_handler(self):
        """Configure the RotatingFileHandler, creating the log directory if needed."""
        log_path = os.path.join(self.log_dir, self.log_file)

        try:
            Path(self.log_dir).mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
            )
            # Raw JSON output, no formatter prefix
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
            self._file_enabled = True
        except (OSError, PermissionError):
            # If we cannot write to the log dir (e.g. in test or non-root),
            # disable file logging but keep in-memory buffer active.
            self._file_enabled = False

    def log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Optional[Dict[str, Any]] = None,
        result: str = "success",
        source_ip: Optional[str] = None,
        actor: str = "api",
    ) -> Dict[str, Any]:
        """Write an audit entry.

        Returns the entry dict for convenience (e.g. in tests).
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "actor": actor,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "details": details or {},
            "result": result,
            "source_ip": source_ip or "unknown",
        }

        line = json.dumps(entry, separators=(",", ":"))

        # Write to rotating file
        if self._file_enabled:
            self._logger.info(line)

        # Store in ring buffer
        self._recent_entries.append(entry)

        return entry

    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Return the most recent audit entries (newest first)."""
        entries = list(self._recent_entries)
        entries.reverse()
        return entries[:limit]

    # --- Convenience methods for common actions ---

    def log_rule_created(
        self,
        rule_id: str,
        details: Dict[str, Any],
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action="rule.created",
            resource_type="firewall_rule",
            resource_id=rule_id,
            details=details,
            source_ip=source_ip,
        )

    def log_rule_deleted(
        self,
        rule_id: str,
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action="rule.deleted",
            resource_type="firewall_rule",
            resource_id=rule_id,
            source_ip=source_ip,
        )

    def log_rules_flushed(
        self,
        count: int,
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action="rules.flushed",
            resource_type="firewall_rule",
            resource_id="*",
            details={"rules_removed": count},
            source_ip=source_ip,
        )

    def log_nat_created(
        self,
        nat_type: str,
        rule_id: str,
        details: Dict[str, Any],
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action=f"nat.{nat_type}_created",
            resource_type="nat_rule",
            resource_id=rule_id,
            details=details,
            source_ip=source_ip,
        )

    def log_nat_deleted(
        self,
        rule_id: str,
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action="nat.deleted",
            resource_type="nat_rule",
            resource_id=rule_id,
            source_ip=source_ip,
        )

    def log_nat_flushed(
        self,
        count: int,
        source_ip: Optional[str] = None,
    ):
        return self.log(
            action="nat.flushed",
            resource_type="nat_rule",
            resource_id="*",
            details={"rules_removed": count},
            source_ip=source_ip,
        )

    def log_daemon_start(self):
        return self.log(
            action="daemon.started",
            resource_type="system",
            resource_id="tc-firewall",
            actor="system",
        )

    def log_daemon_stop(self):
        return self.log(
            action="daemon.stopped",
            resource_type="system",
            resource_id="tc-firewall",
            actor="system",
        )
