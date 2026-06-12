from __future__ import annotations

import pytest

from yokogawa_lan_service.backend import FakeYokogawaBackend, QcodesYokogawaBackend
from yokogawa_lan_service.config import InstrumentConfig


class FailingInstrument:
    def __init__(self) -> None:
        self.closed = False

    def source_mode(self) -> str:
        raise OSError("instrument disappeared")

    def close(self) -> None:
        self.closed = True


def test_snapshot_drops_stale_qcodes_driver_after_read_error() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = FailingInstrument()
    backend._instrument = instrument

    snapshot = backend.snapshot()

    assert snapshot.connected is False
    assert snapshot.error == "OSError: instrument disappeared"
    assert backend._instrument is None
    assert instrument.closed is True


def test_fake_backend_rejects_currents_above_hardware_range() -> None:
    backend = FakeYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    backend.connect()

    with pytest.raises(ValueError, match="exceeds GS200 current range"):
        backend.program_current(0.201, step_a=1e-5, delay_s=0.01)

    assert backend.current_a == 0.0


class _QcodesParam:
    """Mimics a qcodes get/set Parameter, with optional .step/.inter_delay."""

    def __init__(self, value: object) -> None:
        self.value = value
        self.step: float | None = None
        self.inter_delay: float | None = None
        self.set_history: list[tuple[object, float | None, float | None]] = []

    def __call__(self, value: object | None = None) -> object | None:
        if value is None:
            return self.value
        self.value = value
        self.set_history.append((value, self.step, self.inter_delay))
        return None


class _FakeGS200Instrument:
    """Minimal stand-in for qcodes YokogawaGS200 used to pin the ramp path."""

    def __init__(self) -> None:
        self.source_mode = _QcodesParam("CURR")
        self.output = _QcodesParam("off")
        self.current_range = _QcodesParam(0.1)
        self.current = _QcodesParam(0.0)
        self.ramp_mode = _QcodesParam("SOFTWARE")

    def ramp_current(self, *args: object, **kwargs: object) -> None:
        raise AssertionError(
            "program_current must not call YokogawaGS200.ramp_current; the "
            "ramp_mode='SOFTWARE' path is broken in qcodes 0.58."
        )


def test_qcodes_backend_program_current_uses_stepping_without_enabling_output() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument()
    backend._instrument = instrument

    backend.program_current(0.001, step_a=1e-5, delay_s=0.01)

    # Reached target via the parameter's native software stepping.
    assert instrument.current() == 0.001
    assert instrument.current.set_history == [(0.001, 1e-5, 0.01)]
    # Per-step writes are direct (JUMP), and the output is never enabled.
    assert instrument.ramp_mode() == "JUMP"
    assert instrument.output() == "off"
    # Stepping attributes are restored after the ramp.
    assert instrument.current.step is None
    assert instrument.current.inter_delay is None
