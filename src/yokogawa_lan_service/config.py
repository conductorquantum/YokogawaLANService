"""Configuration models for the Yokogawa LAN service."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

_DEFAULT_VENDOR_ID = "0x0B21"
_DEFAULT_PRODUCT_ID = "0x0039"


class InstrumentConfig(BaseModel):
    """One USB/VISA Yokogawa source."""

    name: str
    serial: str
    address: str = ""

    @model_validator(mode="after")
    def fill_address(self) -> InstrumentConfig:
        if not self.address:
            self.address = (
                f"USB0::{_DEFAULT_VENDOR_ID}::{_DEFAULT_PRODUCT_ID}"
                f"::{self.serial}::INSTR"
            )
        return self


class ServiceConfig(BaseModel):
    """Top-level server configuration."""

    visa_backend: str = "@ivi"
    instruments: list[InstrumentConfig] = Field(default_factory=list)
    qubit_mapping: dict[str, str] = Field(default_factory=dict)
    min_current_a: float = -25e-3
    max_current_a: float = 25e-3
    default_step_a: float = 1e-5
    default_delay_s: float = 1e-2
    audit_log_dir: str = "logs/yokogawa-service"
    allow_degraded_startup: bool = True

    _source_dir: Path | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def validate_config(self) -> ServiceConfig:
        names = [instrument.name for instrument in self.instruments]
        serials = [instrument.serial for instrument in self.instruments]
        if len(names) != len(set(names)):
            raise ValueError("instrument names must be unique")
        if len(serials) != len(set(serials)):
            raise ValueError("instrument serials must be unique")
        unknown = sorted(set(self.qubit_mapping.values()).difference(names))
        if unknown:
            raise ValueError(f"qubit mapping references unknown instruments: {unknown}")
        mapped_instruments = list(self.qubit_mapping.values())
        if len(mapped_instruments) != len(set(mapped_instruments)):
            raise ValueError("each instrument may only appear once in qubit_mapping")
        for qubit in self.qubit_mapping:
            normalize_qubit(qubit)
        if self.min_current_a >= self.max_current_a:
            raise ValueError("min_current_a must be less than max_current_a")
        if self.default_step_a <= 0:
            raise ValueError("default_step_a must be positive")
        if self.default_delay_s < 0:
            raise ValueError("default_delay_s must be non-negative")
        return self

    @property
    def instruments_by_name(self) -> dict[str, InstrumentConfig]:
        return {instrument.name: instrument for instrument in self.instruments}

    @property
    def resolved_audit_log_dir(self) -> Path:
        raw = Path(self.audit_log_dir)
        if raw.is_absolute():
            return raw
        return (self._source_dir or Path.cwd()) / raw


def normalize_qubit(qubit: str) -> str:
    """Return canonical qubit label like ``Q20``."""

    value = str(qubit).strip().upper()
    if not value.startswith("Q"):
        value = f"Q{value}"
    digits = value[1:]
    if not re.fullmatch(r"[0-9]+", digits):
        raise ValueError(f"invalid qubit label: {qubit!r}")
    return f"Q{int(digits)}"


def load_config(path: str | Path) -> ServiceConfig:
    """Load service config from YAML."""

    config_path = Path(path).resolve()
    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path}: expected YAML mapping")
    config = ServiceConfig.model_validate(raw)
    config._source_dir = config_path.parent
    return config


def config_to_public_dict(config: ServiceConfig) -> dict[str, Any]:
    """Return non-secret config fields for diagnostics."""

    return {
        "visa_backend": config.visa_backend,
        "instrument_count": len(config.instruments),
        "qubit_count": len(config.qubit_mapping),
        "min_current_a": config.min_current_a,
        "max_current_a": config.max_current_a,
        "default_step_a": config.default_step_a,
        "default_delay_s": config.default_delay_s,
        "allow_degraded_startup": config.allow_degraded_startup,
        "audit_log_dir": str(config.resolved_audit_log_dir),
    }

