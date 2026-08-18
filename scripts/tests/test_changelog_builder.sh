#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

merge_body="Merge branch 'feature/issue-2907-deploy-view' into 'wjs-develop'

Add a view to show wjs-packages versions

See merge request wjs/wjs-profile-project!1422"
assert_equal "1422" "$(changelog_extract_mr_iid "$merge_body")" "extracts MR iid from trailer"
assert_equal "" "$(changelog_extract_mr_iid "just a plain commit, no trailer")" "no iid when there is no trailer"

# --- changelog_build_section, full integration against a real git sandbox --
# Mirrors a real release cycle: wjs-develop diverges from wjs-production, gets
# a prior cycle's real merge-back + dev-bump (must be skipped), then two real
# feature-MR merges and one genuine direct push.

sandbox="$(mktemp -d)"
git -C "$sandbox" init -q -b wjs-develop
git -C "$sandbox" config user.email test@example.test
git -C "$sandbox" config user.name "Test"
git -C "$sandbox" commit -q --allow-empty -m "base"
git -C "$sandbox" branch wjs-production

git -C "$sandbox" checkout -q -b feature/old
git -C "$sandbox" commit -q --allow-empty -m "old feature commit"
git -C "$sandbox" checkout -q wjs-develop
git -C "$sandbox" merge -q --no-ff feature/old -m "$(printf 'Merge branch %s into %s\n\nold feature\n\nSee merge request wjs/wjs-profile-project!1000' "'feature/old'" "'wjs-develop'")"

git -C "$sandbox" checkout -q wjs-production
git -C "$sandbox" merge -q --no-ff wjs-develop -m "Merge branch 'wjs-develop' into 'wjs-production'"
git -C "$sandbox" commit -q --allow-empty -m "Release 2.0.18"
git -C "$sandbox" tag -a v2.0.18 -m "Release 2.0.18"

git -C "$sandbox" checkout -q wjs-develop
git -C "$sandbox" merge -q --no-ff wjs-production -m "Merge branch 'wjs-production' into 'wjs-develop'"
git -C "$sandbox" commit -q --allow-empty -m "Release 2.0.19.dev1"

git -C "$sandbox" checkout -q -b feature/2907
git -C "$sandbox" commit -q --allow-empty -m "feature commit 1"
git -C "$sandbox" checkout -q wjs-develop
git -C "$sandbox" merge -q --no-ff feature/2907 -m "$(printf 'Merge branch %s into %s\n\nAdd a view to show wjs-packages versions\n\nSee merge request wjs/wjs-profile-project!1422' "'feature/issue-2907-deploy-view'" "'wjs-develop'")"

git -C "$sandbox" checkout -q -b chore/click
git -C "$sandbox" commit -q --allow-empty -m "bump click"
git -C "$sandbox" checkout -q wjs-develop
git -C "$sandbox" merge -q --no-ff chore/click -m "$(printf 'Merge branch %s into %s\n\nupgrade click to 8.3.3\n\nSee merge request wjs/wjs-profile-project!1424' "'chore/click-bump'" "'wjs-develop'")"

git -C "$sandbox" commit -q --allow-empty -m "direct dependency pin, no MR"

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
path="$2"
case "$path" in
  *"/merge_requests/1422/related_issues")
    echo '[{"iid":2907,"title":"Create deployment environment 1-5 and enable deployment from CI","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907","references":{"relative":"specs#2907"}}]'
    exit 0
    ;;
  *"/merge_requests/1424/related_issues")
    echo '[]'
    exit 0
    ;;
esac
echo "unexpected call: $*" >&2
exit 2
STUB
chmod +x "$fake_bin/glab"

section="$(cd "$sandbox" && PATH="$fake_bin:$PATH" changelog_build_section "example.test" "wjs/wjs-profile-project" "wjs" "v2.0.18" "wjs-develop" "2.0.19" "2026-07-17")"

expected="$(
  changelog_section_header '2.0.19' '2026-07-17'
  printf '\n'
  changelog_format_plain_commit 'direct dependency pin, no MR'
  changelog_format_no_issue_entry 'upgrade click to 8.3.3' '1424'
  changelog_format_entry 'specs#2907' 'Create deployment environment 1-5 and enable deployment from CI' 'https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907' 'Add a view to show wjs-packages versions' '1422'
)"

assert_equal "$expected" "$section" \
  "full section: plain commit + MR-with-issue + MR-with-no-issue, machinery commits (dev-bump, merge-back) silently skipped"

rm -rf "$sandbox" "$fake_bin"

test_summary
exit $?
