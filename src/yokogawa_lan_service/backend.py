"""Instrument backend adapters for real and fake Yokogawa sources."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from yokogawa_lan_service.config import InstrumentConfig

_CURRENT_RANGES_A = (1e-3, 10e-3, 100e-3, 200e-3)


@dataclass(frozen=True)
class InstrumentSnapshot:
    """Readback state for one Yokogawa source."""

    name: str
    serial: str
    address: str
    connected: bool
    idn: str | None = None
    output: str | None = None
    source_mode: str | None = None
    current_a: float | None = None
    current_range_a: float | None = None
    error: str | None = None


class InstrumentBackend(Protocol):
    """Operations the service needs from a Yokogawa source."""

    config: InstrumentConfig

    def connect(self) -> None: ...

    def close(self) -> None: ...

    def snapshot(self) -> InstrumentSnapshot: ...

    def program_current(self, target_a: float, *, step_a: float, delay_s: float) -> None:
        """Set current without implicitly enabling output."""

    def set_output(self, state: str) -> None: ...


class QcodesYokogawaBackend:
    """QCoDeS/PyVISA adapter for a Yokogawa GS200/GS211."""

    def __init__(self, config: InstrumentConfig, *, visa_backend: str = "@ivi") -> None:
        self.config = config
        self._visa_backend = visa_backend
        self._instrument = None
        self._last_error: str | None = None

    def connect(self) -> None:
        if self._instrument is not None:
            return
        try:
            from qcodes.instrument_drivers.yokogawa import YokogawaGS200

            self._instrument = YokogawaGS200(
                self.config.name,
                address=self.config.address,
                terminator="\n",
                visalib=self._visa_backend,
                device_clear=False,
            )
            self._last_error = None
        except Exception as exc:
            self._instrument = None
            self._last_error = f"{type(exc).__name__}: {exc}"
            raise

    def close(self) -> None:
        instrument = self._instrument
        self._instrument = None
        if instrument is not None:
            instrument.close()

    def snapshot(self) -> InstrumentSnapshot:
        instrument = self._instrument
        if instrument is None:
            return InstrumentSnapshot(
                name=self.config.name,
                serial=self.config.serial,
                address=self.config.address,
                connected=False,
                error=self._last_error,
            )
        try:
            source_mode = str(instrument.source_mode())
            current_a = float(instrument.current()) if source_mode == "CURR" else None
            current_range_a = (
                float(instrument.current_range()) if source_mode == "CURR" else None
            )
            return InstrumentSnapshot(
                name=self.config.name,
                serial=self.config.serial,
                address=self.config.address,
                connected=True,
                idn=str(instrument.IDN()),
                output=str(instrument.output()),
                source_mode=source_mode,
                current_a=current_a,
                current_range_a=current_range_a,
            )
        except Exception as exc:
            self._last_error = f"{type(exc).__name__}: {exc}"
            with suppress(Exception):
                self.close()
            return InstrumentSnapshot(
                name=self.config.name,
                serial=self.config.serial,
                address=self.config.address,
                connected=False,
                error=self._last_error,
            )

    def program_current(self, target_a: float, *, step_a: float, delay_s: float) -> None:
        instrument = self._require_instrument()
        if step_a <= 0:
            raise ValueError("step_a must be positive")
        if delay_s < 0:
            raise ValueError("delay_s must be non-negative")  # also validated in manager
        self._ensure_current_mode()
        self._ensure_current_range(target_a)
        # Ramp via the current parameter's native software stepping instead of
        # YokogawaGS200.ramp_current(ramp_mode="SOFTWARE"). In qcodes 0.58 that
        # path locks ramp_mode through set_to() and then its SOFTWARE branch
        # tries to set ramp_mode again, raising "Trying to set a parameter that
        # is not settable.". Forcing ramp_mode="JUMP" makes every stepped write a
        # direct :SOUR:LEV update, which never enables the output.
        instrument.ramp_mode("JUMP")
        saved_step = instrument.current.step
        saved_inter_delay = instrument.current.inter_delay
        try:
            instrument.current.step = step_a
            instrument.current.inter_delay = delay_s
            instrument.current(target_a)
        finally:
            instrument.current.step = saved_step
            instrument.current.inter_delay = saved_inter_delay

    def set_output(self, state: str) -> None:
        instrument = self._require_instrument()
        normalized = state.strip().lower()
        if normalized not in {"on", "off"}:
            raise ValueError("output state must be 'on' or 'off'")
        if normalized == "on":
            self._ensure_current_mode()
            instrument.output("on")
        else:
            instrument.output("off")

    def _require_instrument(self):
        if self._instrument is None:
            raise RuntimeError(f"{self.config.name} is not connected")
        return self._instrument

    def _ensure_current_mode(self) -> None:
        instrument = self._require_instrument()
        if instrument.source_mode() == "CURR":
            return
        if instrument.output() == "on":
            raise RuntimeError("cannot switch to current mode while output is on")
        instrument.source_mode("CURR")

    def _ensure_current_range(self, target_a: float) -> None:
        instrument = self._require_instrument()
        required = abs(float(target_a))
        current_range = float(instrument.current_range())
        if required <= current_range:
            return
        if instrument.output() == "on":
            raise RuntimeError(
                "current range is too small while output is on; turn output off first"
            )
        for candidate in _CURRENT_RANGES_A:
            if required <= candidate:
                instrument.current_range(candidate)
                return
        raise ValueError(f"target current {target_a} A exceeds GS200 current range")


class FakeYokogawaBackend:
    """In-memory backend used by tests and offline demos."""

    def __init__(
        self,
        config: InstrumentConfig,
        *,
        fail_connect: bool = False,
        initial_source_mode: str = "CURR",
    ) -> None:
        self.config = config
        self.fail_connect = fail_connect
        self.connected = False
        self.idn = f"YOKOGAWA,GS211,{config.serial},2.02"
        self.output = "off"
        self.source_mode = initial_source_mode
        self.current_a = 0.0
        self.current_range_a = 100e-3
        self.last_error: str | None = None

    def connect(self) -> None:
        if self.fail_connect:
            self.connected = False
            self.last_error = "fake connection failure"
            raise RuntimeError(self.last_error)
        self.connected = True
        self.last_error = None

    def close(self) -> None:
        self.connected = False

    def snapshot(self) -> InstrumentSnapshot:
        if not self.connected:
            return InstrumentSnapshot(
                name=self.config.name,
                serial=self.config.serial,
                address=self.config.address,
                connected=False,
                error=self.last_error,
            )
        return InstrumentSnapshot(
            name=self.config.name,
            serial=self.config.serial,
            address=self.config.address,
            connected=True,
            idn=self.idn,
            output=self.output,
            source_mode=self.source_mode,
            current_a=self.current_a if self.source_mode == "CURR" else None,
            current_range_a=self.current_range_a if self.source_mode == "CURR" else None,
        )

    def program_current(self, target_a: float, *, step_a: float, delay_s: float) -> None:
        self._require_connected()
        if self.source_mode != "CURR":
            if self.output == "on":
                raise RuntimeError("cannot switch to current mode while output is on")
            self.source_mode = "CURR"
        if abs(target_a) > self.current_range_a:
            if self.output == "on":
                raise RuntimeError(
                    "current range is too small while output is on; turn output off first"
                )
            for candidate in _CURRENT_RANGES_A:
                if abs(target_a) <= candidate:
                    self.current_range_a = candidate
                    break
            else:
                raise ValueError(f"target current {target_a} A exceeds GS200 current range")
        self.current_a = float(target_a)

    def set_output(self, state: str) -> None:
        self._require_connected()
        normalized = state.strip().lower()
        if normalized not in {"on", "off"}:
            raise ValueError("output state must be 'on' or 'off'")
        if normalized == "on" and self.source_mode != "CURR":
            raise RuntimeError("cannot enable output unless source mode is CURR")
        self.output = normalized

    def _require_connected(self) -> None:
        if not self.connected:
            raise RuntimeError(f"{self.config.name} is not connected")


def qcodes_backend_factory(
    config: InstrumentConfig, *, visa_backend: str
) -> InstrumentBackend:
    """Create the real QCoDeS-backed instrument adapter."""

    return QcodesYokogawaBackend(config, visa_backend=visa_backend)
