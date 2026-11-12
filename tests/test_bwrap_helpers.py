"""Synthetic bubblewrap outcomes; no subprocesses or namespaces are created."""

import pytest

from tests.test_pm_native_runtime import skip_if_bwrap_namespace_denied

DENIALS = (
    "bwrap: Creating new namespace failed: Operation not permitted\n",
    "bwrap: No permissions to create a new namespace, likely because the kernel "
    "does not allow non-privileged user namespaces.\n",
)


@pytest.mark.parametrize("stderr", DENIALS)
@pytest.mark.parametrize("as_bytes", [False, True])
def test_namespace_creation_denials_skip(stderr, as_bytes):
    if as_bytes:
        stderr = stderr.encode()
    with pytest.raises(pytest.skip.Exception, match="bubblewrap cannot create a namespace"):
        skip_if_bwrap_namespace_denied(1, stderr)


@pytest.mark.parametrize("returncode,stderr", [
    (1, "bwrap: Can't bind mount: Operation not permitted\n"),
    (1, "bwrap: execvp missing-command: No such file or directory\n"),
    (1, "Operation not permitted\n"),
    (1, "bwrap: Creating new namespace failed: Invalid argument\n"),
    (2, b"unrelated failure\n"),
    (3, b""),  # The read-only canary unexpectedly wrote to the host.
    (0, b""),
    *[(0, message) for message in DENIALS],
    *[(-9, message) for message in DENIALS],
    *[(None, message) for message in DENIALS],
])
def test_other_outcomes_do_not_skip(returncode, stderr):
    skip_if_bwrap_namespace_denied(returncode, stderr)
