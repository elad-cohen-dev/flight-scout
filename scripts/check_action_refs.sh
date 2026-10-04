#!/usr/bin/env bash
# Verify every `uses: owner/repo@ref` in the workflows resolves to a real tag,
# branch or commit — actionlint checks syntax only, not that a version exists
# (2.3.1 shipped astral-sh/setup-uv@v10, a tag that doesn't exist).
set -euo pipefail
status=0
for ref in $(grep -hoE 'uses: *[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+' .github/workflows/*.yml | sed 's/uses: *//' | sort -u); do
  repo=${ref%@*}; ver=${ref##*@}
  if gh api "repos/$repo/commits/$ver" --silent 2>/dev/null; then
    echo "ok       $ref"
  else
    echo "MISSING  $ref"; status=1
  fi
done
exit $status
