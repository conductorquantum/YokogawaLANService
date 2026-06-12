"""FastAPI app for Yokogawa LAN control."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from yokogawa_lan_service.backend import InstrumentBackend
from yokogawa_lan_service.config import InstrumentConfig, ServiceConfig, load_config
from yokogawa_lan_service.manager import (
    CurrentLimitError,
    InstrumentBusyError,
    InstrumentUnavailableError,
    UnknownQubitError,
    YokogawaManager,
)

logger = logging.getLogger("yokogawa_lan_service")


class CurrentRequest(BaseModel):
    target_a: float
    step_a: float | None = None
    delay_s: float | None = None


class OutputRequest(BaseModel):
    state: str


def create_app(
    config: ServiceConfig | str | Path,
    *,
    backend_factory: Callable[[InstrumentConfig], InstrumentBackend] | None = None,
) -> FastAPI:
    """Create the FastAPI service."""

    resolved_config = load_config(config) if isinstance(config, str | Path) else config
    manager = YokogawaManager(resolved_config, backend_factory=backend_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        manager.start()
        try:
            yield
        finally:
            manager.close()

    app = FastAPI(
        title="Yokogawa LAN Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.manager = manager

    @app.get("/health")
    def health():
        return manager.health()

    @app.get("/yokos")
    def yokos():
        return {"items": manager.all_statuses()}

    @app.get("/qubits/{qubit}")
    def qubit_status(qubit: str):
        try:
            return manager.qubit_status(qubit)
        except Exception as exc:
            raise _http_error(exc, context=f"GET /qubits/{qubit}") from exc

    @app.post("/qubits/{qubit}/current")
    def program_current(qubit: str, request: CurrentRequest):
        logger.info(
            "program_current qubit=%s target_a=%s step_a=%s delay_s=%s",
            qubit,
            request.target_a,
            request.step_a,
            request.delay_s,
        )
        try:
            return manager.program_current(
                qubit,
                target_a=request.target_a,
                step_a=request.step_a,
                delay_s=request.delay_s,
            )
        except Exception as exc:
            raise _http_error(
                exc, context=f"POST /qubits/{qubit}/current target_a={request.target_a}"
            ) from exc

    @app.post("/qubits/{qubit}/output")
    def set_output(qubit: str, request: OutputRequest):
        logger.info("set_output qubit=%s state=%s", qubit, request.state)
        try:
            return manager.set_output(qubit, state=request.state)
        except Exception as exc:
            raise _http_error(
                exc, context=f"POST /qubits/{qubit}/output state={request.state}"
            ) from exc

    @app.post("/all/off")
    def all_off():
        try:
            return manager.all_off()
        except Exception as exc:
            raise _http_error(exc, context="POST /all/off") from exc

    @app.post("/refresh")
    def refresh():
        return manager.refresh()

    return app


def _http_error(exc: Exception, *, context: str = "") -> HTTPException:
    where = f" while handling {context}" if context else ""
    if isinstance(exc, UnknownQubitError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, InstrumentUnavailableError):
        logger.warning("Instrument unavailable%s: %s", where, exc)
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, InstrumentBusyError):
        logger.warning("Instrument busy%s: %s", where, exc)
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, CurrentLimitError | ValueError):
        logger.warning("Rejected request%s: %s", where, exc)
        return HTTPException(status_code=400, detail=str(exc))
    logger.exception("Unhandled error%s", where)
    return HTTPException(status_code=500, detail=str(exc))

