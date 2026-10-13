#!/usr/bin/env bash
# Named workflow steps share the original ten-minute test budget, excluding setup.
set -euo pipefail
: "${BATC_CENTRAL_UI_DEADLINE:?Start the shared central UI budget first}"
remaining=$((BATC_CENTRAL_UI_DEADLINE - $(date +%s)))
if ((remaining <= 0)); then
  echo 'Central UI scripts exhausted their shared ten-minute budget.' >&2
  exit 124
fi
exec timeout "${remaining}s" npm run "$@"
