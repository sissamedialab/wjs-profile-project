#!/usr/bin/env bash
# Minimal assertion helpers for scripts/tests/*.sh — no bats, no external deps.
set -uo pipefail

# File-backed counters: several tests call assert_* inside a `( ... )`
# subshell (e.g. `(cd "$sandbox" && assert_success ...)`), and plain
# shell-variable increments made inside a subshell are discarded when it
# exits. Writing counts to temp files keeps them visible across subshell
# boundaries, so test_summary's final tally is never silently short.
TEST_COUNTERS_DIR="$(mktemp -d)"
TESTS_RUN_FILE="$TEST_COUNTERS_DIR/run"
TESTS_FAILED_FILE="$TEST_COUNTERS_DIR/failed"
echo 0 > "$TESTS_RUN_FILE"
echo 0 > "$TESTS_FAILED_FILE"

_record_run() {
  local n
  n="$(cat "$TESTS_RUN_FILE")"
  echo $((n + 1)) > "$TESTS_RUN_FILE"
}

_record_failure() {
  local n
  n="$(cat "$TESTS_FAILED_FILE")"
  echo $((n + 1)) > "$TESTS_FAILED_FILE"
}

assert_equal() {
  local expected="$1" actual="$2" msg="${3:-assert_equal}"
  _record_run
  if [[ "$expected" != "$actual" ]]; then
    _record_failure
    echo "FAIL: $msg"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    return 1
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" msg="${3:-assert_contains}"
  _record_run
  if [[ "$haystack" != *"$needle"* ]]; then
    _record_failure
    echo "FAIL: $msg"
    echo "  haystack: $haystack"
    echo "  needle:   $needle"
    return 1
  fi
}

assert_success() {
  local msg="$1" out
  shift
  out="$(mktemp)"
  _record_run
  if ! "$@" >"$out" 2>&1; then
    _record_failure
    echo "FAIL: $msg (expected success, command failed)"
    cat "$out"
  fi
  rm -f "$out"
}

assert_failure() {
  local msg="$1" out
  shift
  out="$(mktemp)"
  _record_run
  if "$@" >"$out" 2>&1; then
    _record_failure
    echo "FAIL: $msg (expected failure, command succeeded)"
    cat "$out"
  fi
  rm -f "$out"
}

test_summary() {
  local run failed
  run="$(cat "$TESTS_RUN_FILE")"
  failed="$(cat "$TESTS_FAILED_FILE")"
  echo "---"
  echo "$run run, $failed failed"
  rm -rf "$TEST_COUNTERS_DIR"
  [[ "$failed" -eq 0 ]]
}
