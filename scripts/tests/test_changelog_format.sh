#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

assert_equal \
  '- [specs#2923: Fix css cache issues](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2923) — feat: activate wjs-themes context processor (!1423)' \
  "$(changelog_format_entry 'specs#2923' 'Fix css cache issues' 'https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2923' 'feat: activate wjs-themes context processor' '1423')" \
  "changelog_format_entry matches skill's documented format"

assert_equal '- No linked issue — upgrade click to 8.3.3 (!1424)' \
  "$(changelog_format_no_issue_entry 'upgrade click to 8.3.3' '1424')" \
  "changelog_format_no_issue_entry"

assert_equal '- direct dependency bump' \
  "$(changelog_format_plain_commit 'direct dependency bump')" \
  "changelog_format_plain_commit"

assert_equal '## [2.0.19] - 2026-07-17' \
  "$(changelog_section_header '2.0.19' '2026-07-17')" \
  "changelog_section_header"

# prepend into an existing file with a "# Changelog" header
existing="$(mktemp)"
printf '# Changelog\n\n## [2.0.18] - 2026-06-01\n\n- some old entry\n' > "$existing"
changelog_prepend_section "$existing" "$(printf '## [2.0.19] - 2026-07-17\n\n- new entry one\n- new entry two')"
expected="$(printf '# Changelog\n\n## [2.0.19] - 2026-07-17\n\n- new entry one\n- new entry two\n\n## [2.0.18] - 2026-06-01\n\n- some old entry\n')"
assert_equal "$expected" "$(cat "$existing")" "prepend into existing CHANGELOG.md keeps old sections below"
rm -f "$existing"

# prepend into a nonexistent file creates it with the standard header
missing="$(mktemp -u)"
changelog_prepend_section "$missing" "$(printf '## [1.0.0] - 2026-01-01\n\n- first entry')"
expected="$(printf '# Changelog\n\n## [1.0.0] - 2026-01-01\n\n- first entry\n')"
assert_equal "$expected" "$(cat "$missing")" "prepend into missing file creates # Changelog header"
rm -f "$missing"

# a section for a version already present must be refused, not stacked: that
# duplication is what produced two near-identical "Release <v>" commits.
dup="$(mktemp)"
printf '# Changelog\n\n## [2.0.20] - 2026-07-30\n\n- an entry from the interrupted run\n' > "$dup"
before="$(cat "$dup")"
assert_failure "prepending an already-present version is refused" \
  changelog_prepend_section "$dup" "$(printf '## [2.0.20] - 2026-07-30\n\n- regenerated entry')"
assert_equal "$before" "$(cat "$dup")" "refused prepend leaves the changelog untouched"

# ... while a genuinely new version still goes in
changelog_prepend_section "$dup" "$(printf '## [2.0.21] - 2026-08-01\n\n- next release')"
assert_equal "2" "$(grep -c '^## ' "$dup")" "a new version is still prepended above the existing one"
assert_equal '## [2.0.21] - 2026-08-01' "$(sed -n '3p' "$dup")" "the new section lands on top"
rm -f "$dup"

test_summary
exit $?
