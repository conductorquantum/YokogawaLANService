from __future__ import annotations

from pathlib import Path

import pytest

from yokogawa_lan_service.config import ServiceConfig, load_config, normalize_qubit


def test_lab_config_contains_all_twenty_yokogawas() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    config = load_config(repo_root / "site" / "yokogawa.yaml")

    assert len(config.instruments) == 20
    assert config.qubit_mapping["Q20"] == "yoko19"
    yoko19 = config.instruments_by_name["yoko19"]
    assert yoko19.serial == "91T608949"
    assert yoko19.address == "USB0::0x0B21::0x0039::91T608949::INSTR"
    assert config.min_current_a == -0.025
    assert config.max_current_a == 0.025


def test_config_rejects_unknown_instrument_mapping() -> None:
    with pytest.raises(ValueError, match="unknown instruments"):
        ServiceConfig(
            instruments=[],
            qubit_mapping={"Q1": "missing"},
        )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Q1", "Q1"),
        ("1", "Q1"),
        ("q020", "Q20"),
    ],
)
def test_normalize_qubit(raw: str, expected: str) -> None:
    assert normalize_qubit(raw) == expected


def test_normalize_qubit_rejects_invalid_label() -> None:
    with pytest.raises(ValueError, match="invalid qubit"):
        normalize_qubit("QX")

