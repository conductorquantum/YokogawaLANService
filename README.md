# Yokogawa LAN Service

Small HTTP service for controlling Yokogawa GS200/GS211 current sources that are connected locally over USB/VISA.

The service keeps VISA local to the machine with the USB cables and exposes only the safe operations needed by lab notebooks:

- list instrument status
- read one qubit flux-bias source
- program current without implicitly enabling output
- explicitly turn output on or off
- turn all outputs off
- refresh missing instruments after USB/power cycling

It does not proxy raw SCPI commands and does not write calibration parameters.

## Documentation

- [Running the server](docs/server.md)
- [Using the SDK](docs/sdk.md)

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- NI-VISA (or another PyVISA backend) on the host with the USB cables

## Run

```bash
./scripts/yokogawa_service.sh
```

or directly:

```bash
uv run yoko-lan serve --config site/yokogawa.yaml --host 0.0.0.0 --port 8020
```

The service must run on the host that sees the Yokogawas through NI-VISA/PyVISA. `0.0.0.0` exposes the service on the lab LAN; use a more restrictive bind address if the lab network is not trusted.

## Configuration

The lab config lives at `site/yokogawa.yaml`. It contains:

- `visa_backend: "@ivi"` for NI-VISA.
- the 20 GS211 serial numbers.
- the qubit mapping, including `Q20: yoko19`.
- current limits, default ramp step, and default ramp delay.
- audit log directory.

Current requests are rejected outside the configured range. The default lab range is:

```yaml
min_current_a: -0.025
max_current_a: 0.025
```

## API

```bash
curl http://localhost:8020/health
curl http://localhost:8020/yokos
curl http://localhost:8020/qubits/Q20
```

Program current while leaving the output in its current state:

```bash
curl -X POST http://localhost:8020/qubits/Q20/current \
  -H 'content-type: application/json' \
  -d '{"target_a": 0.001, "step_a": 0.00001, "delay_s": 0.01}'
```

Turn output on explicitly:

```bash
curl -X POST http://localhost:8020/qubits/Q20/output \
  -H 'content-type: application/json' \
  -d '{"state": "on"}'
```

Turn all connected outputs off:

```bash
curl -X POST http://localhost:8020/all/off
```

Reconnect after restarting a Yokogawa:

```bash
curl -X POST http://localhost:8020/refresh
```

## Python Client

```python
from yokogawa_lan_service import YokogawaClient

yoko = YokogawaClient("http://lab-pc:8020")
print(yoko.health())
print(yoko.qubit("Q20"))

# Output remains off unless explicitly enabled.
yoko.set_current("Q20", 1e-3)
yoko.set_output("Q20", "on")
yoko.set_output("Q20", "off")
```

## Safety Behavior

- Startup and shutdown never change instrument output/current state.
- Current programming never turns an output on.
- Output enable is a separate explicit request.
- Mutating calls are serialized per Yokogawa.
- All mutating calls are written to JSONL audit logs.
- Missing instruments do not prevent startup when degraded mode is enabled.

This service is deliberately narrower than a VISA proxy. Keep raw SCPI control in local debugging sessions.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
```

## Manual Hardware Tests

Run these on the USB-connected host.

1. Confirm VISA sees all 20 instruments:

   ```bash
   uv run python -c 'import pyvisa; print(pyvisa.ResourceManager("@ivi").list_resources())'
   ```

2. Start the service:

   ```bash
   ./scripts/yokogawa_service.sh
   ```

3. Check health:

   ```bash
   curl http://localhost:8020/health
   ```

   Expected: `connected_count` is 20 and `unavailable_count` is 0.

4. Check Q20 maps to serial `91T608949`:

   ```bash
   curl http://localhost:8020/qubits/Q20
   ```

5. Program Q20 current while output remains off:

   ```bash
   curl -X POST http://localhost:8020/qubits/Q20/current \
     -H 'content-type: application/json' \
     -d '{"target_a": 0.001}'
   ```

   Expected: response has `"current_a": 0.001` and `"output": "off"`.

6. Explicitly turn Q20 output on, then off:

   ```bash
   curl -X POST http://localhost:8020/qubits/Q20/output \
     -H 'content-type: application/json' \
     -d '{"state": "on"}'

   curl -X POST http://localhost:8020/qubits/Q20/output \
     -H 'content-type: application/json' \
     -d '{"state": "off"}'
   ```

7. Confirm out-of-range current is rejected:

   ```bash
   curl -i -X POST http://localhost:8020/qubits/Q20/current \
     -H 'content-type: application/json' \
     -d '{"target_a": 0.03}'
   ```

   Expected: HTTP 400.

8. Confirm unknown qubits are rejected:

   ```bash
   curl -i http://localhost:8020/qubits/Q99
   ```

   Expected: HTTP 404.

9. Restart one Yokogawa and recover it:

   ```bash
   curl http://localhost:8020/health
   curl -X POST http://localhost:8020/refresh
   curl http://localhost:8020/health
   ```

10. End with all outputs off:

    ```bash
    curl -X POST http://localhost:8020/all/off
    curl http://localhost:8020/yokos
    ```
