from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from fastapi.testclient import TestClient

from yokogawa_lan_service.api import create_app
from yokogawa_lan_service.backend import FakeYokogawaBackend, InstrumentBackend
from yokogawa_lan_service.config import InstrumentConfig, ServiceConfig


def _config(tmp_path: Path) -> ServiceConfig:
    config = ServiceConfig(
        instruments=[
            InstrumentConfig(name="yoko0", serial="91T624610"),
            InstrumentConfig(name="yoko19", serial="91T608949"),
        ],
        qubit_mapping={"Q1": "yoko0", "Q20": "yoko19"},
        min_current_a=-0.025,
        max_current_a=0.025,
        default_step_a=1e-5,
        default_delay_s=1e-2,
        audit_log_dir="audit",
        allow_degraded_startup=True,
    )
    config._source_dir = tmp_path
    return config


def _factory(
    *,
    missing: set[str] | None = None,
) -> tuple[
    Callable[[InstrumentConfig], InstrumentBackend],
    dict[str, FakeYokogawaBackend],
]:
    missing = missing or set()
    instances: dict[str, FakeYokogawaBackend] = {}

    def build(config: InstrumentConfig) -> InstrumentBackend:
        backend = FakeYokogawaBackend(
            config,
            fail_connect=config.name in missing,
        )
        instances[config.name] = backend
        return backend

    return build, instances


def test_health_and_listing_report_connected_instruments(tmp_path: Path) -> None:
    build, _instances = _factory()
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["connected_count"] == 2
        assert health["unavailable_count"] == 0

        yokos = client.get("/yokos").json()["items"]
        assert [item["name"] for item in yokos] == ["yoko0", "yoko19"]
        assert yokos[1]["qubit"] == "Q20"


def test_program_current_keeps_output_off_and_writes_audit_log(tmp_path: Path) -> None:
    build, instances = _factory()
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        response = client.post(
            "/qubits/Q20/current",
            json={"target_a": 0.012, "step_a": 1e-5, "delay_s": 0.01},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["current_a"] == 0.012
        assert payload["output"] == "off"
        assert instances["yoko19"].output == "off"

    audit_files = list((tmp_path / "audit").glob("*.jsonl"))
    assert len(audit_files) == 1
    event = json.loads(audit_files[0].read_text().splitlines()[0])
    assert event["action"] == "program_current"
    assert event["qubit"] == "Q20"
    assert event["success"] is True


def test_program_current_rejects_out_of_range_current(tmp_path: Path) -> None:
    build, _instances = _factory()
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        response = client.post("/qubits/Q1/current", json={"target_a": 0.03})
        assert response.status_code == 400
        assert "outside configured range" in response.json()["detail"]


def test_unknown_qubit_returns_404(tmp_path: Path) -> None:
    build, _instances = _factory()
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        response = client.get("/qubits/Q99")
        assert response.status_code == 404


def test_degraded_startup_and_refresh(tmp_path: Path) -> None:
    build, instances = _factory(missing={"yoko19"})
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        health = client.get("/health").json()
        assert health["connected_count"] == 1
        assert health["unavailable_count"] == 1

        response = client.post("/qubits/Q20/current", json={"target_a": 0.001})
        assert response.status_code == 503

        instances["yoko19"].fail_connect = False
        refreshed = client.post("/refresh").json()
        assert refreshed["connected_count"] == 2
        assert client.get("/qubits/Q20").json()["connected"] is True


def test_output_requires_explicit_call_and_all_off(tmp_path: Path) -> None:
    build, instances = _factory()
    app = create_app(_config(tmp_path), backend_factory=build)

    with TestClient(app) as client:
        client.post("/qubits/Q20/current", json={"target_a": 0.001}).raise_for_status()
        on = client.post("/qubits/Q20/output", json={"state": "on"})
        assert on.status_code == 200
        assert on.json()["output"] == "on"

        all_off = client.post("/all/off")
        assert all_off.status_code == 200
        assert instances["yoko19"].output == "off"
        assert instances["yoko0"].output == "off"

