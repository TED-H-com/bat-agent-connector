# Frozen verbatim from tag v0.2.12, commit 14c62830.
import hashlib

from bat_agent_connector.update_release import require


def package_files(path, *, python_only=False):
    result = {}
    for item in sorted(path.rglob("*")):
        relative = item.relative_to(path)
        require(not item.is_symlink(), "package_symlink_refused")
        if "__pycache__" in relative.parts or item.is_dir() or relative.parts[0] == "skill":
            continue
        if python_only and item.suffix != ".py":
            continue
        require(item.is_file() and item.stat().st_size <= 20 * 1024 * 1024, "package_file_invalid")
        result[relative.as_posix()] = hashlib.sha256(item.read_bytes()).hexdigest()
        require(len(result) <= 5000, "package_file_limit")
    require(result, "package_empty")
    return result

