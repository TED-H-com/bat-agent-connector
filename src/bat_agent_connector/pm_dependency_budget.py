"""Dependency-specific storage and projection budgets."""
from .operations import OperationError
from .pm_release_baselines import _size

MAX_READ_BYTES = 2_000_000
# A grouped read can repeat dependency rows; reserve room for declarations and peers.
MAX_CAPTURE_BYTES = 750_000
MAX_DECLARATION_BYTES = 250_000
MAX_COMMAND_BYTES = 512


def bounded(value, *, limit=MAX_READ_BYTES):
    if _size(value) > limit:
        raise OperationError("PM_DEPENDENCIES_READ_TOO_LARGE",
            "Dependency evidence exceeds the byte budget / 相依證據超過資料大小上限", 413)
    return value
