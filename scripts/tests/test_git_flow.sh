#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

# --- git_ff_branch -----------------------------------------------------
# clone_a gets a real local branch literally named wjs-develop from the
# start (not HEAD on some other default-branch name) — a plain
# `git push origin wjs-develop` later requires that local ref to exist.

bare="$(mktemp -d)"; clone_a="$(mktemp -d)"; clone_b="$(mktemp -d)"
rmdir "$bare" "$clone_a" "$clone_b"
git init -q --bare "$bare"
git clone -q "$bare" "$clone_a"
git -C "$clone_a" config user.email test@example.test
git -C "$clone_a" config user.name "Test"
git -C "$clone_a" checkout -q -b wjs-develop
git -C "$clone_a" commit -q --allow-empty -m "first"
git -C "$clone_a" push -q origin wjs-develop

git clone -q "$bare" "$clone_b" -b wjs-develop
git -C "$clone_b" config user.email test@example.test
git -C "$clone_b" config user.name "Test"

git -C "$clone_a" commit -q --allow-empty -m "second"
git -C "$clone_a" push -q origin wjs-develop

(cd "$clone_b" && git fetch -q origin && assert_success "git_ff_branch fast-forwards a behind branch" git_ff_branch wjs-develop)
assert_equal "$(git -C "$clone_a" rev-parse wjs-develop)" "$(git -C "$clone_b" rev-parse wjs-develop)" \
  "git_ff_branch brought clone_b's wjs-develop up to date"

git -C "$clone_b" commit -q --allow-empty -m "local-only divergent commit"
git -C "$clone_a" commit -q --allow-empty -m "origin-only divergent commit"
git -C "$clone_a" push -q origin wjs-develop
(cd "$clone_b" && git fetch -q origin && assert_failure "git_ff_branch refuses to force through real divergence" git_ff_branch wjs-develop)

rm -rf "$bare" "$clone_a" "$clone_b"

# --- git_merge_already_done / git_merge_or_skip -------------------------

sandbox="$(mktemp -d)"
git -C "$sandbox" init -q -b wjs-production
git -C "$sandbox" config user.email test@example.test
git -C "$sandbox" config user.name "Test"
git -C "$sandbox" commit -q --allow-empty -m "base"
git -C "$sandbox" branch wjs-develop
git -C "$sandbox" checkout -q wjs-develop
git -C "$sandbox" commit -q --allow-empty -m "feature work"
develop_tip="$(git -C "$sandbox" rev-parse wjs-develop)"

(cd "$sandbox" && git_merge_or_skip wjs-production wjs-develop "Merge branch 'wjs-develop' into 'wjs-production'")
(cd "$sandbox" && assert_success "git_merge_already_done true right after a real merge" git_merge_already_done "$develop_tip")

before="$(git -C "$sandbox" rev-parse wjs-production)"
(cd "$sandbox" && git_merge_or_skip wjs-production wjs-develop "Merge branch 'wjs-develop' into 'wjs-production'")
after="$(git -C "$sandbox" rev-parse wjs-production)"
assert_equal "$before" "$after" "git_merge_or_skip is a no-op when the merge already happened"

rm -rf "$sandbox"

# --- git_tag_release_or_verify ------------------------------------------

sandbox="$(mktemp -d)"
git -C "$sandbox" init -q -b wjs-production
git -C "$sandbox" config user.email test@example.test
git -C "$sandbox" config user.name "Test"
git -C "$sandbox" commit -q --allow-empty -m "Release 2.0.19"
older="$(git -C "$sandbox" rev-parse HEAD)"
git -C "$sandbox" commit -q --allow-empty -m "Release 2.0.20"

(cd "$sandbox" && assert_success "git_tag_release_or_verify creates a missing tag" \
  git_tag_release_or_verify "v2.0.20" "Release 2.0.20" wjs-production)
assert_equal "$(git -C "$sandbox" rev-parse wjs-production)" "$(git -C "$sandbox" rev-parse 'v2.0.20^{commit}')" \
  "the created tag points at the branch tip"

# Re-running after an interrupted release must accept its own tag rather than
# dying under set -e on "tag already exists".
(cd "$sandbox" && assert_success "git_tag_release_or_verify accepts a tag already at the target" \
  git_tag_release_or_verify "v2.0.20" "Release 2.0.20" wjs-production)

# A tag pointing elsewhere is a human decision, not something to overwrite.
git -C "$sandbox" tag -a "v2.0.21" -m "stale" "$older"
(cd "$sandbox" && assert_failure "git_tag_release_or_verify refuses a tag pointing at another commit" \
  git_tag_release_or_verify "v2.0.21" "Release 2.0.21" wjs-production)
assert_equal "$older" "$(git -C "$sandbox" rev-parse 'v2.0.21^{commit}')" \
  "the mismatching tag is left where it was, not moved"

rm -rf "$sandbox"

test_summary
exit $?
