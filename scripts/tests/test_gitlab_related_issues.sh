#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

# --- changelog_related_issues_to_entries: pure JSON -> lines -------------

one_issue_json='[{"iid":2907,"title":"Create deployment environment 1-5 and enable deployment from CI","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907","references":{"short":"#2907","relative":"specs#2907","full":"wjs/specs#2907"}}]'
expected="$(changelog_format_entry 'specs#2907' 'Create deployment environment 1-5 and enable deployment from CI' 'https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907' 'Add a view to show wjs-packages versions' '1422')"
actual="$(changelog_related_issues_to_entries "$one_issue_json" 'wjs-profile-project' 'Add a view to show wjs-packages versions' '1422')"
assert_equal "$expected" "$actual" "related_issues with a cross-project issue"

same_project_json='[{"iid":42,"title":"Fix thing","web_url":"https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/42","references":{"short":"#42","relative":"#42","full":"wjs/wjs-profile-project#42"}}]'
expected="$(changelog_format_entry 'wjs-profile-project#42' 'Fix thing' 'https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/42' 'fix: thing' '10')"
actual="$(changelog_related_issues_to_entries "$same_project_json" 'wjs-profile-project' 'fix: thing' '10')"
assert_equal "$expected" "$actual" "related_issues with a same-project issue (relative ref has no project prefix)"

empty_json='[]'
expected="$(changelog_format_no_issue_entry 'upgrade click to 8.3.3' '1424')"
actual="$(changelog_related_issues_to_entries "$empty_json" 'wjs-profile-project' 'upgrade click to 8.3.3' '1424')"
assert_equal "$expected" "$actual" "related_issues empty array yields the no-linked-issue line"

# --- empty first field must not shift the later fields -------------------
# `references.relative` null (or `references` missing entirely — jq yields
# null for both) used to collapse under IFS=$'\t', producing
# "- [<title>: <url>]() — ..." : ref slot holding the title, URL slot empty.

null_relative_json='[{"iid":2957,"title":"Fix thing","web_url":"https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/2957","references":{"short":"#2957","relative":null,"full":null}}]'
expected="$(changelog_format_entry 'wjs-profile-project#2957' 'Fix thing' 'https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/2957' 'fix: thing' '11')"
actual="$(changelog_related_issues_to_entries "$null_relative_json" 'wjs-profile-project' 'fix: thing' '11')"
assert_equal "$expected" "$actual" "null references.relative falls back to #iid, url slot intact"

no_references_json='[{"iid":42,"title":"Fix other thing","web_url":"https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/42"}]'
expected="$(changelog_format_entry 'wjs-profile-project#42' 'Fix other thing' 'https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/42' 'fix: other' '12')"
actual="$(changelog_related_issues_to_entries "$no_references_json" 'wjs-profile-project' 'fix: other' '12')"
assert_equal "$expected" "$actual" "missing references key falls back to #iid, url slot intact"

# Neither ref field available: fall back to web_url for both project and iid
# rather than silently sliding the URL one slot to the left.
no_ref_at_all_json='[{"title":"Untraceable","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/9"}]'
expected="$(changelog_format_entry 'specs#9' 'Untraceable' 'https://gitlab.sissamedialab.it/wjs/specs/-/issues/9' 'chore: x' '13')"
actual="$(changelog_related_issues_to_entries "$no_ref_at_all_json" 'wjs-profile-project' 'chore: x' '13')"
assert_equal "$expected" "$actual" "project and iid both derived from web_url when no ref field is usable"

# --- cross-project issues must not be labelled with this project ---------
# GitLab returns references.relative relative to the issue's own project, so a
# cross-project issue also comes back as a bare "#iid". The entry ref must
# follow web_url, not own_project_short.

cross_project_bare_relative_json='[{"iid":2957,"title":"Server error trying to arrange JCAP home page","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2957","references":{"short":"#2957","relative":"#2957","full":"wjs/specs#2957"}}]'
expected="$(changelog_format_entry 'specs#2957' 'Server error trying to arrange JCAP home page' 'https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2957' 'fix: fix home page error when plugin configuration is missing' '1438')"
actual="$(changelog_related_issues_to_entries "$cross_project_bare_relative_json" 'wjs-profile-project' 'fix: fix home page error when plugin configuration is missing' '1438')"
assert_equal "$expected" "$actual" "cross-project issue with a bare #iid relative ref is attributed to its own project"

own_work_item_json='[{"iid":290,"title":"Use activity page / log_message to notify editore disabling themselves","web_url":"https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/290","references":{"short":"#290","relative":"#290","full":"wjs/wjs-profile-project#290"}}]'
expected="$(changelog_format_entry 'wjs-profile-project#290' 'Use activity page / log_message to notify editore disabling themselves' 'https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/work_items/290' 'feat: block manual editor assignment if not enabled' '1435')"
actual="$(changelog_related_issues_to_entries "$own_work_item_json" 'wjs-profile-project' 'feat: block manual editor assignment if not enabled' '1435')"
assert_equal "$expected" "$actual" "same-project work item keeps this project's short name"

subgroup_json='[{"iid":7,"title":"Nested","web_url":"https://gitlab.sissamedialab.it/wjs/sub/deep/-/issues/7","references":{"relative":"#7"}}]'
expected="$(changelog_format_entry 'deep#7' 'Nested' 'https://gitlab.sissamedialab.it/wjs/sub/deep/-/issues/7' 'chore: y' '14')"
actual="$(changelog_related_issues_to_entries "$subgroup_json" 'wjs-profile-project' 'chore: y' '14')"
assert_equal "$expected" "$actual" "subgroup-nested project resolves to the segment before /-/"

# --- changelog_project_short_from_issue_url ------------------------------

assert_equal "specs" "$(changelog_project_short_from_issue_url 'https://gitlab.sissamedialab.it/wjs/specs/-/issues/1')" \
  "project short name from an issues URL"
assert_equal "specs" "$(changelog_project_short_from_issue_url 'https://gitlab.sissamedialab.it/wjs/specs/-/work_items/1')" \
  "project short name from a work_items URL"
assert_equal "" "$(changelog_project_short_from_issue_url 'https://example.test/nothing/useful')" \
  "URL without /-/ yields no project name instead of garbage"

# --- gitlab_related_issues_json: fake glab stub --------------------------

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
# args: api "projects/<enc>/merge_requests/<iid>/related_issues" --hostname <host>
path="$2"
if [[ "$path" == *"/merge_requests/1/related_issues" ]]; then
  echo '[{"iid":1,"title":"t","web_url":"https://example.test/issues/1","references":{"relative":"#1"}}]'
  exit 0
elif [[ "$path" == *"/merge_requests/2/related_issues" ]]; then
  echo '{"message":"404 Not found"}' >&2
  exit 1
fi
echo "unexpected call: $*" >&2
exit 2
STUB
chmod +x "$fake_bin/glab"

PATH="$fake_bin:$PATH" assert_success "gitlab_related_issues_json succeeds when supported" \
  gitlab_related_issues_json "example.test" "wjs/demo" "1"

PATH="$fake_bin:$PATH" out="$(gitlab_related_issues_json "example.test" "wjs/demo" "1" 2>/dev/null)"
assert_contains "$out" '"iid":1' "gitlab_related_issues_json returns the JSON body on stdout"

set +e
PATH="$fake_bin:$PATH" gitlab_related_issues_json "example.test" "wjs/demo" "2" >/dev/null 2>/dev/null
status=$?
set -e
assert_equal "44" "$status" "gitlab_related_issues_json reports 44 on a 404"

rm -rf "$fake_bin"

test_summary
exit $?
