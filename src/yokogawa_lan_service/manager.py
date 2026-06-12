"""Thread-safe Yokogawa service manager."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from yokogawa_lan_service.backend import (
    InstrumentBackend,
    InstrumentSnapshot,
    qcodes_backend_factory,
)
from yokogawa_lan_service.config import (
    InstrumentConfig,
    ServiceConfig,
    config_to_public_dict,
    normalize_qubit,
)

BackendFactory = Callable[[InstrumentConfig], InstrumentBackend]


class YokogawaServiceError(RuntimeError):
    """Base service-level error."""


class UnknownQubitError(YokogawaServiceError):
    """Raised when a qubit is not present in the service mapping."""


class InstrumentUnavailableError(YokogawaServiceError):
    """Raised when a mapped Yokogawa is not connected."""


class InstrumentBusyError(YokogawaServiceError):
    """Raised when another request owns the same instrument lock."""


class CurrentLimitError(YokogawaServiceError):
    """Raised when a requested current is outside configured limits."""


class AuditLog:
    """Append-only JSONL audit log for mutating operations."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.Lock()

    def write(self, event: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        payload = {
            "timestamp": now.isoformat(),
            **event,
        }
        with self._lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / f"{now.date().isoformat()}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")


class YokogawaManager:
    """Owns Yokogawa instrument sessions and applies service safety policy."""

    def __init__(
        self,
        config: ServiceConfig,
        *,
        backend_factory: BackendFactory | None = None,
    ) -> None:
        self.config = config
        if backend_factory is None:
            def backend_factory(instrument_config: InstrumentConfig) -> InstrumentBackend:
                return qcodes_backend_factory(
                    instrument_config,
                    visa_backend=config.visa_backend,
                )

        self._backends = {
            instrument.name: backend_factory(instrument)
            for instrument in config.instruments
        }
        self._locks = {name: threading.Lock() for name in self._backends}
        self.audit = AuditLog(config.resolved_audit_log_dir)

    def start(self) -> None:
        failures: dict[str, str] = {}
        for name, backend in self._backends.items():
            try:
                backend.connect()
            except Exception as exc:
                failures[name] = f"{type(exc).__name__}: {exc}"
        if failures and not self.config.allow_degraded_startup:
            raise RuntimeError(f"failed to connect Yokogawas: {failures}")

    def close(self) -> None:
        for backend in self._backends.values():
            with suppress(Exception):
                backend.close()

    def health(self) -> dict[str, Any]:
        statuses = self.all_statuses()
        connected = sum(1 for status in statuses if status["connected"])
        return {
            "ok": connected > 0,
            "connected_count": connected,
            "unavailable_count": len(statuses) - connected,
            "instrument_count": len(statuses),
            "config": config_to_public_dict(self.config),
        }

    def all_statuses(self) -> list[dict[str, Any]]:
        qubit_by_instrument = {
            instrument_name: qubit
            for qubit, instrument_name in self.config.qubit_mapping.items()
        }
        return [
            self._status_payload(
                backend.snapshot(),
                qubit=qubit_by_instrument.get(name),
            )
            for name, backend in self._backends.items()
        ]

    def qubit_status(self, qubit: str) -> dict[str, Any]:
        normalized = normalize_qubit(qubit)
        backend = self._backend_for_qubit(normalized)
        return self._status_payload(backend.snapshot(), qubit=normalized)

    def refresh(self) -> dict[str, Any]:
        for backend in self._backends.values():
            if not backend.snapshot().connected:
                with suppress(Exception):
                    backend.close()
                with suppress(Exception):
                    backend.connect()
        return self.health()

    def program_current(
        self,
        qubit: str,
        *,
        target_a: float,
        step_a: float | None = None,
        delay_s: float | None = None,
    ) -> dict[str, Any]:
        normalized = normalize_qubit(qubit)
        self._validate_current(target_a)
        resolved_step = self.config.default_step_a if step_a is None else float(step_a)
        resolved_delay = (
            self.config.default_delay_s if delay_s is None else float(delay_s)
        )
        if resolved_step <= 0:
            raise ValueError("step_a must be positive")
        if resolved_delay < 0:
            raise ValueError("delay_s must be non-negative")
        backend = self._backend_for_qubit(normalized)
        with self._instrument_lock(backend.config.name):
            event = {
                "action": "program_current",
                "qubit": normalized,
                "instrument": backend.config.name,
                "serial": backend.config.serial,
                "target_a": target_a,
                "step_a": resolved_step,
                "delay_s": resolved_delay,
            }
            try:
                self._require_available(backend)
                backend.program_current(
                    target_a,
                    step_a=resolved_step,
                    delay_s=resolved_delay,
                )
                payload = self._status_payload(backend.snapshot(), qubit=normalized)
                self.audit.write({**event, "success": True})
                return payload
            except Exception as exc:
                self.audit.write(
                    {
                        **event,
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise

    def set_output(self, qubit: str, *, state: str) -> dict[str, Any]:
        normalized = normalize_qubit(qubit)
        normalized_state = state.strip().lower()
        if normalized_state not in {"on", "off"}:
            raise ValueError("state must be 'on' or 'off'")
        backend = self._backend_for_qubit(normalized)
        with self._instrument_lock(backend.config.name):
            event = {
                "action": "set_output",
                "qubit": normalized,
                "instrument": backend.config.name,
                "serial": backend.config.serial,
                "state": normalized_state,
            }
            try:
                self._require_available(backend)
                if normalized_state == "on":
                    snapshot = backend.snapshot()
                    current = snapshot.current_a
                    if current is None:
                        raise RuntimeError("cannot enable output without current readback")
                    self._validate_current(current)
                backend.set_output(normalized_state)
                payload = self._status_payload(backend.snapshot(), qubit=normalized)
                self.audit.write({**event, "success": True})
                return payload
            except Exception as exc:
                self.audit.write(
                    {
                        **event,
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise

    def all_off(self) -> dict[str, Any]:
        acquired: list[threading.Lock] = []
        try:
            for name in sorted(self._locks):
                lock = self._locks[name]
                if not lock.acquire(blocking=False):
                    raise InstrumentBusyError(f"{name} is busy")
                acquired.append(lock)
            failures: dict[str, str] = {}
            for name, backend in sorted(self._backends.items()):
                if not backend.snapshot().connected:
                    continue
                try:
                    backend.set_output("off")
                except Exception as exc:
                    failures[name] = f"{type(exc).__name__}: {exc}"
            self.audit.write(
                {
                    "action": "all_off",
                    "success": not failures,
                    "failures": failures,
                }
            )
            if failures:
                raise RuntimeError(f"failed to turn off some Yokogawas: {failures}")
            return self.health()
        finally:
            for lock in reversed(acquired):
                lock.release()

    def _backend_for_qubit(self, qubit: str) -> InstrumentBackend:
        instrument_name = self.config.qubit_mapping.get(qubit)
        if instrument_name is None:
            raise UnknownQubitError(f"unknown qubit: {qubit}")
        return self._backends[instrument_name]

    def _instrument_lock(self, instrument_name: str):
        return _NonBlockingLock(self._locks[instrument_name], instrument_name)

    def _require_available(self, backend: InstrumentBackend) -> None:
        snapshot = backend.snapshot()
        if not snapshot.connected:
            raise InstrumentUnavailableError(
                f"{backend.config.name} is unavailable: {snapshot.error or 'not connected'}"
            )

    def _validate_current(self, current_a: float) -> None:
        if current_a < self.config.min_current_a or current_a > self.config.max_current_a:
            raise CurrentLimitError(
                f"current {current_a} A outside configured range "
                f"[{self.config.min_current_a}, {self.config.max_current_a}]"
            )

    @staticmethod
    def _status_payload(
        snapshot: InstrumentSnapshot,
        *,
        qubit: str | None,
    ) -> dict[str, Any]:
        return {
            "name": snapshot.name,
            "qubit": qubit,
            "serial": snapshot.serial,
            "address": snapshot.address,
            "connected": snapshot.connected,
            "idn": snapshot.idn,
            "output": snapshot.output,
            "source_mode": snapshot.source_mode,
            "current_a": snapshot.current_a,
            "current_range_a": snapshot.current_range_a,
            "error": snapshot.error,
        }


class _NonBlockingLock:
    def __init__(self, lock: threading.Lock, name: str) -> None:
        self._lock = lock
        self._name = name

    def __enter__(self) -> None:
        if not self._lock.acquire(blocking=False):
            raise InstrumentBusyError(f"{self._name} is busy")

    def __exit__(self, exc_type, exc, tb) -> None:
        self._lock.release()
