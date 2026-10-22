import json

import pytest

from bat_agent_connector.connection_source_descriptor import build_source_descriptor, source_digest


def inputs():
    return {"kind": "bat_profile", "profile_reference": "profile-a",
        "association": {"url": "wss://bat.example:9876/", "fingerprint": "A" * 64},
        "availability_reason": "SOURCE_AVAILABLE", "source_state": {"directory": "b" * 64, "index": "c" * 64, "credential": "d" * 64}}


def test_descriptor_exact_shape_and_canonical_digest():
    args = inputs()
    descriptor = build_source_descriptor(**args)
    assert descriptor == {"reference": "source_" + source_digest(**args), "kind": "bat_profile", "policy_version": 1,
        "source_digest": source_digest(**args), "profile_reference": "profile-a", "association": args["association"],
        "availability_reason": "SOURCE_AVAILABLE"}
    reordered = {**args, "source_state": dict(reversed(list(args["source_state"].items())))}
    assert build_source_descriptor(**reordered) == descriptor
    assert "directory" not in json.dumps(descriptor)
    args["association"]["url"] = "wss://other.example:9876/"
    assert descriptor["association"]["url"] == "wss://bat.example:9876/"


@pytest.mark.parametrize("field", ["kind", "profile_reference", "association", "availability_reason", "source_state"])
def test_every_source_binding_changes_digest(field):
    args = inputs()
    original = source_digest(**args)
    args[field] = {"kind": "staged_secret", "profile_reference": "profile-b",
        "association": {"url": "wss://bat.example:9876/", "fingerprint": "B" * 64},
        "availability_reason": "PROFILE_UNAVAILABLE", "source_state": {"index": "e" * 64}}[field]
    assert source_digest(**args) != original


@pytest.mark.parametrize("patch", [{"kind": "arbitrary"}, {"profile_reference": "x" * 257},
    {"association": {"url": "wss://bat.example:9876/?token=secret", "fingerprint": "A" * 64}},
    {"availability_reason": "private path"}, {"source_state": {"token": "raw-secret"}}])
def test_descriptor_rejects_unbounded_or_untyped_inputs(patch):
    with pytest.raises(ValueError):
        build_source_descriptor(**{**inputs(), **patch})
