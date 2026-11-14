# BAT Agent Connector API Reference

This document provides a detailed reference for the core Python API of the `bat-agent-connector` library.

## Module: `bat_agent_connector.automation`

This module provides the stable public API for external housekeeping and automation.

### `open_fleet`
```python
@asynccontextmanager
async def open_fleet(
    config: Config | None = None,
    *,
    actor: str = "automation",
    read_only: bool = False,
    idle_timeout: float = 120.0,
) -> AsyncIterator[Fleet]
```
Yields a `Fleet` instance using the supplied or default configuration, and closes it upon exit.
- `config`: A `Config` object. If `None`, `load_config()` is used.
- `actor`: String identifying the caller/actor (default: `"automation"`).
- `read_only`: If `True`, prevents dispatching write operations (default: `False`).
- `idle_timeout`: How long to keep clients alive before closing them (default: `120.0` seconds).

### `jev_for`
```python
def jev_for(fleet: Fleet) -> Jev | None
```
Returns a configured `Jev` judgment layer if enabled and available in the provided fleet configuration; otherwise returns `None`.

---

## Module: `bat_agent_connector.fleet`

Provides connection pooling and management for connected BAT hosts.

### `Fleet`
A small pool of `BatClient` connections, one per configured host.

#### `__init__`
```python
def __init__(
    self,
    config: Config,
    *,
    read_only: bool = False,
    idle_timeout: float = 120.0,
    actor: str = "cli",
) -> None
```
Initializes the Fleet.

#### `writes_enabled`
```python
def writes_enabled(self, host: str) -> bool
```
Returns `True` if writes are enabled for the given `host` in this Fleet.

#### `orchestrate_enabled`
```python
def orchestrate_enabled(self, host: str) -> bool
```
Returns `True` if orchestration is enabled for the given `host`.

#### `any_writes`
```python
@property
def any_writes(self) -> bool
```
Access as `fleet.any_writes`. Returns `True` if writes are enabled on any configured host.

#### `any_orchestrate`
```python
@property
def any_orchestrate(self) -> bool
```
Access as `fleet.any_orchestrate`. Returns `True` if orchestration is enabled on any configured host.

#### `client`
```python
def client(self, name: str) -> BatClient
```
Returns the `BatClient` instance for the specified host `name`. Will lazily initialize if necessary.

#### `close`
```python
async def close(self) -> None
```
Closes all connections in the pool and cancels any background tasks.

---

## Module: `bat_agent_connector.jev`

Optional judgment layer connecting to TypeSafe or OpenRouter APIs.

### `Jev`

#### `ask`
```python
async def ask(self, state: Any, questions: dict) -> dict | None
```
Asks the configured judgment model to answer the provided `questions` based on `state`. Returns a dictionary of answers or `None` if the call fails or falls back.

#### `classify_state`
```python
async def classify_state(self, excerpt: str) -> dict | None
```
Classifies the session state based on the provided `excerpt`.

#### `merge_gate`
```python
async def merge_gate(self, task: str, final_output: str, diff_excerpt: str, tests: str) -> dict | None
```
Evaluates whether a pull request or merge should be allowed based on the given `task` description, `final_output`, `diff_excerpt`, and `tests` context. Returns judgment results or `None`.
