#!/bin/sh
# Fetch both bootstrap files from the same explicitly selected release tag.
set -eu
tag=
allow_root=false
previous=
for argument in "$@"; do
    if [ "$previous" = tag ]; then tag=$argument; previous=; continue; fi
    case "$argument" in
        --tag) previous=tag ;;
        --tag=*) tag=${argument#--tag=} ;;
        --allow-root) allow_root=true ;;
    esac
done
case "$tag" in v[0-9]*) ;; *) echo "A pinned --tag is required" >&2; exit 1 ;; esac
case "$tag" in *[!A-Za-z0-9.+-]*) echo "Invalid tag" >&2; exit 1 ;; esac
if [ "$(id -u)" = 0 ] && [ "$allow_root" != true ]; then
    echo "Root requires --allow-root" >&2
    exit 1
fi
command -v uv >/dev/null 2>&1 || { echo "Install uv first" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "Install Python >=3.10 first" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' ||
    { echo "Python >=3.10 required" >&2; exit 1; }
# A dry run uses an in-memory bootstrap: no temporary files or installation writes.
python3 -c '
import sys, urllib.request
tag = sys.argv[1]
url = "https://raw.githubusercontent.com/TED-H-com/bat-agent-connector/" + tag + "/scripts/install_connector.py"
try:
    with urllib.request.urlopen(url, timeout=30) as response:
        code = response.read(1024 * 1024 + 1)
    if len(code) > 1024 * 1024:
        raise ValueError()
except Exception:
    sys.exit("Unable to fetch pinned installer")
sys.argv = [url] + sys.argv[2:]
exec(compile(code, url, "exec"), {"__name__": "__main__"})
' "$tag" "$@"
