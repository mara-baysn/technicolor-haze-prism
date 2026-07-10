"""PoC test runner -- executes poc-test-definition/v1 YAML test files."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from .ssh_executor import SSHExecutor

logger = logging.getLogger(__name__)


class TestStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class StepResult:
    step_index: int
    command: str
    target: str
    exit_code: int
    stdout: str
    duration_ms: float
    passed: bool


@dataclass
class AssertionResult:
    description: str
    passed: bool
    actual_value: Optional[str] = None
    expected: Optional[str] = None


@dataclass
class TestResult:
    test_id: str
    status: TestStatus
    started_at: float
    completed_at: Optional[float] = None
    duration_seconds: Optional[float] = None
    step_results: list[StepResult] = field(default_factory=list)
    assertion_results: list[AssertionResult] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dictionary."""
        return {
            "test_id": self.test_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "step_results": [
                {
                    "step_index": s.step_index,
                    "command": s.command,
                    "target": s.target,
                    "exit_code": s.exit_code,
                    "stdout": s.stdout[:500],  # Truncate for API responses
                    "duration_ms": s.duration_ms,
                    "passed": s.passed,
                }
                for s in self.step_results
            ],
            "assertion_results": [
                {
                    "description": a.description,
                    "passed": a.passed,
                    "actual_value": a.actual_value,
                    "expected": a.expected,
                }
                for a in self.assertion_results
            ],
            "error_message": self.error_message,
        }


