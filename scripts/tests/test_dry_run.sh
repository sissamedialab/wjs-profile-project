#!/usr/bin/env bash
# --dry-run: simulate the release on 'next-release' and stop — no tag, no push,
# no merge-back, wjs-develop/wjs-production untouched. Sandbox mirrors
# test_full_pipeline.sh's (same fake glab, same synthetic MR history).
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
git -C "$work" push -q origin wjs-develop

production_before="$(git -C "$work" rev-parse wjs-production)"
develop_before="$(git -C "$work" rev-parse wjs-develop)"

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
# real `glab auth status` always exits 0; status is text-only, on stderr.
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  exit 0
fi
if [[ "$1" == "api" && "$2" == *"/merge_requests/1422/related_issues" ]]; then
  echo '[{"iid":2907,"title":"Create deployment environment 1-5 and enable deployment from CI","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907","references":{"relative":"specs#2907"}}]'
  exit 0
fi
echo "unexpected glab call: $*" >&2
exit 2
STUB
chmod +x "$fake_bin/glab"
cat > "$fake_bin/pre-commit" <<'STUB'
#!/usr/bin/env bash
echo "pre-commit must not run on a dry run" >&2
exit 3
STUB
chmod +x "$fake_bin/pre-commit"

output="$(cd "$work" && PATH="$fake_bin:$PATH" bash "$release_sh_abs" --dry-run 2>&1)"
status=$?
assert_equal "0" "$status" "--dry-run completes successfully without any prompt"

assert_equal "next-release" "$(git -C "$work" branch --show-current)" \
  "the checkout is left on the next-release branch"

changelog="$(git -C "$work" show next-release:CHANGELOG.md)"
assert_contains "$changelog" "## [2.0.19]" "next-release's CHANGELOG.md has the simulated version section"
assert_contains "$changelog" "specs#2907: Create deployment environment 1-5 and enable deployment from CI" \
  "next-release's CHANGELOG.md links the resolved issue"
assert_contains "$(git -C "$work" show next-release:setup.cfg)" "version = 2.0.19" \
  "next-release's setup.cfg carries the release version"
assert_contains "$(git -C "$work" log -1 --format=%s next-release)" "Release 2.0.19 (dry run)" \
  "the dry-run commit is labelled as such"
assert_contains "$output" "## [2.0.19]" "the generated section is echoed for review"
assert_contains "$output" "https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/blob/next-release/CHANGELOG.md?ref_type=heads" \
  "the GitLab blob URL for the branch's CHANGELOG.md is printed (host/project taken from origin)"
assert_contains "$output" "git push -f origin next-release" \
  "the push needed to make that URL resolve is spelled out"

assert_equal "$production_before" "$(git -C "$work" rev-parse wjs-production)" "wjs-production is untouched"
assert_equal "$develop_before" "$(git -C "$work" rev-parse wjs-develop)" "wjs-develop is untouched"
assert_equal "" "$(git -C "$work" tag -l v2.0.19)" "no release tag is created"
assert_equal "" "$(git -C "$bare" branch --list next-release)" "next-release is not pushed to origin"
assert_equal "" "$(git -C "$bare" tag -l v2.0.19)" "nothing is pushed to origin"

# second run: the existing next-release is hard-reset, not stacked on top of
git -C "$work" commit -q --allow-empty -m "stale leftover from the previous dry run"
output2="$(cd "$work" && PATH="$fake_bin:$PATH" bash "$release_sh_abs" --dry-run 2>&1)"
status2=$?
assert_equal "0" "$status2" "a second --dry-run succeeds over an existing next-release"
assert_contains "$output2" "resetting existing 'next-release'" "the second run reports the reset"
assert_equal "0" "$(git -C "$work" log --oneline next-release | grep -c 'stale leftover')" \
  "the previous dry run's commits are discarded, not built upon"
assert_equal "1" "$(git -C "$work" show next-release:CHANGELOG.md | grep -c -- '## \[2.0.19\]')" \
  "the changelog section is written once, not stacked"

usage_out="$(cd "$work" && PATH="$fake_bin:$PATH" bash "$release_sh_abs" --help 2>&1)"
assert_contains "$usage_out" "--dry-run" "--help documents --dry-run"
assert_failure "an unknown option is rejected" env PATH="$fake_bin:$PATH" bash "$release_sh_abs" --nope

rm -rf "$bare" "$work" "$fake_bin"

test_summary
exit $?
