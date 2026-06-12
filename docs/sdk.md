# Using the SDK

The SDK is the `YokogawaClient` class — a small, dependency-light HTTP client for the running service. It is part of the same `yokogawa-lan-service` package as the server, but importing it does not touch VISA or qcodes, so it is safe to install in any notebook or analysis environment.

## Install

### From PyPI (pip)

Once the package is published to PyPI:

```bash
pip install yokogawa-lan-service
```

Until then, install straight from GitHub:

```bash
pip install git+https://github.com/conductorquantum/YokogawaLANService
```

### Into a uv project

```bash
uv add yokogawa-lan-service
# or from GitHub:
uv add git+https://github.com/conductorquantum/YokogawaLANService
```

### With conda

The package is not on conda-forge, so use conda for the environment and pip for the package:

```bash
conda create -n yoko python=3.12
conda activate yoko
pip install yokogawa-lan-service
# or from GitHub:
pip install git+https://github.com/conductorquantum/YokogawaLANService
```

### From a local clone

```bash
pip install /path/to/yokogawa-lan-service
```

## Connect

```python
from yokogawa_lan_service import YokogawaClient

yoko = YokogawaClient("http://192.0.2.10:8020")
```

`192.0.2.10` is a placeholder — replace it with your lab host's real IP or hostname (the machine running the service, see [Running the server](server.md)).

The constructor takes the service base URL and an optional `timeout_s` (default 10 s):

```python
yoko = YokogawaClient("http://192.0.2.10:8020", timeout_s=5)
```

## Read state

```python
yoko.health()
# {'ok': True, 'connected_count': 20, 'unavailable_count': 0,
#  'instrument_count': 20, 'config': {...}}

yoko.yokos()
# [{'name': 'yoko0', 'qubit': 'Q1', 'serial': '91T624610', 'connected': True,
#   'output': 'off', 'source_mode': 'CURR', 'current_a': 0.0, ...}, ...]

yoko.qubit("Q20")
# {'name': 'yoko19', 'qubit': 'Q20', 'serial': '91T608949', 'connected': True,
#  'output': 'off', 'source_mode': 'CURR', 'current_a': 1e-05, ...}
```

Qubit labels are normalized, so `"Q20"`, `"q20"`, and `"20"` all refer to the same instrument.

## Program current

`set_current` ramps the source to the target current. **It never turns the output on** — readbacks after the call show the output still in its previous state.

```python
yoko.set_current("Q20", 1e-3)                              # ramp with config defaults
yoko.set_current("Q20", 1e-3, step_a=1e-5, delay_s=0.01)   # explicit ramp step/delay
```

- `target_a` must be inside the configured limits (lab default ±25 mA), otherwise the service rejects the request with HTTP 400.
- `step_a`/`delay_s` default to the values in the service config (`default_step_a`, `default_delay_s`).

## Switch output

Enabling output is a separate, explicit call:

```python
yoko.set_output("Q20", "on")
yoko.set_output("Q20", "off")
```

Turn every connected output off in one call (e.g. at the end of a session):

```python
yoko.all_off()
```

## Recover instruments

After power-cycling or replugging a Yokogawa, ask the service to reconnect anything that dropped:

```python
yoko.refresh()   # returns the same payload as health()
```

## Error handling

Every method raises `YokogawaClientError` when the service returns an HTTP error. The exception carries the status code and the service's detail message:

```python
from yokogawa_lan_service import YokogawaClient, YokogawaClientError

yoko = YokogawaClient("http://192.0.2.10:8020")

try:
    yoko.set_current("Q20", 0.03)
except YokogawaClientError as exc:
    print(exc.status_code)  # 400
    print(exc.detail)       # current 0.03 A outside configured range [-0.025, 0.025]
```

| Status | Meaning |
| ------ | ------- |
| 400 | Invalid request — current outside configured limits, bad ramp parameters, or bad output state |
| 404 | Unknown qubit (not in `qubit_mapping`) |
| 409 | Instrument busy — another request holds that Yokogawa's lock; retry shortly |
| 503 | Instrument unavailable — not connected; try `refresh()` first |
| 500 | Unexpected service error — check the service logs |

Connection-level failures (service down, wrong host) raise the underlying `requests.exceptions.ConnectionError` rather than `YokogawaClientError`.

## Typical notebook session

```python
from yokogawa_lan_service import YokogawaClient

yoko = YokogawaClient("http://192.0.2.10:8020")
assert yoko.health()["ok"]

yoko.set_current("Q20", 1e-3)     # program bias; output stays off
yoko.set_output("Q20", "on")      # enable explicitly

# ... run the experiment ...

yoko.set_output("Q20", "off")     # disable when done
```

Every mutating call is written to the service's JSONL audit log, so bias changes are traceable after the fact.
