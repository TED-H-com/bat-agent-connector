"""Compare every managed launch fact except its two observation clocks."""

from copy import deepcopy


def stable_launch(reply):
    value = deepcopy(reply)
    for field in ("bundled_runtime", "running_runtime"):
        if isinstance(value.get(field), dict):
            value[field].pop("observed_at", None)
    return value
