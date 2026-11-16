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


class _FakeMeasure:
    def __init__(self, *, present: bool = True, enabled: bool = False) -> None:
        self.present = present
        self.enabled = enabled
        self.calls: list[str] = []

    def state(self) -> bool:
        self.calls.append("state")
        return self.enabled

    def on(self) -> None:
        self.calls.append("on")
        self.enabled = True

    def off(self) -> None:
        self.calls.append("off")
        self.enabled = False


class _FakeGS200Instrument:
    """Minimal stand-in for qcodes YokogawaGS200 used to pin the ramp path."""

    def __init__(
        self,
        *,
        monitor_present: bool = True,
        sense_enabled: bool = False,
        source_mode: str = "CURR",
        output: str = "off",
    ) -> None:
        self.IDN = _QcodesParam("YOKOGAWA,GS211,91T624610,2.02")
        self.source_mode = _QcodesParam(source_mode)
        self.output = _QcodesParam(output)
        self.current_range = _QcodesParam(0.1)
        self.current = _QcodesParam(0.0)
        self.ramp_mode = _QcodesParam("SOFTWARE")
        self.measure = _FakeMeasure(present=monitor_present, enabled=sense_enabled)
        self.voltage_v = 0.123
        self.ask_calls: list[str] = []
        self.ask_error: Exception | None = None

    def ask(self, command: str) -> str:
        self.ask_calls.append(command)
        if self.ask_error is not None:
            raise self.ask_error
        return str(self.voltage_v)

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


def test_qcodes_measure_voltage_enables_and_restores_sense_without_other_writes() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument(output="on")
    instrument.current.value = 0.012
    backend._instrument = instrument

    voltage_v = backend.measure_voltage()

    assert voltage_v == 0.123
    assert instrument.measure.calls == ["state", "on", "off"]
    assert instrument.measure.enabled is False
    assert instrument.ask_calls == [":MEAS?"]
    assert instrument.source_mode.set_history == []
    assert instrument.output.set_history == []
    assert instrument.current.set_history == []
    assert instrument.current_range.set_history == []
    assert instrument.output() == "on"
    assert instrument.current() == 0.012


def test_qcodes_measure_voltage_leaves_enabled_sense_on() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument(output="on", sense_enabled=True)
    backend._instrument = instrument

    assert backend.measure_voltage() == 0.123

    assert instrument.measure.calls == ["state"]
    assert instrument.measure.enabled is True
    assert instrument.ask_calls == [":MEAS?"]


def test_qcodes_measure_voltage_restores_sense_when_measurement_raises() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument(output="on")
    instrument.ask_error = OSError("measurement failed")
    backend._instrument = instrument

    with pytest.raises(OSError, match="measurement failed"):
        backend.measure_voltage()

    assert instrument.measure.calls == ["state", "on", "off"]
    assert instrument.measure.enabled is False
    assert instrument.ask_calls == [":MEAS?"]


@pytest.mark.parametrize(
    ("monitor_present", "source_mode", "output", "message"),
    (
        (False, "CURR", "on", "no /MON monitor option"),
        (True, "VOLT", "on", "requires CURR source mode"),
        (True, "CURR", "off", "requires output on"),
    ),
)
def test_qcodes_measure_voltage_rejects_unsupported_states_without_measurement_traffic(
    monitor_present: bool,
    source_mode: str,
    output: str,
    message: str,
) -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument(
        monitor_present=monitor_present,
        source_mode=source_mode,
        output=output,
    )
    backend._instrument = instrument

    with pytest.raises(ValueError, match=message):
        backend.measure_voltage()

    assert instrument.measure.calls == []
    assert instrument.ask_calls == []


def test_qcodes_snapshot_reports_monitor_without_measuring_voltage() -> None:
    backend = QcodesYokogawaBackend(InstrumentConfig(name="yoko0", serial="91T624610"))
    instrument = _FakeGS200Instrument()
    backend._instrument = instrument

    snapshot = backend.snapshot()

    assert snapshot.monitor_present is True
    assert instrument.measure.calls == []
    assert instrument.ask_calls == []
