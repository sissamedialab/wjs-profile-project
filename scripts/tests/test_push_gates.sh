#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

bare="$(mktemp -d)"; clone="$(mktemp -d)"
rmdir "$bare" "$clone"
git init -q --bare "$bare"
git clone -q "$bare" "$clone"
git -C "$clone" config user.email test@example.test
git -C "$clone" config user.name "Test"
git -C "$clone" commit -q --allow-empty -m "base"
git -C "$clone" branch -M wjs-production
git -C "$clone" push -q origin wjs-production
git -C "$clone" branch wjs-develop
git -C "$clone" push -q origin wjs-develop

git -C "$clone" checkout -q wjs-develop
git -C "$clone" commit -q --allow-empty -m "Release 2.0.20.dev1"

# real behavioral check: saying n must push nothing
(cd "$clone" && echo n | release_confirm_develop_ready wjs-develop) || true
assert_equal "$(git -C "$bare" rev-parse wjs-develop)" "$(git -C "$clone" rev-parse origin/wjs-develop)" \
  "saying n leaves origin/wjs-develop untouched"

(cd "$clone" && echo y | release_confirm_develop_ready wjs-develop)
status=$?
assert_equal "0" "$status" "saying y to the develop gate succeeds (it only confirms, doesn't push)"

git -C "$clone" checkout -q wjs-production
git -C "$clone" merge -q --no-ff wjs-develop -m "Merge branch 'wjs-develop' into 'wjs-production'"
git -C "$clone" commit -q --allow-empty -m "Release 2.0.19"
git -C "$clone" tag -a v2.0.19 -m "Release 2.0.19"

(cd "$clone" && echo n | release_confirm_and_push_production wjs-production wjs-develop v2.0.19) || true
assert_equal "$(git -C "$bare" tag -l v2.0.19)" "" "saying n to the production gate pushes no tag"

(cd "$clone" && echo y | release_confirm_and_push_production wjs-production wjs-develop v2.0.19)
assert_equal "v2.0.19" "$(git -C "$bare" tag -l v2.0.19)" "saying y pushes the tag to origin"
assert_equal "$(git -C "$clone" rev-parse wjs-production)" "$(git -C "$bare" rev-parse wjs-production)" \
  "saying y pushes wjs-production to origin"
assert_equal "$(git -C "$clone" rev-parse wjs-develop)" "$(git -C "$bare" rev-parse wjs-develop)" \
  "saying y also pushes wjs-develop to origin"

rm -rf "$bare" "$clone"

test_summary
exit $?
