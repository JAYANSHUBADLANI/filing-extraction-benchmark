#!/usr/bin/env bash
# Package the project for handoff.
#
# The exclusions are not housekeeping. .env holds a real API key once it is
# filled in, and a zip is the easiest way to hand a secret to somebody by
# accident, so it is excluded explicitly rather than relied on being absent.
# The filing cache is excluded because it is close to a gigabyte and every file
# in it can be refetched from the SEC.
set -euo pipefail

NAME="filing-extraction-benchmark"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$(dirname "$ROOT")/${NAME}.zip"

cd "$(dirname "$ROOT")"
rm -f "$OUT"
COPYFILE_DISABLE=1 zip -rq "$OUT" "$(basename "$ROOT")" \
  -x "*/.env" \
     "*/.env.local" \
     "*/data/cache/*" \
     "*/.scratch/*" \
     "*__pycache__*" \
     "*/.pytest_cache/*" \
     "*/._*" \
     "*.DS_Store" \
     "*/.git/*"

if unzip -l "$OUT" | grep -qE "/\.env$"; then
  echo "refusing to ship: a .env reached the archive" >&2
  rm -f "$OUT"
  exit 1
fi

echo "wrote $OUT"
unzip -l "$OUT" | tail -2
