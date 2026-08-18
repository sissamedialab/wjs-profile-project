#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

description="Closes #123 and see specs#2923, also Fixes wjs-help#177 and https://gitlab.sissamedialab.it/wjs/specs/-/issues/2999 plus https://gitlab.sissamedialab.it/wjs/wjs-help/-/work_items/55"
refs="$(changelog_extract_issue_refs "$description" "wjs-profile-project" | sort -u)"
expected="$(printf 'specs#2923\nspecs#2999\nwjs-help#177\nwjs-help#55\nwjs-profile-project#123' | sort -u)"
assert_equal "$expected" "$refs" "extracts shorthand, bare, and full-URL issue refs"

assert_equal "" "$(changelog_extract_issue_refs "nothing to see here" "wjs-profile-project")" \
  "no refs in a description with no issue mentions"

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
# args: api "projects/<enc>/issues/<iid>" --hostname <host>
path="$2"
if [[ "$path" == "projects/wjs%2Fspecs/issues/2923" ]]; then
  echo '{"title":"Fix css cache issues","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2923"}'
  exit 0
fi
echo '{"message":"404 Not found"}' >&2
exit 1
STUB
chmod +x "$fake_bin/glab"

PATH="$fake_bin:$PATH" out="$(changelog_resolve_issue_ref "example.test" "wjs" "specs#2923")"
assert_equal "Fix css cache issues${CHANGELOG_FS}https://gitlab.sissamedialab.it/wjs/specs/-/issues/2923" "$out" \
  "resolves a known ref to title + url"

# An issue with an empty title must not shift its URL into the title slot:
# that is the IFS-whitespace collapse CHANGELOG_FS exists to prevent.
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
echo '{"title":"","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/1"}'
STUB
chmod +x "$fake_bin/glab"
PATH="$fake_bin:$PATH" out="$(changelog_resolve_issue_ref "example.test" "wjs" "specs#1")"
IFS="$CHANGELOG_FS" read -r rt ru <<< "$out"
assert_equal "" "$rt" "empty issue title stays in the title slot"
assert_equal "https://gitlab.sissamedialab.it/wjs/specs/-/issues/1" "$ru" \
  "url is not shifted into the title slot when the title is empty"

# Restore the conditional stub (only "specs#2923" resolves) for the
# unresolvable-ref case below — the empty-title stub above exits 0
# unconditionally and would let this assertion pass for the wrong reason.
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
# args: api "projects/<enc>/issues/<iid>" --hostname <host>
path="$2"
if [[ "$path" == "projects/wjs%2Fspecs/issues/2923" ]]; then
  echo '{"title":"Fix css cache issues","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2923"}'
  exit 0
fi
echo '{"message":"404 Not found"}' >&2
exit 1
STUB
chmod +x "$fake_bin/glab"

PATH="$fake_bin:$PATH" assert_failure "unresolvable ref returns failure, not a fabricated result" \
  changelog_resolve_issue_ref "example.test" "wjs" "specs#99999"

rm -rf "$fake_bin"

test_summary
exit $?
