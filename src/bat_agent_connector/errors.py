"""Exception types. Messages must never contain token values (see redact.py)."""


class BatError(Exception):
    """Base class for connector errors."""


class ConfigError(BatError):
    pass


class ConfigNotFound(ConfigError):
    """Neither the selected configuration file nor a fragment beside it exists."""


class TokenUnavailable(ConfigError):
    pass


class FingerprintMismatch(BatError):
    """The server's TLS certificate does not match the pinned SHA-256 fingerprint."""


class AuthError(BatError):
    pass


class ChannelNotAllowed(BatError):
    """The channel is not on the connector allowlist (or is a write channel with writes disabled)."""


class InvokeError(BatError):
    """The host returned invoke-error."""


class BacUnavailable(BatError):
    """The BAC feature this request needs cannot be used here. Nothing was sent."""


class BacOutcomeUnknown(BatError):
    """A BAC write was sent and its reply does not establish whether it was applied."""


class BacReplyError(BatError):
    """A validated BAC error envelope. Details are diagnostics, never decisions."""

    def __init__(self, code: str, message: str, retryable: bool, details: dict) -> None:
        import copy
        import re

        from .redact import redact

        self.code = code
        self.message = re.sub(r"h_[0-9a-f]{32}", "<host-id>", redact(message))[:300]
        self.retryable = retryable
        self.details = copy.deepcopy(details)
        super().__init__(f"{code}: {self.message}")


class InvokeTimeout(BatError):
    pass


class ConnectionLost(BatError):
    pass


class CentralUnavailable(OSError):
    """A request to the local central service that was not sent.

    str() is one sentence that says why and what to do. The exception stays an OSError, and
    each subclass also keeps the type of the error it replaces, so code that handled a
    failed request before handles it the same way now.
    """

    sentence = ""

    def __str__(self) -> str:
        return self.sentence


class WriteRefused(BatError):
    """A write tool refused to run (missing confirm, rate limit, writes disabled, streaming...)."""


class TaskDispatchCancelled(WriteRefused):
    """A journaled task send was cancelled before its BAT frame was submitted."""


class TaskIdentityMismatch(WriteRefused):
    """A durable task reservation conflicts with registry or BAT host identity."""


class ResourceReadOnly(WriteRefused):
    """The resource policy refused a mutation before any BAT frame was sent (see resource_policy.py)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class TaskControlRefused(WriteRefused):
    """The task authority refused a stale or out-of-order session control."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"[{code}] {message}")


class OwnerConflict(RuntimeError, BatError):
    """A live daemon already owns this connector fleet."""

    code = "OWNER_CONFLICT"

    def __init__(self, owner: dict) -> None:
        self.owner = owner
        super().__init__(f"[OWNER_CONFLICT] another task daemon owns this fleet: {owner}")
