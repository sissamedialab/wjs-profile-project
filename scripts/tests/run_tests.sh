#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

overall_status=0
for test_file in test_*.sh; do
  [[ "$test_file" == "test_helpers.sh" ]] && continue  # shared helpers, not a test file itself
  echo "=== $test_file ==="
  bash "$test_file"
  status=$?
  if [[ $status -ne 0 ]]; then
    overall_status=1
  fi
  echo
done
exit $overall_status
