#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh

release_sh_abs="$(cd .. && pwd)/release.sh"

bare="$(mktemp -d)"; work="$(mktemp -d)"
rmdir "$bare" "$work"
git init -q --bare "$bare"
bare_abs="$(cd "$bare" && pwd)"
git clone -q "$bare" "$work"
git -C "$work" config user.email test@example.test
git -C "$work" config user.name "Test"

cat > "$work/setup.cfg" <<'CFG'
[metadata]
name = wjs-demo
version = 2.0.19.dev2
CFG
git -C "$work" add setup.cfg
git -C "$work" commit -q -m "Release 2.0.19.dev2"
git -C "$work" branch -M wjs-production
git -C "$work" tag -a v2.0.18 -m "Release 2.0.18"
git -C "$work" remote set-url origin "git@gitlab.sissamedialab.it:wjs/wjs-profile-project.git"
git -C "$work" config "url.${bare_abs}.insteadOf" "git@gitlab.sissamedialab.it:wjs/wjs-profile-project.git"
git -C "$work" push -q origin wjs-production --tags
git -C "$work" branch wjs-develop
git -C "$work" checkout -q wjs-develop

git -C "$work" checkout -q -b feature/2907
git -C "$work" commit -q --allow-empty -m "feature commit 1"
git -C "$work" checkout -q wjs-develop
git -C "$work" merge -q --no-ff feature/2907 -m "$(printf 'Merge branch %s into %s\n\nAdd a view to show wjs-packages versions\n\nSee merge request wjs/wjs-profile-project!1422' "'feature/issue-2907-deploy-view'" "'wjs-develop'")"

git -C "$work" checkout -q -b chore/click
git -C "$work" commit -q --allow-empty -m "bump click"
git -C "$work" checkout -q wjs-develop
git -C "$work" merge -q --no-ff chore/click -m "$(printf 'Merge branch %s into %s\n\nupgrade click to 8.3.3\n\nSee merge request wjs/wjs-profile-project!1424' "'chore/click-bump'" "'wjs-develop'")"

git -C "$work" commit -q --allow-empty -m "direct dependency pin, no MR"
git -C "$work" push -q origin wjs-develop

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
# real `glab auth status` always exits 0; status is text-only, on stderr.
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  exit 0
fi
if [[ "$1" == "api" ]]; then
  case "$2" in
    *"/merge_requests/1422/related_issues")
      echo '[{"iid":2907,"title":"Create deployment environment 1-5 and enable deployment from CI","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907","references":{"relative":"specs#2907"}}]'
      exit 0 ;;
    *"/merge_requests/1424/related_issues")
      echo '[]'
      exit 0 ;;
  esac
fi
echo "unexpected glab call: $*" >&2
exit 2
STUB
chmod +x "$fake_bin/glab"
cat > "$fake_bin/pre-commit" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
chmod +x "$fake_bin/pre-commit"

(
  cd "$work"
  export PATH="$fake_bin:$PATH"
  printf 'y\ny\n' | bash "$release_sh_abs"
)
status=$?
assert_equal "0" "$status" "main completes successfully with y/y answers"

assert_contains "$(git -C "$work" show wjs-production:setup.cfg)" "version = 2.0.19" \
  "wjs-production's setup.cfg has the release version (main() ends on wjs-develop, already dev-bumped past it — check the release commit itself, not the live working tree)"

assert_contains "$(git -C "$work" log -1 --format=%s wjs-develop)" "Release 2.0.20.dev1" \
  "wjs-develop carries the next dev-version commit"

assert_equal "v2.0.19" "$(git -C "$bare" tag -l v2.0.19)" "the v2.0.19 tag was pushed to origin"

changelog="$(git -C "$work" show wjs-production:CHANGELOG.md)"
assert_contains "$changelog" "## [2.0.19]" "CHANGELOG.md has the new version section"
assert_contains "$changelog" "specs#2907: Create deployment environment 1-5 and enable deployment from CI" \
  "CHANGELOG.md links the resolved issue"
assert_contains "$changelog" "No linked issue — upgrade click to 8.3.3 (!1424)" \
  "CHANGELOG.md has the no-linked-issue line for the click bump"
assert_contains "$changelog" "- direct dependency pin, no MR" "CHANGELOG.md lists the plain commit"
assert_equal "0" "$(grep -cE 'Release 2\.0\.19\.dev2|Merge branch .wjs-production' <<< "$changelog")" \
  "CHANGELOG.md has no line for the merge-back/dev-bump machinery commits"

rm -rf "$bare" "$work" "$fake_bin"

test_summary
exit $?
