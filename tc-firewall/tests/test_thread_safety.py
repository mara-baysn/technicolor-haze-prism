"""Thread-safety tests for tc_manager handle allocation.

Verifies that the _tc_lock prevents race conditions when multiple threads
concurrently add or remove rules.
"""

import threading
from unittest.mock import patch, MagicMock
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.tc_manager import (
    add_allow_rule,
    remove_rule,
    _tc_lock,
    INTERNET_PORT,
    CLIENT_PORT,
)


class TestConcurrentAddAllowRule:
    """Test that 10 threads calling add_allow_rule get unique handles."""

    @patch("src.tc_manager.subprocess.run")
    def test_no_handle_collisions_10_threads(self, mock_run):
        """Concurrent add_allow_rule from 10 threads must produce unique handles."""
        # Each thread gets a unique handle via an incrementing counter
        # The mock simulates tc returning sequential handles
        handle_counter = {"value": 0}
        counter_lock = threading.Lock()

        def mock_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # If this is a "filter show" (handle query), return a unique handle
            if "show" in cmd:
                with counter_lock:
                    handle_counter["value"] += 1
                    handle = handle_counter["value"]
                return MagicMock(
                    returncode=0,
                    stdout=f"filter protocol ip pref 100 flower chain 0 handle 0x{handle:x}\n",
                    stderr="",
                )
            # Otherwise (filter add), just succeed
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_subprocess

        results = []
        errors = []

        def worker(i):
            try:
                fwd, rev = add_allow_rule(
                    src_ip=f"10.0.0.{i}",
                    dst_ip=f"10.0.1.{i}",
                    protocol="tcp",
                    dst_port=8000 + i,
                    priority=100,
                )
                results.append((fwd, rev))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        assert len(results) == 10

        # All forward handles must be unique
        fwd_handles = [r[0] for r in results]
        assert len(set(fwd_handles)) == 10, (
            f"Handle collision detected! Got {len(set(fwd_handles))} unique "
            f"handles out of 10: {fwd_handles}"
        )

        # All reverse handles must be unique
        rev_handles = [r[1] for r in results]
        assert len(set(rev_handles)) == 10, (
            f"Reverse handle collision detected! Got {len(set(rev_handles))} unique "
            f"handles out of 10: {rev_handles}"
        )

        # Forward and reverse handles must not overlap (they query different ports)
        all_handles = fwd_handles + rev_handles
        assert len(set(all_handles)) == 20, "Forward and reverse handles overlap"


class TestConcurrentAddAndRemove:
    """Test that concurrent add + remove does not corrupt state."""

    @patch("src.tc_manager.subprocess.run")
    def test_add_and_remove_no_race(self, mock_run):
        """Interleaved add and remove must not corrupt handle tracking."""
        handle_counter = {"value": 0}
        counter_lock = threading.Lock()

        def mock_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "show" in cmd:
                with counter_lock:
                    handle_counter["value"] += 1
                    handle = handle_counter["value"]
                return MagicMock(
                    returncode=0,
                    stdout=f"filter protocol ip pref 100 flower chain 0 handle 0x{handle:x}\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_subprocess

        results = []
        errors = []
        barrier = threading.Barrier(6)  # 3 adders + 3 removers

        def adder(i):
            try:
                barrier.wait(timeout=5)
                fwd, rev = add_allow_rule(
                    src_ip=f"10.0.{i}.1",
                    dst_ip=f"10.0.{i}.2",
                    protocol="tcp",
                    dst_port=9000 + i,
                    priority=100,
                )
                results.append(("add", fwd, rev))
            except Exception as e:
                errors.append(e)

        def remover(i):
            try:
                barrier.wait(timeout=5)
                # remove_rule should complete without error even with concurrent adds
                remove_rule(INTERNET_PORT, f"0x{i:x}", priority=100)
                results.append(("remove", f"0x{i:x}", None))
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(3):
            threads.append(threading.Thread(target=adder, args=(i,)))
            threads.append(threading.Thread(target=remover, args=(i + 100,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        # All 6 operations should have completed
        assert len(results) == 6


class TestLockPreventsInterleaving:
    """Verify the lock serializes critical sections."""

    @patch("src.tc_manager.subprocess.run")
    def test_lock_serializes_add_operations(self, mock_run):
        """Verify that tc add + handle query are never interleaved."""
        # Track the order of operations to verify atomicity
        operation_log = []
        log_lock = threading.Lock()

        def mock_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            tid = threading.current_thread().name
            if "add" in cmd:
                with log_lock:
                    operation_log.append((tid, "add"))
            elif "show" in cmd:
                with log_lock:
                    operation_log.append((tid, "show"))
                return MagicMock(
                    returncode=0,
                    stdout="filter protocol ip pref 100 flower chain 0 handle 0x1\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_subprocess

        errors = []

        def worker(i):
            try:
                add_allow_rule(
                    src_ip=f"10.0.0.{i}",
                    dst_ip=f"10.0.1.{i}",
                    protocol="tcp",
                    dst_port=7000 + i,
                    priority=100,
                )
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(i,), name=f"worker-{i}")
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"

        # Verify atomicity: for each thread, its "add" must be immediately
        # followed by its "show" (no other thread's op in between)
        # Each add_allow_rule does 2 locked sections (fwd + rev), each has add+show
        for i in range(len(operation_log) - 1):
            tid, op = operation_log[i]
            if op == "add":
                next_tid, next_op = operation_log[i + 1]
                assert next_tid == tid and next_op == "show", (
                    f"Lock violation: thread {tid} did 'add' but next op was "
                    f"({next_tid}, {next_op}) — expected ({tid}, 'show'). "
                    f"Full log: {operation_log}"
                )


class TestHandleMapIntegrity:
    """Verify handle map stays consistent under concurrent access."""

    @patch("src.tc_manager.subprocess.run")
    def test_handles_are_monotonically_unique(self, mock_run):
        """Even under contention, each call gets a distinct handle."""
        # Simulate a kernel that assigns handles in order
        call_count = {"add": 0}
        call_lock = threading.Lock()

        def mock_subprocess(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "add" in cmd and "filter" in cmd:
                with call_lock:
                    call_count["add"] += 1
                return MagicMock(returncode=0, stdout="", stderr="")
            elif "show" in cmd:
                # Return handle matching the current add count
                with call_lock:
                    h = call_count["add"]
                return MagicMock(
                    returncode=0,
                    stdout=f"filter protocol ip pref 100 flower chain 0 handle 0x{h:x}\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = mock_subprocess

        handles_collected = []
        handles_lock = threading.Lock()
        errors = []

        def worker(i):
            try:
                fwd, rev = add_allow_rule(
                    src_ip=f"172.16.{i}.1",
                    dst_ip=f"172.16.{i}.2",
                    protocol="udp",
                    dst_port=5000 + i,
                    priority=100,
                )
                with handles_lock:
                    handles_collected.append(fwd)
                    handles_collected.append(rev)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Threads raised errors: {errors}"
        # 10 threads x 2 handles (fwd + rev) = 20 handles
        assert len(handles_collected) == 20
        # All handles must be unique (the lock ensures add+show atomicity
        # so no two threads see the same counter state)
        assert len(set(handles_collected)) == 20, (
            f"Handle collision! {len(set(handles_collected))} unique out of 20: "
            f"{handles_collected}"
        )
