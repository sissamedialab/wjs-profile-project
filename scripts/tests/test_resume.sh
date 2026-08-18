#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh

# Verifies main()'s resume-after-'n' behavior: saying 'n' at the develop
# gate leaves a fully-prepared-but-unpushed release sitting locally
# (release commit + tag + merge-back + dev-bump all done); re-running must
# detect that and skip straight to the push gates instead of redoing the
# merge/changelog/commit/tag — which would otherwise duplicate the
# CHANGELOG.md section and create a second near-identical release commit.
#
# release_already_prepared_locally() also needs an "ahead of origin" check,
# not just "HEAD looks like a Release commit with an exact tag" — every
# release sits in exactly that state between cycles once pushed, so without
# the ahead-of-origin check every *subsequent* fresh run would falsely
# detect a resume. This fixture's first run exercises exactly that: a
# normal, already-pushed prior release (v2.0.18) must NOT be mistaken for
# an unpushed one.

release_sh_abs="$(cd .. && pwd)/release.sh"

bare="$(mktemp -d)"; work="$(mktemp -d)"
rmdir "$bare" "$work"
git init -q --bare "$bare"
bare_abs="$(cd "$bare" && pwd)"
git clone -q "$bare" "$work"
git -C "$work" config user.email test@example.test
git -C "$work" config user.name "Test"

git -C "$work" commit -q --allow-empty -m "init"
cat > "$work/setup.cfg" <<'CFG'
[metadata]
name = wjs-demo
version = 2.0.18
CFG
git -C "$work" add setup.cfg
git -C "$work" commit -q -m "Release 2.0.18"
git -C "$work" branch -M wjs-production
git -C "$work" tag -a v2.0.18 -m "Release 2.0.18"
git -C "$work" remote set-url origin "git@gitlab.sissamedialab.it:wjs/wjs-profile-project.git"
git -C "$work" config "url.${bare_abs}.insteadOf" "git@gitlab.sissamedialab.it:wjs/wjs-profile-project.git"
git -C "$work" push -q origin wjs-production --tags

git -C "$work" checkout -q -b wjs-develop
cat > "$work/setup.cfg" <<'CFG'
[metadata]
name = wjs-demo
version = 2.0.19.dev1
CFG
git -C "$work" add setup.cfg
git -C "$work" commit -q -m "Release 2.0.19.dev1"
git -C "$work" checkout -q -b feature/2907
git -C "$work" commit -q --allow-empty -m "feature commit 1"
git -C "$work" checkout -q wjs-develop
git -C "$work" merge -q --no-ff feature/2907 -m "$(printf 'Merge branch %s into %s\n\nAdd a view to show wjs-packages versions\n\nSee merge request wjs/wjs-profile-project!1422' "'feature/issue-2907-deploy-view'" "'wjs-develop'")"
git -C "$work" commit -q --allow-empty -m "direct dependency pin, no MR"
git -C "$work" push -q origin wjs-develop

fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  exit 0
fi
if [[ "$1" == "api" ]]; then
  case "$2" in
    *"/merge_requests/1422/related_issues")
      echo '[{"iid":2907,"title":"Create deployment environment 1-5 and enable deployment from CI","web_url":"https://gitlab.sissamedialab.it/wjs/specs/-/issues/2907","references":{"relative":"specs#2907"}}]'
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

# Run 1: say 'n' at the develop gate. Must run the full flow (this is a
# fresh release, not a resume) and leave a prepared-but-unpushed state.
(
  cd "$work"
  export PATH="$fake_bin:$PATH"
  printf 'n\n' | bash "$release_sh_abs"
)
status=$?
assert_equal "1" "$status" "run 1 (n at develop gate) exits 1, having done the full flow"

assert_equal "1" "$(git -C "$work" show wjs-production:CHANGELOG.md | grep -c '^## ')" \
  "exactly one changelog section exists after run 1"
assert_equal "1" "$(git -C "$work" log --oneline wjs-production | grep -c '^[a-f0-9]* Release 2\.0\.19$')" \
  "exactly one 'Release 2.0.19' commit exists on wjs-production after run 1"
assert_equal "" "$(git -C "$bare" tag -l v2.0.19)" "run 1 pushed nothing — v2.0.19 not yet on origin"

# Run 1b: simulate a run that died between the release commit and the tag
# (Ctrl-C, or `git tag -a` aborting under set -e because the tag already
# existed) by dropping the tag run 1 created. The release commit is now
# untagged — the state that used to defeat the resume guard, sending main()
# back through the prepare branch and leaving a duplicate changelog section
# plus a second "Release 2.0.19" commit. It must resume instead, and re-tag.
git -C "$work" tag -d v2.0.19 >/dev/null
run1b_out="$(
  cd "$work"
  export PATH="$fake_bin:$PATH"
  printf 'n\n' | bash "$release_sh_abs" 2>&1
)"
status=$?
assert_equal "1" "$status" "run 1b (untagged release commit, n at develop gate) exits 1"
assert_contains "$run1b_out" "resuming toward the push gates" \
  "an untagged release commit is still recognised as a resume"
assert_equal "1" "$(git -C "$work" show wjs-production:CHANGELOG.md | grep -c '^## ')" \
  "still exactly one changelog section after resuming an untagged release commit"
assert_equal "1" "$(git -C "$work" log --oneline wjs-production | grep -c '^[a-f0-9]* Release 2\.0\.19$')" \
  "no second 'Release 2.0.19' commit was created"
assert_equal "$(git -C "$work" rev-parse wjs-production)" "$(git -C "$work" rev-parse 'v2.0.19^{commit}')" \
  "the missing tag was recreated at the release commit"

# Run 2: say 'y','y'. Must detect the resume and go straight to the push
# gates — not re-merge, not rebuild the changelog, not re-commit/re-tag.
(
  cd "$work"
  export PATH="$fake_bin:$PATH"
  printf 'y\ny\n' | bash "$release_sh_abs"
)
status=$?
assert_equal "0" "$status" "run 2 (y/y) completes successfully by resuming"

assert_equal "1" "$(git -C "$bare" show wjs-production:CHANGELOG.md | grep -c '^## ')" \
  "still exactly one changelog section after resuming and pushing — no duplicate"
assert_equal "1" "$(git -C "$bare" log --oneline wjs-production | grep -c '^[a-f0-9]* Release 2\.0\.19$')" \
  "still exactly one 'Release 2.0.19' commit on origin — resume didn't duplicate it"
assert_equal "v2.0.19" "$(git -C "$bare" tag -l v2.0.19)" "v2.0.19 was pushed to origin by the resumed run"

rm -rf "$bare" "$work" "$fake_bin"

test_summary
exit $?
