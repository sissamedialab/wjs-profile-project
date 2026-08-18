#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

fake_bin="$(mktemp -d)"
# mktemp/rm/cat are included too: PATH is later narrowed to $fake_bin alone
# (not prepended) for the negative case, and assert_failure's own internals
# need mktemp/rm to still resolve.
for tool in git jq pre-commit glab mktemp rm cat; do
  ln -s "$(command -v "$tool")" "$fake_bin/$tool"
done
PATH="$fake_bin:$PATH" assert_success "preflight_check_tools passes when all tools are on PATH" preflight_check_tools
rm "$fake_bin/jq"
# PATH=$fake_bin only (not prepended to $PATH) — otherwise the real jq
# elsewhere on $PATH would still resolve and this could never fail.
PATH="$fake_bin" assert_failure "preflight_check_tools fails when jq is missing" preflight_check_tools
rm -rf "$fake_bin"

sandbox="$(mktemp -d)"
git -C "$sandbox" init -q
git -C "$sandbox" config user.email test@example.test
git -C "$sandbox" config user.name "Test"
git -C "$sandbox" commit -q --allow-empty -m "init"
(cd "$sandbox" && assert_success "preflight_check_clean_tree passes on a clean tree" preflight_check_clean_tree)
echo "dirty" > "$sandbox/untracked.txt"
(cd "$sandbox" && assert_failure "preflight_check_clean_tree fails with an untracked file" preflight_check_clean_tree)
rm -rf "$sandbox"

sandbox="$(mktemp -d)"
git -C "$sandbox" init -q -b wjs-develop
git -C "$sandbox" config user.email test@example.test
git -C "$sandbox" config user.name "Test"
git -C "$sandbox" commit -q --allow-empty -m "init"
(cd "$sandbox" && assert_failure "preflight_check_branches fails when wjs-production is missing" preflight_check_branches)
git -C "$sandbox" branch wjs-production
(cd "$sandbox" && assert_success "preflight_check_branches passes when both branches exist" preflight_check_branches)
rm -rf "$sandbox"

# Real `glab auth status` always exits 0 — the stub mirrors that and
# discriminates via stderr text only, same as gitlab_check_auth's own test.
fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" ]]; then
  if [[ "$4" == "authed-host" ]]; then
    echo "  ✓ Logged in to authed-host as tester" >&2
  else
    echo "x $4 not authenticated with glab." >&2
  fi
  exit 0
fi
exit 1
STUB
chmod +x "$fake_bin/glab"
PATH="$fake_bin:$PATH" assert_success "preflight_check_gitlab_auth passes for an authed host" preflight_check_gitlab_auth "authed-host"
PATH="$fake_bin:$PATH" assert_failure "preflight_check_gitlab_auth fails for an unauthed host" preflight_check_gitlab_auth "other-host"
rm -rf "$fake_bin"

test_summary
exit $?
