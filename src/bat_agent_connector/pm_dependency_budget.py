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


def trim_inventory(value, *, limit=MAX_CAPTURE_BYTES - 32_768):
    """Keep stable prefixes of complete records; omission is durable evidence."""
    value.setdefault("omitted_edges", 0)
    value.setdefault("omitted_hints", 0)
    value.setdefault("omitted_components", 0)
    if _size(value) <= limit:
        return value
    value["parser_reasons"] = sorted(set(value["parser_reasons"]) | {"capture_budget"})
    if "capture" in value:
        value["partial"] = True
        value["capture"]["status"] = "partial"

    def prefix(rows, assign):
        low, high = 0, len(rows)
        while low < high:
            mid = (low + high + 1) // 2
            assign(rows[:mid])
            if _size(value) <= limit:
                low = mid
            else:
                high = mid - 1
        assign(rows[:low])
        return len(rows) - low

    hints = sorted(value["verification_hints"], key=lambda h: h["hint_id"])
    def set_hints(rows):
        value["verification_hints"] = rows
        by_component = {}
        for hint in rows:
            by_component.setdefault(hint["component_id"], []).append(hint)
        for component in value["components"]:
            component["verification_hints"] = by_component.get(component["component_id"], [])
    # Leave room for omission counters growing by a few digits.
    limit -= 128
    value["omitted_hints"] += prefix(hints, set_hints)
    if _size(value) > limit:
        edges = sorted(value["edges"], key=lambda e: e["edge_id"])
        dependencies = value["dependencies"]
        def set_edges(rows):
            value["edges"] = rows
            kinds = {}
            for edge in rows:
                kinds.setdefault(edge["dependency_key"], set()).add(edge["kind"])
            value["dependencies"] = [{**d, "kinds": sorted(kinds[d["key"]])} for d in dependencies if d["key"] in kinds]
        value["omitted_edges"] += prefix(edges, set_edges)
    if _size(value) > limit:
        components = sorted(value["components"], key=lambda c: c["component_id"])
        value["omitted_components"] += prefix(components, lambda rows: value.update(components=rows))
    return bounded(value, limit=limit + 128)
