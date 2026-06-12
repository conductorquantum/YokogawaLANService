"""Small HTTP client for notebooks and scripts."""

from __future__ import annotations

from typing import Any

import requests


class YokogawaClientError(RuntimeError):
    """Raised when the Yokogawa LAN service returns an error."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Yokogawa service error {status_code}: {detail}")


class YokogawaClient:
    """Notebook-friendly client for the Yokogawa LAN service."""

    def __init__(self, base_url: str = "http://127.0.0.1:8020", *, timeout_s: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def yokos(self) -> list[dict[str, Any]]:
        return self._request("GET", "/yokos")["items"]

    def qubit(self, qubit: str) -> dict[str, Any]:
        return self._request("GET", f"/qubits/{qubit}")

    def set_current(
        self,
        qubit: str,
        target_a: float,
        *,
        step_a: float | None = None,
        delay_s: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"target_a": target_a}
        if step_a is not None:
            payload["step_a"] = step_a
        if delay_s is not None:
            payload["delay_s"] = delay_s
        return self._request("POST", f"/qubits/{qubit}/current", json=payload)

    def set_output(self, qubit: str, state: str) -> dict[str, Any]:
        return self._request("POST", f"/qubits/{qubit}/output", json={"state": state})

    def all_off(self) -> dict[str, Any]:
        return self._request("POST", "/all/off")

    def refresh(self) -> dict[str, Any]:
        return self._request("POST", "/refresh")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = requests.request(
            method,
            f"{self.base_url}{path}",
            json=json,
            timeout=self.timeout_s,
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise YokogawaClientError(response.status_code, str(detail))
        return response.json()

