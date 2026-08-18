#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh

assert_equal "a" "a" "assert_equal on equal strings"
assert_contains "hello world" "wor" "assert_contains on matching substring"
assert_success "assert_success on a command that succeeds" true
assert_failure "assert_failure on a command that fails" false

test_summary
exit $?
