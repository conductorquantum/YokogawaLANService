# Running the Server

The service must run on the host that sees the Yokogawa GS211s through NI-VISA/PyVISA — i.e. the machine with the USB cables. By default it listens on port 8020.

> Examples in these docs use the placeholder address `192.0.2.10`. Replace it with your lab host's real IP or hostname.

## Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- NI-VISA installed (the config uses the `@ivi` PyVISA backend)
- The Yokogawas connected over USB

First-time setup in a clone of this repo:

```bash
uv sync
```

## Start

The launcher script runs the service in the foreground with sensible defaults (config `site/yokogawa.yaml`, bind `0.0.0.0:8020`):

```bash
./scripts/yokogawa_service.sh
```

Or call the CLI directly:

```bash
uv run yoko-lan serve --config site/yokogawa.yaml --host 0.0.0.0 --port 8020
```

`0.0.0.0` exposes the service on the lab LAN/tailnet. Use a more restrictive bind address (e.g. the host's Tailscale IP, or `127.0.0.1` for local-only) if the network is not trusted.

### Options and environment overrides

| Flag | Env var | Default |
| ---- | ------- | ------- |
| `--config PATH` | `YOKOGAWA_SERVICE_CONFIG` | `site/yokogawa.yaml` |
| `--host HOST` | `YOKOGAWA_SERVICE_HOST` | `0.0.0.0` |
| `--port PORT` | `YOKOGAWA_SERVICE_PORT` | `8020` |
| `--reload` | `YOKOGAWA_SERVICE_RELOAD=1` | off |
| `--no-access-log` | `YOKOGAWA_SERVICE_ACCESS_LOG=0` | access log on |

## Verify it is up

```bash
curl http://localhost:8020/health
```

Expected on the lab host: `"ok": true`, `connected_count` 20, `unavailable_count` 0. Interactive API docs are served at `http://localhost:8020/docs`.

If some instruments are missing, the service still starts (degraded mode, `allow_degraded_startup: true` in the config) and reports them as `connected: false`. After fixing cables/power:

```bash
curl -X POST http://localhost:8020/refresh
```

## Configuration

`site/yokogawa.yaml` holds the lab setup:

- `visa_backend: "@ivi"` — NI-VISA
- `instruments` — the 20 GS211 names and serial numbers (VISA addresses are derived from serials)
- `qubit_mapping` — which qubit each instrument biases (e.g. `Q20: yoko19`)
- `min_current_a` / `max_current_a` — hard limits; requests outside are rejected with HTTP 400 (lab default ±25 mA)
- `default_step_a` / `default_delay_s` — ramp defaults when a request omits them
- `audit_log_dir` — where JSONL audit logs go (relative paths resolve against the config file's directory)
- `allow_degraded_startup` — start even when some instruments are missing

## Safety behavior

- Startup and shutdown never change instrument output or current state.
- Programming a current never turns an output on; enabling output is a separate explicit request.
- Mutating calls are serialized per instrument (concurrent requests to the same Yokogawa get HTTP 409).
- Every mutating call is appended to a JSONL audit log (one file per day in `audit_log_dir`).

## Stop

`Ctrl-C` in the foreground terminal. Shutdown closes the VISA sessions without touching output or current state, so stopping the service is always safe mid-experiment.

## Troubleshooting

- **`Could not open VISA library`** — NI-VISA is not installed (or not visible to PyVISA) on this host. The service only works on the USB-connected lab machine.
- **`connected_count` below 20** — check USB cables/power, then `POST /refresh`. List what VISA sees directly:

  ```bash
  uv run python -c 'import pyvisa; print(pyvisa.ResourceManager("@ivi").list_resources())'
  ```

- **Port already in use** — another instance is probably running; check `lsof -i :8020`.
- **Audit trail** — mutating operations (including failures) are in `logs/yokogawa-service/<date>.jsonl`.
