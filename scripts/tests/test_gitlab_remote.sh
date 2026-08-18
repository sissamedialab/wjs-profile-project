#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

assert_equal "wjs%2Fwjs-profile-project" "$(url_encode 'wjs/wjs-profile-project')" "url_encode escapes slash"
assert_equal "abc-DEF_123.~" "$(url_encode 'abc-DEF_123.~')" "url_encode leaves unreserved chars alone"

out="$(gitlab_parse_remote_url 'git@gitlab.sissamedialab.it:wjs/wjs-profile-project.git')"
assert_equal "gitlab.sissamedialab.it" "$(cut -f1 <<< "$out")" "ssh remote host"
assert_equal "wjs/wjs-profile-project" "$(cut -f2 <<< "$out")" "ssh remote project path"

out="$(gitlab_parse_remote_url 'https://gitlab.sissamedialab.it/wjs/wjs-profile-project.git')"
assert_equal "gitlab.sissamedialab.it" "$(cut -f1 <<< "$out")" "https remote host"
assert_equal "wjs/wjs-profile-project" "$(cut -f2 <<< "$out")" "https remote project path"

assert_failure "gitlab_parse_remote_url rejects garbage" gitlab_parse_remote_url "not-a-remote-url"

assert_equal "wjs-profile-project" "$(project_short_name 'wjs/wjs-profile-project')" "project_short_name"

# NOTE: real `glab auth status` always exits 0, whether authenticated or not
# — it reports status as text on stderr, not via exit code (verified against
# the actual glab v1.36.0 binary while writing this plan). The stub below
# mirrors that: always exit 0, discriminate only via the printed text.
fake_bin="$(mktemp -d)"
cat > "$fake_bin/glab" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "auth" && "$2" == "status" && "$3" == "--hostname" ]]; then
  if [[ "$4" == "authed-host" ]]; then
    echo "authed-host" >&2
    echo "  ✓ Logged in to authed-host as tester" >&2
  else
    echo "x $4 not authenticated with glab. Run \`glab auth login --hostname $4\` to authenticate" >&2
  fi
  exit 0
fi
exit 1
STUB
chmod +x "$fake_bin/glab"
PATH="$fake_bin:$PATH" assert_success "gitlab_check_auth true for authenticated host" gitlab_check_auth "authed-host"
PATH="$fake_bin:$PATH" assert_failure "gitlab_check_auth false for unauthenticated host" gitlab_check_auth "other-host"
rm -rf "$fake_bin"

test_summary
exit $?
