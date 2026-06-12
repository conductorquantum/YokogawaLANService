from __future__ import annotations

import pytest

from yokogawa_lan_service.client import YokogawaClient, YokogawaClientError


class _Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_client_set_current_posts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def request(method, url, *, json=None, timeout=None):
        calls.append((method, url, json, timeout))
        return _Response(200, {"current_a": 0.001})

    monkeypatch.setattr("requests.request", request)

    client = YokogawaClient("http://lab-pc:8020", timeout_s=3)
    assert client.set_current("Q20", 0.001, step_a=1e-5)["current_a"] == 0.001
    assert calls == [
        (
            "POST",
            "http://lab-pc:8020/qubits/Q20/current",
            {"target_a": 0.001, "step_a": 1e-05},
            3,
        )
    ]


def test_client_raises_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def request(method, url, *, json=None, timeout=None):
        return _Response(400, {"detail": "bad current"})

    monkeypatch.setattr("requests.request", request)

    client = YokogawaClient()
    with pytest.raises(YokogawaClientError) as exc_info:
        client.set_output("Q1", "invalid")
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "bad current"

