#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

out="$(version_split '2.0.19.dev2')"
assert_equal "2.0.19" "$(cut -f1 <<< "$out")" "version_split core, dev version"
assert_equal ".dev2" "$(cut -f2 <<< "$out")" "version_split dev, dev version"
assert_equal "" "$(cut -f3 <<< "$out")" "version_split suffix, dev version"

out="$(version_split '2.0.16.dev1+ally1')"
assert_equal "2.0.16" "$(cut -f1 <<< "$out")" "version_split core, dev+suffix version"
assert_equal ".dev1" "$(cut -f2 <<< "$out")" "version_split dev, dev+suffix version"
assert_equal "+ally1" "$(cut -f3 <<< "$out")" "version_split suffix, dev+suffix version"

out="$(version_split '2.0.19')"
assert_equal "2.0.19" "$(cut -f1 <<< "$out")" "version_split core, release version"
assert_equal "" "$(cut -f2 <<< "$out")" "version_split dev, release version"

assert_failure "version_split rejects garbage" version_split "not-a-version"

assert_equal "2.0.19" "$(version_release_from_dev '2.0.19.dev2')" "release_from_dev drops .devN"
assert_equal "2.0.16+ally1" "$(version_release_from_dev '2.0.16.dev1+ally1')" "release_from_dev preserves suffix"

assert_equal "2.0.20.dev1" "$(version_next_dev '2.0.19')" "next_dev bumps patch"
assert_equal "2.0.17.dev1+ally1" "$(version_next_dev '2.0.16+ally1')" "next_dev preserves suffix"

cfg="$(mktemp)"
printf '[metadata]\nname = x\nversion = 2.0.19.dev2\n' > "$cfg"
assert_equal "2.0.19.dev2" "$(version_read_setup_cfg "$cfg")" "reads version from setup.cfg"
rm -f "$cfg"

test_summary
exit $?