class TestRunner:
    """Executes PoC test YAML definitions.

    Test YAML format (poc-test-definition/v1):
        kind: poc-test-definition/v1
        metadata:
            id: T1
            name: DPDK Baseline
            description: ...
        setup:
            - target: dpu1
              command: "systemctl restart dpdk-app"
        steps:
            - target: dpu2
              command: "trex -f stl/bench.py -d 60"
              expect_exit_code: 0
        assertions:
            - description: "Throughput above 90 Gbps"
              type: output_contains
              step: 0
              pattern: "tx_bps.*[89][0-9]\\.[0-9]+ Gbps"
        cleanup:
            - target: dpu2
              command: "trex --stop"
    """

    def __init__(self, ssh_executor: SSHExecutor, tests_dir: str = "tests/"):
        self._ssh = ssh_executor
        self._tests_dir = Path(tests_dir)
        self._results: dict[str, TestResult] = {}
        self._running_test: Optional[str] = None

    def list_tests(self) -> list[dict]:
        """List available test definitions.

        Scans tests_dir for .yaml/.yml files with poc-test-definition/v1 kind.
        Returns list of metadata dicts with id, name, description, status.
        """
        tests: list[dict] = []

        if not self._tests_dir.exists():
            logger.warning("Tests directory not found: %s", self._tests_dir)
            return tests

        for yaml_file in sorted(self._tests_dir.glob("*.y*ml")):
            try:
                with open(yaml_file) as f:
                    doc = yaml.safe_load(f)

                if not isinstance(doc, dict):
                    continue

                kind = doc.get("kind", "")
                if kind != "poc-test-definition/v1":
                    continue

                metadata = doc.get("metadata", {})
                test_id = metadata.get("id", yaml_file.stem)
                existing_result = self._results.get(test_id)

                tests.append({
                    "id": test_id,
                    "name": metadata.get("name", yaml_file.stem),
                    "description": metadata.get("description", ""),
                    "file": str(yaml_file),
                    "status": existing_result.status.value if existing_result else "ready",
                })
            except (yaml.YAMLError, OSError) as exc:
                logger.warning("Cannot parse test file %s: %s", yaml_file, exc)

        return tests

    async def run_test(self, test_id: str) -> TestResult:
        """Execute a test definition end-to-end.

        Phases: setup -> steps -> assertions -> cleanup
        Handles SSH connection failures gracefully (marks test as ERROR).
        """
        result = TestResult(
            test_id=test_id,
            status=TestStatus.RUNNING,
            started_at=time.time(),
        )
        self._running_test = test_id
        self._results[test_id] = result

        try:
            # Find and load test definition
            test_def = self._load_test_definition(test_id)
            if test_def is None:
                result.status = TestStatus.ERROR
                result.error_message = f"Test definition not found: {test_id}"
                result.completed_at = time.time()
                result.duration_seconds = result.completed_at - result.started_at
                return result

            # Execute setup phase
            setup_steps = test_def.get("setup", [])
            for i, step in enumerate(setup_steps):
                step_result = await self._execute_step(i, step, phase="setup")
                result.step_results.append(step_result)
                if not step_result.passed:
                    result.status = TestStatus.ERROR
                    result.error_message = (
                        f"Setup step {i} failed on {step_result.target}: "
                        f"exit_code={step_result.exit_code}"
                    )
                    # Still run cleanup
                    await self._run_cleanup(test_def)
                    result.completed_at = time.time()
                    result.duration_seconds = result.completed_at - result.started_at
                    return result

            # Execute test steps
            test_steps = test_def.get("steps", [])
            for i, step in enumerate(test_steps):
                step_result = await self._execute_step(i, step, phase="test")
                result.step_results.append(step_result)

            # Evaluate assertions
            assertions = test_def.get("assertions", [])
            all_passed = True
            for assertion_def in assertions:
                assertion_result = self._evaluate_assertion(assertion_def, result.step_results)
                result.assertion_results.append(assertion_result)
                if not assertion_result.passed:
                    all_passed = False

            # Determine final status
            if all_passed and all(s.passed for s in result.step_results):
                result.status = TestStatus.PASSED
            else:
                result.status = TestStatus.FAILED

            # Cleanup phase
            await self._run_cleanup(test_def)

        except Exception as exc:
            result.status = TestStatus.ERROR
            result.error_message = f"Unexpected error: {exc}"
            logger.exception("Test %s failed with unexpected error", test_id)
        finally:
            result.completed_at = time.time()
            result.duration_seconds = result.completed_at - result.started_at
            self._running_test = None

        return result

    def get_result(self, test_id: str) -> Optional[TestResult]:
        """Get the result of a previously run test."""
        return self._results.get(test_id)

    @property
    def is_running(self) -> bool:
        return self._running_test is not None

    @property
    def running_test_id(self) -> Optional[str]:
        return self._running_test

    def _load_test_definition(self, test_id: str) -> Optional[dict]:
        """Load a test YAML file by test_id."""
        if not self._tests_dir.exists():
            return None

        for yaml_file in self._tests_dir.glob("*.y*ml"):
            try:
                with open(yaml_file) as f:
                    doc = yaml.safe_load(f)
                if isinstance(doc, dict):
                    metadata = doc.get("metadata", {})
                    if metadata.get("id") == test_id:
                        return doc
            except (yaml.YAMLError, OSError):
                continue

        return None

    async def _execute_step(
        self, index: int, step: dict, phase: str = "test"
    ) -> StepResult:
        """Execute a single step on the target host."""
        target = step.get("target", "")
        command = step.get("command", "")
        expect_exit_code = step.get("expect_exit_code", 0)
        timeout = step.get("timeout", 60.0)

        logger.info(
            "[%s] Step %d on %s: %s", phase, index, target, command
        )

        try:
            cmd_result = await self._ssh.execute(target, command, timeout=timeout)
            passed = cmd_result.exit_code == expect_exit_code
            return StepResult(
                step_index=index,
                command=command,
                target=target,
                exit_code=cmd_result.exit_code,
                stdout=cmd_result.stdout,
                duration_ms=cmd_result.duration_ms,
                passed=passed,
            )
        except RuntimeError as exc:
            # SSH not connected
            logger.error("SSH connection error for %s: %s", target, exc)
            return StepResult(
                step_index=index,
                command=command,
                target=target,
                exit_code=-1,
                stdout=f"SSH ERROR: {exc}",
                duration_ms=0.0,
                passed=False,
            )
        except TimeoutError as exc:
            logger.error("Timeout on %s: %s", target, exc)
            return StepResult(
                step_index=index,
                command=command,
                target=target,
                exit_code=-2,
                stdout=f"TIMEOUT: {exc}",
                duration_ms=timeout * 1000,
                passed=False,
            )

    def _evaluate_assertion(
        self, assertion_def: dict, step_results: list[StepResult]
    ) -> AssertionResult:
        """Evaluate a single assertion against step results."""
        import re

        description = assertion_def.get("description", "unnamed assertion")
        assertion_type = assertion_def.get("type", "exit_code")
        step_index = assertion_def.get("step", 0)

        # Find the referenced step result (offset for setup steps if needed)
        if step_index >= len(step_results):
            return AssertionResult(
                description=description,
                passed=False,
                actual_value="N/A",
                expected=f"step {step_index} to exist",
            )

        step = step_results[step_index]

        if assertion_type == "exit_code":
            expected_code = assertion_def.get("expected", 0)
            return AssertionResult(
                description=description,
                passed=step.exit_code == expected_code,
                actual_value=str(step.exit_code),
                expected=str(expected_code),
            )
        elif assertion_type == "output_contains":
            pattern = assertion_def.get("pattern", "")
            match = re.search(pattern, step.stdout)
            return AssertionResult(
                description=description,
                passed=match is not None,
                actual_value=step.stdout[:200],
                expected=f"pattern: {pattern}",
            )
        elif assertion_type == "output_not_contains":
            pattern = assertion_def.get("pattern", "")
            match = re.search(pattern, step.stdout)
            return AssertionResult(
                description=description,
                passed=match is None,
                actual_value=step.stdout[:200],
                expected=f"not matching: {pattern}",
            )
        elif assertion_type == "numeric_gte":
            pattern = assertion_def.get("extract_pattern", r"[\d.]+")
            threshold = float(assertion_def.get("threshold", 0))
            match = re.search(pattern, step.stdout)
            if match:
                actual = float(match.group())
                return AssertionResult(
                    description=description,
                    passed=actual >= threshold,
                    actual_value=str(actual),
                    expected=f">= {threshold}",
                )
            return AssertionResult(
                description=description,
                passed=False,
                actual_value="no numeric value found",
                expected=f">= {threshold}",
            )
        else:
            return AssertionResult(
                description=description,
                passed=False,
                actual_value="N/A",
                expected=f"unknown assertion type: {assertion_type}",
            )

    async def _run_cleanup(self, test_def: dict) -> None:
        """Execute cleanup steps, logging but not failing on errors."""
        cleanup_steps = test_def.get("cleanup", [])
        for i, step in enumerate(cleanup_steps):
            try:
                await self._execute_step(i, step, phase="cleanup")
            except Exception as exc:
                logger.warning("Cleanup step %d failed: %s", i, exc)
