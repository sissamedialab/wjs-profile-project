# `scripts/release.sh` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/release.sh`, a standalone bash script that performs the `wjs-develop` → `wjs-production` release flow currently described (and manually improvised) by `.claude/skills/releasing-wjs-python-package/SKILL.md` — merge, version bump, changelog from GitLab MRs/issues, tag, merge back, dev-version bump — authenticating to GitLab itself via `glab`, with no Claude/MCP dependency.

**Architecture:** One shippable file, `scripts/release.sh`, containing every function (version parsing, changelog building, GitLab wrapper, git-flow helpers, preflight, `main`), guarded so the file can be `source`d for testing without auto-running `main`. A parallel `scripts/tests/` suite exercises every function in isolation (fake `glab` stub on `PATH`, real `git` against throwaway local repos, no network) plus one full-pipeline integration test built from this repo's own real `v2.0.19` release data.

**Tech Stack:** Bash, `git`, `glab` CLI, `jq`. No Python, no bats — tests are plain bash using a small custom assertion harness (`scripts/tests/test_helpers.sh`), matching the design's "no Python/other runtime needed" constraint.

## Global Constraints

These apply to every task below; do not re-derive them per task.

- Single file: all logic lives in `scripts/release.sh`. Do not split into `lib/*.sh` — the file must be copyable verbatim into 4 sibling repos later.
- No GitLab MCP tools, no Python — `git` + `glab` + `jq` + `pre-commit` only.
- No subcommands/flags for normal use; the two push gates are the only prompts.
- No GitLab Release object creation — tag-only (`git tag -a v<version>`).
- No towncrier / changelog-fragment adoption.
- Release commit message: exactly `Release <version>` — never `Release v<version>`, never "Bump version...".
- Tag: `v<version>` (annotated, `git tag -a`) — the `v` prefix appears **only** on the tag, never in commit messages.
- Version logic only ever strips/reapplies `.devN`; never bumps minor/major itself (that already happened when `.dev1` was appended after the previous release).
- A `+<suffix>` local version marker (e.g. `+ally1`) is preserved unchanged through both the release version and the next dev version.
- Unresolvable issue references become `- No linked issue — <MR title> (!<mr-iid>)`. Never fabricate a URL or project path.
- Every step must check whether its effect already happened before acting (resumability) — re-running after a manual conflict resolution or a `n` at a confirm prompt must continue, not redo or duplicate.
- Both pushes are gated behind explicit `y`/`n` prompts; saying `n` at either must leave the local repo untouched (nothing pushed) and abort cleanly.
- Divergence between local and `origin` on `wjs-develop`/`wjs-production` aborts with a status dump — never force anything (no `-X ours`/`-X theirs`, no force-push).

---

## File Structure

- `scripts/release.sh` — the single shippable file. Sections, in order: version helpers → changelog formatting helpers → GitLab remote/auth helpers → GitLab data-fetch helpers (`related_issues` + description fallback) → changelog section builder → git-flow helpers → preflight → `main` → source-guard footer.
- `scripts/tests/test_helpers.sh` — assertion primitives shared by every test file.
- `scripts/tests/test_version.sh` — version parsing/bumping.
- `scripts/tests/test_changelog_format.sh` — pure changelog formatting + `CHANGELOG.md` prepend logic.
- `scripts/tests/test_gitlab_remote.sh` — remote URL parsing, project short name, auth check, URL-encoding.
- `scripts/tests/test_gitlab_related_issues.sh` — `related_issues` fetch + 404 probe + JSON→changelog-lines, via a fake `glab` stub.
- `scripts/tests/test_changelog_fallback.sh` — description-regex ref extraction + per-ref resolution fallback.
- `scripts/tests/test_changelog_builder.sh` — wires the above into full per-MR changelog-section building, against a throwaway git sandbox + fake `glab`.
- `scripts/tests/test_preflight.sh` — tool/clean-tree/branch-existence checks.
- `scripts/tests/test_git_flow.sh` — fast-forward sync and merge-with-resumability, against real throwaway git sandboxes.
- `scripts/tests/test_push_gates.sh` — the two interactive confirm+push steps, against a local bare-repo "origin".
- `scripts/tests/test_full_pipeline.sh` — end-to-end run of `main` against a synthetic sandbox modeled on this repo's real `88b21b2d..6e549f08` range (fake `glab`, fake `pre-commit`, local bare "origin").
- `scripts/tests/test_resume.sh` — Task 13/14: `main` run twice (`n` then `y`/`y`) against a bare-repo "origin" to prove a clean abort resumes instead of re-preparing the release, including the case where the tag itself is missing.
- `scripts/tests/run_tests.sh` — runs every `test_*.sh`, aggregates pass/fail, non-zero exit if anything failed.

---

### Task 1: Test harness scaffolding

**Files:**
- Create: `scripts/tests/test_helpers.sh`
- Create: `scripts/tests/run_tests.sh`
- Create: `scripts/tests/test_00_harness_selfcheck.sh`

**Interfaces:**
- Produces: `assert_equal "$expected" "$actual" "$msg"`, `assert_contains "$haystack" "$needle" "$msg"`, `assert_success "$msg" cmd args...`, `assert_failure "$msg" cmd args...`, `test_summary` (prints a `N run, M failed` line, returns non-zero if `M > 0`). All later test files source this.

- [ ] **Step 1: Write `scripts/tests/test_helpers.sh`**

```bash
#!/usr/bin/env bash
# Minimal assertion helpers for scripts/tests/*.sh — no bats, no external deps.
set -uo pipefail

TESTS_RUN=0
TESTS_FAILED=0

assert_equal() {
  local expected="$1" actual="$2" msg="${3:-assert_equal}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [[ "$expected" != "$actual" ]]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: $msg"
    echo "  expected: $expected"
    echo "  actual:   $actual"
    return 1
  fi
}

assert_contains() {
  local haystack="$1" needle="$2" msg="${3:-assert_contains}"
  TESTS_RUN=$((TESTS_RUN + 1))
  if [[ "$haystack" != *"$needle"* ]]; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: $msg"
    echo "  haystack: $haystack"
    echo "  needle:   $needle"
    return 1
  fi
}

assert_success() {
  local msg="$1" out
  shift
  out="$(mktemp)"
  TESTS_RUN=$((TESTS_RUN + 1))
  if ! "$@" >"$out" 2>&1; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: $msg (expected success, command failed)"
    cat "$out"
  fi
  rm -f "$out"
}

assert_failure() {
  local msg="$1" out
  shift
  out="$(mktemp)"
  TESTS_RUN=$((TESTS_RUN + 1))
  if "$@" >"$out" 2>&1; then
    TESTS_FAILED=$((TESTS_FAILED + 1))
    echo "FAIL: $msg (expected failure, command succeeded)"
    cat "$out"
  fi
  rm -f "$out"
}

test_summary() {
  echo "---"
  echo "$TESTS_RUN run, $TESTS_FAILED failed"
  [[ "$TESTS_FAILED" -eq 0 ]]
}
```

- [ ] **Step 2: Write `scripts/tests/run_tests.sh`**

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

overall_status=0
for test_file in test_*.sh; do
  [[ "$test_file" == "test_helpers.sh" ]] && continue  # shared helpers, not a test file itself
  echo "=== $test_file ==="
  bash "$test_file"
  status=$?
  if [[ $status -ne 0 ]]; then
    overall_status=1
  fi
  echo
done
exit $overall_status
```

- [ ] **Step 3: Write `scripts/tests/test_00_harness_selfcheck.sh`**

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh

assert_equal "a" "a" "assert_equal on equal strings"
assert_contains "hello world" "wor" "assert_contains on matching substring"
assert_success "assert_success on a command that succeeds" true
assert_failure "assert_failure on a command that fails" false

test_summary
exit $?
```

- [ ] **Step 4: Make the test files executable and run them**

```bash
chmod +x scripts/tests/run_tests.sh scripts/tests/test_00_harness_selfcheck.sh
bash scripts/tests/run_tests.sh
```

Expected output ends with:
```
=== test_00_harness_selfcheck.sh ===
---
4 run, 0 failed

```
and exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/tests/test_helpers.sh scripts/tests/run_tests.sh scripts/tests/test_00_harness_selfcheck.sh
git commit -m "test: add bash test harness scaffolding for release script"
```

---

### Task 2: Version helpers

**Files:**
- Create: `scripts/release.sh`
- Create: `scripts/tests/test_version.sh`

**Interfaces:**
- Consumes: nothing (first functions in the file).
- Produces: `version_split <raw>` (prints `core<TAB>dev<TAB>suffix`, returns 1 if unparsable), `version_release_from_dev <raw>` (prints core+suffix), `version_next_dev <release_version>` (prints core-with-patch+1 + `.dev1` + suffix), `version_read_setup_cfg <path>` (prints the raw `version =` value). Later tasks call all four.

- [ ] **Step 1: Write the failing tests**

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

out="$(version_split '2.0.19.dev2')"
assert_equal "2.0.19" "$(cut -f1 <<< "$out")" "version_split core, dev version"
assert_equal ".dev2" "$(cut -f2 <<< "$out")" "version_split dev, dev version"
assert_equal "" "$(cut -f3 <<< "$out")" "version_split suffix, dev version"

out="$(version_split '2.0.16.dev1+ally1')"
assert_equal "2.0.16" "$(cut -f1 <<< "$out")" "version_split core, dev+suffix version"
assert_equal ".dev1" "$(cut -f2 <<< "$out")" "version_split dev, dev+suffix version"
assert_equal "+ally1" "$(cut -f3 <<< "$out")" "version_split suffix, dev+suffix version"

out="$(version_split '2.0.19')"
assert_equal "2.0.19" "$(cut -f1 <<< "$out")" "version_split core, release version"
assert_equal "" "$(cut -f2 <<< "$out")" "version_split dev, release version"

assert_failure "version_split rejects garbage" version_split "not-a-version"

assert_equal "2.0.19" "$(version_release_from_dev '2.0.19.dev2')" "release_from_dev drops .devN"
assert_equal "2.0.16+ally1" "$(version_release_from_dev '2.0.16.dev1+ally1')" "release_from_dev preserves suffix"

assert_equal "2.0.20.dev1" "$(version_next_dev '2.0.19')" "next_dev bumps patch"
assert_equal "2.0.17.dev1+ally1" "$(version_next_dev '2.0.16+ally1')" "next_dev preserves suffix"

cfg="$(mktemp)"
printf '[metadata]\nname = x\nversion = 2.0.19.dev2\n' > "$cfg"
assert_equal "2.0.19.dev2" "$(version_read_setup_cfg "$cfg")" "reads version from setup.cfg"
rm -f "$cfg"

test_summary
exit $?
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_version.sh`
Expected: fails immediately with `../release.sh: No such file or directory` (the file doesn't exist yet).

- [ ] **Step 3: Write `scripts/release.sh`**

```bash
#!/usr/bin/env bash
# Release wjs-develop -> wjs-production for a wjs-* Python package:
# merge, version bump, changelog from GitLab MRs/issues, tag, merge back,
# dev-version bump. See docs/superpowers/specs/2026-07-17-release-script-design.md.
set -euo pipefail

# --- version helpers ------------------------------------------------------

version_split() {
  local raw="$1"
  if [[ "$raw" =~ ^([0-9]+\.[0-9]+\.[0-9]+)(\.dev[0-9]+)?(\+[A-Za-z0-9]+)?$ ]]; then
    printf '%s\t%s\t%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
  else
    echo "version_split: cannot parse version '$raw'" >&2
    return 1
  fi
}

version_release_from_dev() {
  # cut, not `read` with IFS=$'\t': tab is an IFS-whitespace character, so
  # bash's `read` silently collapses an empty middle field (dev-less
  # versions like "2.0.16+ally1") into the next field, dropping the suffix.
  # Verified live against bash's actual behavior while writing this plan.
  local raw="$1" split core suffix
  split="$(version_split "$raw")"
  core="$(cut -f1 <<< "$split")"
  suffix="$(cut -f3 <<< "$split")"
  printf '%s%s\n' "$core" "$suffix"
}

version_next_dev() {
  local release_version="$1" split core suffix major minor patch
  split="$(version_split "$release_version")"
  core="$(cut -f1 <<< "$split")"
  suffix="$(cut -f3 <<< "$split")"
  IFS='.' read -r major minor patch <<< "$core"
  printf '%s.%s.%s.dev1%s\n' "$major" "$minor" "$((patch + 1))" "$suffix"
}

version_read_setup_cfg() {
  local setup_cfg="$1" line
  line="$(grep -E '^version[[:space:]]*=' "$setup_cfg" | head -n1)"
  [[ -n "$line" ]] || { echo "version_read_setup_cfg: no 'version =' line in $setup_cfg" >&2; return 1; }
  printf '%s\n' "${line#*=}" | xargs
}

# --- entrypoint -------------------------------------------------------------

main() {
  echo "release.sh: not yet implemented" >&2
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_version.sh`
Expected: `13 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
chmod +x scripts/release.sh
git add scripts/release.sh scripts/tests/test_version.sh
git commit -m "feat: add version parsing/bumping helpers to release.sh"
```

---

### Task 3: Changelog formatting + `CHANGELOG.md` prepend

**Files:**
- Modify: `scripts/release.sh` (insert new section after the version helpers, before `main`)
- Create: `scripts/tests/test_changelog_format.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: `changelog_format_entry <project_and_iid> <issue_title> <issue_url> <mr_title> <mr_iid>`, `changelog_format_no_issue_entry <mr_title> <mr_iid>`, `changelog_format_plain_commit <subject>`, `changelog_section_header <version> <date>`, `changelog_prepend_section <changelog_path> <section_text>`. Task 7 (changelog builder) and Task 11 (main wiring) call all five.

- [ ] **Step 1: Write the failing tests**

```bash
#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./test_helpers.sh
source ../release.sh

assert_equal \
  '- [specs#2923: Fix css cache issues](https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2923) — feat: activate wjs-themes context processor (!1423)' \
  "$(changelog_format_entry 'specs#2923' 'Fix css cache issues' 'https://gitlab.sissamedialab.it/wjs/specs/-/work_items/2923' 'feat: activate wjs-themes context processor' '1423')" \
  "changelog_format_entry matches skill's documented format"

assert_equal '- No linked issue — upgrade click to 8.3.3 (!1424)' \
  "$(changelog_format_no_issue_entry 'upgrade click to 8.3.3' '1424')" \
  "changelog_format_no_issue_entry"

assert_equal '- direct dependency bump' \
  "$(changelog_format_plain_commit 'direct dependency bump')" \
  "changelog_format_plain_commit"

assert_equal '## [2.0.19] - 2026-07-17' \
  "$(changelog_section_header '2.0.19' '2026-07-17')" \
  "changelog_section_header"

# prepend into an existing file with a "# Changelog" header
existing="$(mktemp)"
printf '# Changelog\n\n## [2.0.18] - 2026-06-01\n\n- some old entry\n' > "$existing"
changelog_prepend_section "$existing" "$(printf '## [2.0.19] - 2026-07-17\n\n- new entry one\n- new entry two')"
expected="$(printf '# Changelog\n\n## [2.0.19] - 2026-07-17\n\n- new entry one\n- new entry two\n\n## [2.0.18] - 2026-06-01\n\n- some old entry\n')"
assert_equal "$expected" "$(cat "$existing")" "prepend into existing CHANGELOG.md keeps old sections below"
rm -f "$existing"

# prepend into a nonexistent file creates it with the standard header
missing="$(mktemp -u)"
changelog_prepend_section "$missing" "$(printf '## [1.0.0] - 2026-01-01\n\n- first entry')"
expected="$(printf '# Changelog\n\n## [1.0.0] - 2026-01-01\n\n- first entry\n')"
assert_equal "$expected" "$(cat "$missing")" "prepend into missing file creates # Changelog header"
rm -f "$missing"

test_summary
exit $?
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_changelog_format.sh`
Expected: FAILs with `changelog_format_entry: command not found` (function doesn't exist yet).

- [ ] **Step 3: Insert the changelog-formatting section into `scripts/release.sh`**

Insert immediately after the version-helpers section (before the `# --- entrypoint ---` comment):

```bash
# --- changelog formatting ---------------------------------------------------

changelog_format_entry() {
  local project_and_iid="$1" issue_title="$2" issue_url="$3" mr_title="$4" mr_iid="$5"
  printf -- '- [%s: %s](%s) — %s (!%s)\n' "$project_and_iid" "$issue_title" "$issue_url" "$mr_title" "$mr_iid"
}

changelog_format_no_issue_entry() {
  local mr_title="$1" mr_iid="$2"
  printf -- '- No linked issue — %s (!%s)\n' "$mr_title" "$mr_iid"
}

changelog_format_plain_commit() {
  local subject="$1"
  printf -- '- %s\n' "$subject"
}

changelog_section_header() {
  local version="$1" date="$2"
  printf '## [%s] - %s\n' "$version" "$date"
}

changelog_prepend_section() {
  local path="$1" section="$2" tmp
  tmp="$(mktemp)"
  if [[ ! -f "$path" ]]; then
    printf '# Changelog\n\n%s\n' "$section" > "$path"
    return 0
  fi
  if head -n1 "$path" | grep -qx '# Changelog'; then
    {
      printf '# Changelog\n\n%s\n\n' "$section"
      tail -n +2 "$path" | sed '/./,$!d'
    } > "$tmp"
  else
    {
      printf '%s\n\n' "$section"
      cat "$path"
    } > "$tmp"
  fi
  mv "$tmp" "$path"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_changelog_format.sh`
Expected: `6 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_changelog_format.sh
git commit -m "feat: add changelog entry formatting and CHANGELOG.md prepend logic"
```

---

### Task 4: GitLab remote/auth helpers

**Files:**
- Modify: `scripts/release.sh` (new section after changelog formatting)
- Create: `scripts/tests/test_gitlab_remote.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: `url_encode <raw>`, `gitlab_parse_remote_url <url>` (prints `host<TAB>project_path`, returns 1 if unparsable), `gitlab_host_and_project_from_origin` (wraps `git config --get remote.origin.url`, deliberately not `git remote get-url` — see the function's comment), `project_short_name <project_path>`, `gitlab_check_auth <host>` (exit-status only). Task 5, 6, 7, 9 call these.

- [ ] **Step 1: Write the failing tests**

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_gitlab_remote.sh`
Expected: FAILs with `url_encode: command not found`.

- [ ] **Step 3: Insert the GitLab remote/auth section into `scripts/release.sh`**

Insert after the changelog-formatting section:

```bash
# --- gitlab remote / auth helpers -------------------------------------------

url_encode() {
  local raw="$1" i c encoded="" hex
  for (( i = 0; i < ${#raw}; i++ )); do
    c="${raw:i:1}"
    case "$c" in
      [a-zA-Z0-9.~_-]) encoded+="$c" ;;
      *)
        printf -v hex '%02X' "'$c"
        encoded+="%$hex"
        ;;
    esac
  done
  printf '%s\n' "$encoded"
}

gitlab_parse_remote_url() {
  local url="$1"
  if [[ "$url" =~ ^git@([^:]+):(.+)\.git$ ]]; then
    printf '%s\t%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ "$url" =~ ^https?://([^/]+)/(.+)\.git$ ]]; then
    printf '%s\t%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  elif [[ "$url" =~ ^https?://([^/]+)/(.+)$ ]]; then
    printf '%s\t%s\n' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
  else
    echo "gitlab_parse_remote_url: cannot parse remote url '$url'" >&2
    return 1
  fi
}

gitlab_host_and_project_from_origin() {
  # git config --get (not `git remote get-url`) deliberately: get-url applies
  # any url.<base>.insteadOf rewriting, which would hide the real host/project
  # behind whatever the rewritten fetch URL happens to be.
  gitlab_parse_remote_url "$(git config --get remote.origin.url)"
}

project_short_name() {
  local project_path="$1"
  printf '%s\n' "${project_path##*/}"
}

gitlab_check_auth() {
  local host="$1" output
  # `glab auth status` always exits 0 regardless of auth state — status must
  # be read from its (stderr) text output, not the exit code.
  output="$(glab auth status --hostname "$host" 2>&1)"
  ! grep -qi 'not authenticated' <<< "$output"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_gitlab_remote.sh`
Expected: `9 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_gitlab_remote.sh
git commit -m "feat: add GitLab remote-URL parsing and auth-check helpers"
```

---

### Task 5: `related_issues` fetch + 404 probe + JSON→changelog-lines

**Files:**
- Modify: `scripts/release.sh` (new section after GitLab remote/auth helpers)
- Create: `scripts/tests/test_gitlab_related_issues.sh`

**Interfaces:**
- Consumes: `url_encode`, `changelog_format_entry`, `changelog_format_no_issue_entry` (Tasks 3–4).
- Produces: `gitlab_related_issues_json <host> <project_path> <mr_iid>` (stdout = JSON on success; exit `44` means "endpoint unsupported/404, caller must use the fallback"; exit `1` means a hard error, caller must abort loudly), `changelog_related_issues_to_entries <json> <own_project_short> <mr_title> <mr_iid>`. Task 7 calls both.

- [ ] **Step 1: Write the failing tests**

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_gitlab_related_issues.sh`
Expected: FAILs with `changelog_related_issues_to_entries: command not found`.

- [ ] **Step 3: Insert the section into `scripts/release.sh`**

Insert after the GitLab remote/auth section:

```bash
# --- gitlab related_issues data fetch ---------------------------------------

gitlab_related_issues_json() {
  local host="$1" project_path="$2" mr_iid="$3" err_file status
  err_file="$(mktemp)"
  if glab api "projects/$(url_encode "$project_path")/merge_requests/${mr_iid}/related_issues" --hostname "$host" 2>"$err_file"; then
    rm -f "$err_file"
    return 0
  fi
  status=1
  grep -qi '404' "$err_file" && status=44
  cat "$err_file" >&2
  rm -f "$err_file"
  return "$status"
}

changelog_related_issues_to_entries() {
  local json="$1" own_project_short="$2" mr_title="$3" mr_iid="$4"
  local count
  count="$(jq 'length' <<< "$json")"
  if [[ "$count" -eq 0 ]]; then
    changelog_format_no_issue_entry "$mr_title" "$mr_iid"
    return 0
  fi
  local relative title url project_and_iid
  while IFS=$'\t' read -r relative title url; do
    if [[ "$relative" == \#* ]]; then
      project_and_iid="${own_project_short}${relative}"
    else
      project_and_iid="$relative"
    fi
    changelog_format_entry "$project_and_iid" "$title" "$url" "$mr_title" "$mr_iid"
  done < <(jq -r '.[] | [.references.relative, .title, .web_url] | @tsv' <<< "$json")
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_gitlab_related_issues.sh`
Expected: `6 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_gitlab_related_issues.sh
git commit -m "feat: fetch related_issues via glab and render them as changelog lines"
```

---

### Task 6: Description-regex fallback (for pre-`related_issues` GitLab instances)

**Files:**
- Modify: `scripts/release.sh` (new section after `related_issues` fetch)
- Create: `scripts/tests/test_changelog_fallback.sh`

**Interfaces:**
- Consumes: `url_encode` (Task 4).
- Produces: `changelog_extract_issue_refs <description> <own_project_short>` (prints zero or more `project#iid` refs, one per line, not deduplicated), `changelog_resolve_issue_ref <host> <group_prefix> <ref>` (prints `title<TAB>web_url` on stdout, returns 1 if the ref can't be resolved). Task 7 calls both.

- [ ] **Step 1: Write the failing tests**

```bash
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
assert_equal "$(printf 'Fix css cache issues\thttps://gitlab.sissamedialab.it/wjs/specs/-/issues/2923')" "$out" \
  "resolves a known ref to title + url"

PATH="$fake_bin:$PATH" assert_failure "unresolvable ref returns failure, not a fabricated result" \
  changelog_resolve_issue_ref "example.test" "wjs" "specs#99999"

rm -rf "$fake_bin"

test_summary
exit $?
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_changelog_fallback.sh`
Expected: FAILs with `changelog_extract_issue_refs: command not found`.

- [ ] **Step 3: Insert the section into `scripts/release.sh`**

Insert after the `related_issues` data-fetch section:

```bash
# --- changelog description-regex fallback -----------------------------------
# Used only if gitlab_related_issues_json reports exit 44 (endpoint 404s on
# an older self-hosted GitLab). Mirrors the reference patterns the
# releasing-wjs-python-package skill already parsed by hand.

changelog_extract_issue_refs() {
  local description="$1" own_project_short="$2"
  local line proj iid ref

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    proj="$(sed -E 's#https?://[^/]+/([^[:space:]]+)/-/(issues|work_items)/[0-9]+.*#\1#' <<< "$line")"
    iid="$(sed -E 's#.*/-/(issues|work_items)/([0-9]+).*#\2#' <<< "$line")"
    printf '%s#%s\n' "${proj##*/}" "$iid"
  done < <(grep -oE 'https?://[^[:space:]]+/-/(issues|work_items)/[0-9]+' <<< "$description")

  while IFS= read -r ref; do
    [[ -n "$ref" ]] && printf '%s\n' "$ref"
  done < <(grep -oE '[A-Za-z][A-Za-z0-9_-]*#[0-9]+' <<< "$description")

  while IFS= read -r iid; do
    [[ -n "$iid" ]] && printf '%s#%s\n' "$own_project_short" "$iid"
  done < <(grep -oE '(^|[^A-Za-z0-9_-])#[0-9]+' <<< "$description" | grep -oE '#[0-9]+' | grep -oE '[0-9]+')
}

changelog_resolve_issue_ref() {
  local host="$1" group_prefix="$2" ref="$3" project_short issue_iid project_path json
  project_short="${ref%%#*}"
  issue_iid="${ref##*#}"
  project_path="${group_prefix}/${project_short}"
  if json="$(glab api "projects/$(url_encode "$project_path")/issues/${issue_iid}" --hostname "$host" 2>/dev/null)"; then
    jq -r '[.title, .web_url] | @tsv' <<< "$json"
  else
    return 1
  fi
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_changelog_fallback.sh`
Expected: `4 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_changelog_fallback.sh
git commit -m "feat: add description-regex changelog fallback for pre-related_issues GitLab"
```

---

### Task 7: Changelog section builder (wires Tasks 3, 4, 5, 6 to real `git log`)

**Files:**
- Modify: `scripts/release.sh` (new section after the fallback section)
- Create: `scripts/tests/test_changelog_builder.sh`

**Interfaces:**
- Consumes: `changelog_extract_mr_iid` (new, this task), `gitlab_related_issues_json`, `changelog_related_issues_to_entries`, `changelog_extract_issue_refs`, `changelog_resolve_issue_ref`, `changelog_format_entry`, `changelog_format_no_issue_entry`, `changelog_format_plain_commit`, `changelog_section_header`.
- Produces: `changelog_extract_mr_iid <commit_body>` (prints the IID from a `See merge request <project>!<iid>` trailer, empty if absent), `changelog_build_section <host> <project_path> <group_prefix> <prev_tag> <develop_branch> <version> <date>` (prints the full `## [version] - date` section).

**Design note (found by hands-on verification while writing this plan, corrects the letter of the approved spec's step-5 git command):** the spec's literal `git log <prev-tag>..wjs-production --merges --grep="See merge request"` structurally cannot also satisfy its own "non-merge commits in range → listed by subject" requirement — that filter excludes every non-merge commit outright, so the fallback path would be dead code. Verified directly with real git sandboxes (see conversation) that the correct traversal is `git log <prev_tag>..wjs-develop --first-parent` (walking `wjs-develop`'s own mainline, not `wjs-production`, and not filtered to merges) classified per-commit:
1. Has a `See merge request ...!<iid>` trailer → build changelog entries for that MR (unchanged from the original design).
2. No trailer, but 2+ parents → a release-machinery merge (the script's own `Merge branch 'wjs-production' into 'wjs-develop'` from a prior cycle) — skip silently, no changelog line.
3. No trailer, single parent, subject starts with `Release ` → the script's own release/dev-bump commit — skip silently.
4. No trailer, single parent, anything else → a genuine direct push — one plain-subject changelog line.

Without cases 2–3, every release's changelog would spuriously list its own prior "Release ..." and merge-back commits — confirmed this does NOT happen in this repo's real `v2.0.19` `CHANGELOG.md` (checked via `git show 6e549f08:CHANGELOG.md` — all 9 real lines are genuine feature MRs, zero machinery noise).

- [ ] **Step 1: Write the failing tests**

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_changelog_builder.sh`
Expected: FAILs with `changelog_extract_mr_iid: command not found`.

- [ ] **Step 3: Insert the section into `scripts/release.sh`**

Insert after the fallback section:

```bash
# --- changelog section builder ----------------------------------------------

changelog_extract_mr_iid() {
  local body="$1"
  if [[ "$body" =~ See\ merge\ request\ [^\!]*\!([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  fi
}

changelog_build_section() {
  local host="$1" project_path="$2" group_prefix="$3" prev_tag="$4" develop_branch="$5" version="$6" date="$7"
  local own_project_short related_issues_supported=1
  own_project_short="$(project_short_name "$project_path")"

  changelog_section_header "$version" "$date"
  printf '\n'

  local sha body mr_iid json status subject parent_count
  while IFS=$'\x00' read -r -d $'\x00' sha; do
    sha="${sha#$'\n'}"
    IFS=$'\x00' read -r -d $'\x00' body
    IFS=$'\x00' read -r -d $'\x00' _separator || true

    mr_iid="$(changelog_extract_mr_iid "$body")"
    subject="$(git log -1 --format='%s' "$sha")"

    if [[ -z "$mr_iid" ]]; then
      parent_count="$(git log -1 --format='%P' "$sha" | wc -w)"
      if [[ "$parent_count" -ge 2 ]]; then
        continue  # a non-GitLab merge (e.g. this script's own merge-back) — not a real MR, skip
      fi
      if [[ "$subject" == Release\ * ]]; then
        continue  # this script's own release/dev-bump commit — skip
      fi
      changelog_format_plain_commit "$subject"
      continue
    fi

    if [[ "$related_issues_supported" -eq 1 ]]; then
      set +e
      json="$(gitlab_related_issues_json "$host" "$project_path" "$mr_iid" 2>/dev/null)"
      status=$?
      set -e
      if [[ $status -eq 44 ]]; then
        related_issues_supported=0
      elif [[ $status -ne 0 ]]; then
        echo "changelog_build_section: hard failure fetching related_issues for !$mr_iid" >&2
        return 1
      fi
    fi

    local mr_title
    mr_title="$(git log -1 --format='%b' "$sha" | head -n1)"

    if [[ "$related_issues_supported" -eq 1 ]]; then
      changelog_related_issues_to_entries "$json" "$own_project_short" "$mr_title" "$mr_iid"
    else
      local description refs ref title url found=0
      description="$(glab api "projects/$(url_encode "$project_path")/merge_requests/${mr_iid}" --hostname "$host" | jq -r '.description')"
      refs="$(changelog_extract_issue_refs "$description" "$own_project_short" | sort -u)"
      while IFS= read -r ref; do
        [[ -z "$ref" ]] && continue
        if IFS=$'\t' read -r title url < <(changelog_resolve_issue_ref "$host" "$group_prefix" "$ref" 2>/dev/null); then
          changelog_format_entry "$ref" "$title" "$url" "$mr_title" "$mr_iid"
          found=1
        fi
      done <<< "$refs"
      [[ "$found" -eq 0 ]] && changelog_format_no_issue_entry "$mr_title" "$mr_iid"
    fi
  done < <(git log "${prev_tag}..${develop_branch}" --first-parent --format='%H%x00%B%x00---%x00')
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_changelog_builder.sh`
Expected: `3 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_changelog_builder.sh
git commit -m "feat: build a full changelog section from git log + GitLab MR/issue data"
```

---

### Task 8: Preflight checks

**Files:**
- Modify: `scripts/release.sh` (new section after the changelog builder)
- Create: `scripts/tests/test_preflight.sh`

**Interfaces:**
- Consumes: `gitlab_host_and_project_from_origin`, `gitlab_check_auth` (Task 4).
- Produces: `preflight_check_tools`, `preflight_check_clean_tree`, `preflight_check_branches`, `preflight_check_gitlab_auth <host>`. Task 10 (`main` wiring) calls all four in sequence.

- [ ] **Step 1: Write the failing tests**

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_preflight.sh`
Expected: FAILs with `preflight_check_tools: command not found`.

- [ ] **Step 3: Insert the section into `scripts/release.sh`**

Insert after the changelog-builder section:

```bash
# --- preflight ---------------------------------------------------------------

preflight_check_tools() {
  local tool missing=()
  for tool in git glab jq pre-commit; do
    command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    echo "release.sh: missing required tools: ${missing[*]}" >&2
    echo "Install them before running this script." >&2
    return 1
  fi
}

preflight_check_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "release.sh: working tree is not clean. Commit or stash changes first." >&2
    git status --short >&2
    return 1
  fi
}

preflight_check_branches() {
  local branch
  for branch in wjs-develop wjs-production; do
    if ! git show-ref --verify --quiet "refs/heads/$branch" &&
       ! git show-ref --verify --quiet "refs/remotes/origin/$branch"; then
      echo "release.sh: branch '$branch' not found locally or on origin." >&2
      return 1
    fi
  done
}

preflight_check_gitlab_auth() {
  local host="$1"
  if ! gitlab_check_auth "$host"; then
    echo "release.sh: not authenticated to $host. Run: glab auth login --hostname $host" >&2
    return 1
  fi
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_preflight.sh`
Expected: `8 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_preflight.sh
git commit -m "feat: add release.sh preflight checks (tools, clean tree, branches, glab auth)"
```

---

### Task 9: Git-flow helpers (sync + resumable merge)

**Files:**
- Modify: `scripts/release.sh` (new section after preflight)
- Create: `scripts/tests/test_git_flow.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: `git_ff_branch <branch>` (fetches nothing itself — caller runs `git fetch` once — fast-forwards local `<branch>` to `origin/<branch>`, returns 1 with a status dump on divergence), `git_merge_already_done <expected_parent_sha>` (returns 0 if `HEAD`'s parents include it), `git_merge_or_skip <target_branch> <source_branch> <message>` (switches to target, skips the merge if `git_merge_already_done` says it's already there, otherwise merges and leaves the tree mid-merge with instructions on conflict). Task 10 wires all three into `main`.

- [ ] **Step 1: Write the failing tests**

```bash
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

test_summary
exit $?
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_git_flow.sh`
Expected: FAILs with `git_ff_branch: command not found`.

- [ ] **Step 3: Insert the section into `scripts/release.sh`**

Insert after the preflight section:

```bash
# --- git flow helpers --------------------------------------------------------

git_ff_branch() {
  local branch="$1"
  if ! git show-ref --verify --quiet "refs/heads/$branch"; then
    git switch -c "$branch" "origin/$branch"
    return 0
  fi
  git switch "$branch"
  if ! git merge --ff-only "origin/$branch"; then
    echo "release.sh: '$branch' has diverged from origin/$branch. Resolve manually:" >&2
    git status >&2
    return 1
  fi
}

git_merge_already_done() {
  local expected_parent_sha="$1" parents
  parents="$(git log -1 --format='%P' HEAD)"
  [[ " $parents " == *" $expected_parent_sha "* ]]
}

git_merge_or_skip() {
  local target="$1" source="$2" message="$3" source_tip
  git switch "$target"
  source_tip="$(git rev-parse "$source")"
  if git_merge_already_done "$source_tip"; then
    echo "release.sh: $target already contains $source (merge previously completed), skipping"
    return 0
  fi
  if ! git merge --no-ff "$source" -m "$message"; then
    echo "release.sh: merge conflict merging $source into $target." >&2
    echo "Resolve the conflicts, then: git add <files> && git commit, and re-run this script." >&2
    return 1
  fi
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_git_flow.sh`
Expected: `5 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_git_flow.sh
git commit -m "feat: add fast-forward-sync and resumable-merge git-flow helpers"
```

---

### Task 10: Interactive push gates

**Files:**
- Modify: `scripts/release.sh` (new section after git-flow helpers)
- Create: `scripts/tests/test_push_gates.sh`

**Interfaces:**
- Consumes: nothing new.
- Produces: `release_confirm_develop_ready <develop_branch>` (prints the outgoing commit log, prompts `y/n` on stdin, returns 1 on anything but `y`/`Y` — pushes nothing itself), `release_confirm_and_push_production <production_branch> <develop_branch> <tag>` (prints outgoing commits + the tag, prompts `y/n`, and on `y` runs `git push origin <production_branch> <develop_branch>` followed by `git push origin <tag>`; on anything else, returns 1 and pushes nothing). Task 12 (`main` wiring) calls both in sequence.

- [ ] **Step 1: Write the failing tests**

```bash
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

(cd "$clone" && assert_failure "release_confirm_develop_ready aborts on n" \
  bash -c 'echo n | { source ../release.sh 2>/dev/null || true; }' )

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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_push_gates.sh`
Expected: FAILs with `release_confirm_develop_ready: command not found`. (The first assertion block above is a placeholder probe and can be deleted once real functions exist — replace it in Step 3 below.)

- [ ] **Step 3: Simplify the test to drop the placeholder probe, then insert the section into `scripts/release.sh`**

Edit `scripts/tests/test_push_gates.sh` to delete these two lines (they were only there to make Step 2's initial failure legible; the real behavioral assertions below already cover the `n` case):

```bash
(cd "$clone" && assert_failure "release_confirm_develop_ready aborts on n" \
  bash -c 'echo n | { source ../release.sh 2>/dev/null || true; }' )
```

Insert into `scripts/release.sh`, after the git-flow section:

```bash
# --- interactive push gates ---------------------------------------------

release_confirm_develop_ready() {
  local develop_branch="$1"
  echo "--- commits ready to push on $develop_branch ---"
  git log "origin/${develop_branch}..${develop_branch}" --oneline
  local answer
  read -r -p "Continue toward pushing $develop_branch? [y/N] " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "release.sh: aborted. Nothing pushed; re-run this script to resume."
    return 1
  fi
}

release_confirm_and_push_production() {
  local production_branch="$1" develop_branch="$2" tag="$3"
  echo "--- commits ready to push on $production_branch ---"
  git log "origin/${production_branch}..${production_branch}" --oneline
  echo "--- tag ready to push ---"
  git tag --points-at "$production_branch"
  local answer
  read -r -p "Push $production_branch, $develop_branch, and $tag to origin? [y/N] " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "release.sh: aborted. Nothing pushed; re-run this script to resume."
    return 1
  fi
  git push origin "$production_branch" "$develop_branch"
  git push origin "$tag"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_push_gates.sh`
Expected: `6 run, 0 failed`, exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_push_gates.sh
git commit -m "feat: add the two interactive confirm-and-push gates"
```

---

### Task 11: `main` orchestration

**Files:**
- Modify: `scripts/release.sh` (replace the placeholder `main` with the real orchestration)
- Create: `scripts/tests/test_full_pipeline.sh`

**Interfaces:**
- Consumes: every function from Tasks 2–10.
- Produces: `main` (no args) — runs preflight, then steps 1–11 from the design doc, exiting non-zero with a printed reason at the first failed check, and returning 0 after both pushes succeed (or after a clean `n`-abort at either gate, in which case it returns 1 but has changed nothing on `origin`).

- [ ] **Step 1: Write the failing end-to-end test**

This models a real release cycle end-to-end: two feature MRs (one linking `specs#2907`, one with no linked issue) plus a plain dependency-bump commit, merged into `wjs-develop` via real `--no-ff` branch merges, then released to `wjs-production` as `2.0.19`. It uses a fake `glab` for both the API calls and `auth status`, and a fake `pre-commit`.

Two details matter here, both found by hands-on verification while writing this plan (see the conversation that produced it):
- `origin` must look like a real GitLab SSH URL to `gitlab_host_and_project_from_origin` (Task 4) while actually pushing/fetching against a local bare repo. `git config url.<bare-path>.insteadOf <fake-ssh-url>` does exactly this: `git config --get remote.origin.url` (what Task 4 reads) still returns the fake SSH URL, while `git push`/`git fetch` transparently redirect to the local bare path.
- `PATH=X cmd1 | cmd2` only exports `PATH=X` to `cmd1`, **not** `cmd2` — the fake `glab`/`pre-commit` must be put on `PATH` with a plain `export` (or on its own line) *before* the `printf ... | bash ../release.sh` pipeline, not as a prefix on `printf`.

```bash
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_full_pipeline.sh`
Expected: FAILs — `main` currently just prints `release.sh: not yet implemented` and returns 1.

- [ ] **Step 3: Replace the placeholder `main` in `scripts/release.sh`**

```bash
main() {
  preflight_check_tools || return 1
  preflight_check_clean_tree || return 1

  local host project_path group_prefix
  IFS=$'\t' read -r host project_path < <(gitlab_host_and_project_from_origin)
  group_prefix="${project_path%/*}"

  preflight_check_gitlab_auth "$host" || return 1
  preflight_check_branches || return 1

  git fetch origin
  git_ff_branch wjs-develop || return 1
  git_ff_branch wjs-production || return 1

  local prev_tag
  prev_tag="$(git describe --tags --abbrev=0 wjs-production)"

  git_merge_or_skip wjs-production wjs-develop "Merge branch 'wjs-develop' into 'wjs-production'" || return 1

  local raw_version release_version
  raw_version="$(version_read_setup_cfg setup.cfg)"
  release_version="$(version_release_from_dev "$raw_version")"

  local section
  section="$(changelog_build_section "$host" "$project_path" "$group_prefix" "$prev_tag" wjs-develop "$release_version" "$(date +%F)")"
  changelog_prepend_section CHANGELOG.md "$section"

  sed -i "s/^version = .*/version = ${release_version}/" setup.cfg
  pre-commit run --all-files || true
  git add setup.cfg CHANGELOG.md
  git commit -m "Release ${release_version}"
  git tag -a "v${release_version}" -m "Release ${release_version}"

  git_merge_or_skip wjs-develop wjs-production "Merge branch 'wjs-production' into 'wjs-develop'" || return 1

  local next_dev_version
  next_dev_version="$(version_next_dev "$release_version")"
  sed -i "s/^version = .*/version = ${next_dev_version}/" setup.cfg
  if ! git diff --quiet -- setup.cfg; then
    git add setup.cfg
    git commit -m "Release ${next_dev_version}"
  fi

  release_confirm_develop_ready wjs-develop || return 1
  release_confirm_and_push_production wjs-production wjs-develop "v${release_version}" || return 1
}
```

This replaces only the `main() { ... }` function body shown above. **Do not touch or duplicate** the source-guard footer (`if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then main "$@"; fi`) that Task 2 already put at the end of the file, right after `main`'s closing `}` — it stays exactly where it is, unchanged. (Two copies of that guard would call `main` twice on every real invocation — a duplicate release attempt.)

Note: `changelog_build_section` is passed `wjs-develop` (not `wjs-production`) as its branch argument — see Task 7's design note on why the changelog is built by walking `wjs-develop`'s own first-parent history, not `wjs-production`'s.

Remove the old placeholder `main` body (the `echo "release.sh: not yet implemented" >&2; return 1` two-liner) it replaces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_full_pipeline.sh`
Expected: `9 run, 0 failed`, exit code `0`.

Then run the entire suite to confirm nothing earlier regressed:

Run: `bash scripts/tests/run_tests.sh`
Expected: every `test_*.sh` block ends `N run, 0 failed`; overall exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_full_pipeline.sh
git commit -m "feat: wire release.sh's main() end-to-end and add a full-pipeline test"
```

> **Superseded:** the `main()` body above is the first working version, not
> the final one. Task 13 makes it resumable across a clean `n`-abort, and
> Task 14 makes tag creation idempotent and moves it outside the
> fresh-vs-resumed branch. See those tasks for the code that actually ships.

---

### Task 12: `pre-commit` hygiene + executable bit

**Files:**
- Modify: `scripts/release.sh` (no content change expected, only formatting/lint fixes)
- Modify: `.pre-commit-config.yaml` — none expected; this task only runs the existing hooks against the new files.

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task just ensures `scripts/release.sh` and `scripts/tests/*.sh` satisfy the repo's existing hooks (`trailing-whitespace`, `end-of-file-fixer`, `check-executables-have-shebangs`, ...) before this work is proposed as a merge request, per `.claude/rules/linting.md`.

- [ ] **Step 1: Confirm every shipped script is executable and has a shebang**

```bash
chmod +x scripts/release.sh scripts/tests/*.sh
```

- [ ] **Step 2: Run pre-commit against the new files**

Run: `pre-commit run --all-files`
Expected: all hooks pass (`Passed`) for `scripts/release.sh` and every `scripts/tests/*.sh`. If `trailing-whitespace`/`end-of-file-fixer` auto-fix anything, re-stage and re-run until clean.

- [ ] **Step 3: Re-run the full test suite after any auto-fixes**

Run: `bash scripts/tests/run_tests.sh`
Expected: exit code `0`, no regressions from whitespace/EOF fixes.

- [ ] **Step 4: Commit**

```bash
git add -A scripts/
git commit -m "chore: satisfy pre-commit hooks on scripts/release.sh and its tests"
```

(Skip this commit if Step 2 made no changes — nothing to commit.)

---

### Task 13: Resumable across a clean `n`-abort (tag-independent resume detection)

**Context:** found by the final whole-branch review after Task 12, traced back to the approved design rather than implementation drift — the design promises "re-run to resume" after saying `n` at a confirm gate, but a clean `n` *after* the release commit/tag/changelog step was already done locally would recompute `prev_tag` from the tag just created and silently start cutting the *next* release instead of resuming.

**Files:**
- Modify: `scripts/release.sh` (`release_confirm_develop_ready`/`release_confirm_and_push_production` messages, new `release_already_prepared_locally`, `main`'s prepare branch)
- Create: `scripts/tests/test_resume.sh`

**Interfaces:**
- Consumes: `version_read_setup_cfg`, `git_ff_branch`, `git_merge_or_skip` (Tasks 2, 9).
- Produces: `release_already_prepared_locally` (prints the release version and returns 0 when `wjs-production`'s tip is already a `Release <version>` commit **and** still ahead of `origin` — the "ahead" check is what stops a normal, already-pushed prior release from being mistaken for an unpushed one on every subsequent fresh run). `main` calls it first thing after syncing the branches.

- [ ] **Step 1: Write the failing test**

`scripts/tests/test_resume.sh` drives `main` twice against a real bare-repo "origin" and a fake `glab`/`pre-commit`: run 1 answers `n` at the develop gate (must complete the full prepare flow and leave exactly one changelog section and one `Release 2.0.19` commit, unpushed); run 2 answers `y`/`y` (must detect the resume, skip straight to the push gates, and push without duplicating anything). See `scripts/tests/test_resume.sh` for the full fixture — it mirrors `test_full_pipeline.sh`'s setup (one feature MR linking `specs#2907`, `wjs-production` at `v2.0.18`) but runs `main` twice in sequence to exercise the abort-then-resume path specifically.

- [ ] **Step 2: Run it to verify it fails**

Run: `bash scripts/tests/test_resume.sh`
Expected: FAILs on run 2's resume assertions — without `release_already_prepared_locally`, saying `n` then re-running recomputes `prev_tag` from the just-created `v2.0.19` tag and starts building a `2.0.20` release instead of resuming `2.0.19`.

- [ ] **Step 3: Add `release_already_prepared_locally` and wire it into `main`**

```bash
release_already_prepared_locally() {
  # True (prints the release version) when a previous run already got as
  # far as tagging wjs-production but was interrupted or told 'n' before
  # pushing: HEAD's subject is a "Release <version>" commit, an exact
  # v<version> tag points at it, AND wjs-production is still ahead of
  # origin (otherwise this is just the normal, already-pushed state every
  # release sits in between cycles, which also matches the first two
  # checks). Lets main() skip straight to the push gates on a resume
  # instead of re-merging/re-tagging/re-changelog-ing.
  local subject exact_tag ahead
  subject="$(git log -1 --format='%s' wjs-production)"
  [[ "$subject" == Release\ * ]] || return 1
  exact_tag="$(git tag --points-at wjs-production | grep -E '^v[0-9]' | head -n1)"
  [[ -n "$exact_tag" ]] || return 1
  ahead="$(git rev-list --count origin/wjs-production..wjs-production)"
  [[ "$ahead" -gt 0 ]] || return 1
  printf '%s\n' "${exact_tag#v}"
}
```

In `main`, replace the unconditional prepare block with:

```bash
  local release_version
  if release_version="$(release_already_prepared_locally)"; then
    echo "release.sh: wjs-production is already at 'Release ${release_version}' locally (not yet pushed) — resuming toward the push gates"
  else
    local prev_tag
    prev_tag="$(git describe --tags --abbrev=0 wjs-production)"

    git_merge_or_skip wjs-production wjs-develop "Merge branch 'wjs-develop' into 'wjs-production'" || return 1

    local raw_version
    raw_version="$(version_read_setup_cfg setup.cfg)"
    release_version="$(version_release_from_dev "$raw_version")"

    local section
    section="$(changelog_build_section "$host" "$project_path" "$group_prefix" "$prev_tag" wjs-develop "$release_version" "$(date +%F)")"
    changelog_prepend_section CHANGELOG.md "$section"

    sed -i "s/^version = .*/version = ${release_version}/" setup.cfg
    pre-commit run --all-files || true
    git add -A
    git commit -m "Release ${release_version}"
    git tag -a "v${release_version}" -m "Release ${release_version}"
  fi
```

Also reword both confirm-gate abort messages from `"Nothing pushed; re-run this script to resume."` to `"Nothing pushed; everything prepared locally is safe — re-run this script to pick up where you left off."` — the old wording didn't make clear that the local state (release commit, tag, changelog) survives an abort untouched.

The `git add setup.cfg CHANGELOG.md` from Task 11 becomes `git add -A`: `pre-commit run --all-files` can reformat files this release didn't touch, and staging only the two release files left those reformats stranded uncommitted — which could abort a later `git switch` after the release was already tagged.

- [ ] **Step 4: Run tests to verify they pass**

Run: `bash scripts/tests/test_resume.sh`
Expected: `8 run, 0 failed`, exit code `0`. (Grows to `13 run` once Task 14 adds the untagged-release-commit case.)

Then the full suite:

Run: `bash scripts/tests/run_tests.sh`
Expected: every block `N run, 0 failed`; overall exit code `0`.

- [ ] **Step 5: Commit**

```bash
git add scripts/release.sh scripts/tests/test_resume.sh
git commit -m "fix: make main() resumable across a clean n-abort, and stage all pre-commit edits"
```

> **Superseded:** `release_already_prepared_locally`'s tag-based check above
> is itself replaced in Task 14 — it still has the gap that step 7 (tagging)
> runs *after* the release commit, so an interruption in that exact window
> left an untagged commit this version of the guard couldn't recognise.

---

### Task 14: Changelog & tagging hardening (idempotent tag, duplicate-section guard, cross-project issue misattribution, IFS-safe field separator)

**Context:** a later manual review pass (outside the original TDD sequence) found four related correctness gaps, all sharing one theme — a step assumed it would only ever run once, or that a tab-separated `read` was safe:

1. `changelog_prepend_section` would happily prepend a second `## [<version>]` section if a release run was re-prepared after already writing one (e.g. resumed after an interruption the resume check in Task 13 didn't yet cover) — stacking a duplicate section and, downstream, a second near-identical `Release <version>` commit.
2. Tab-separated JSON→shell handoffs (`IFS=$'\t' read`) silently collapsed an empty leading field — a null/missing `references.relative` — into the next field, shifting an issue's title into the ref slot and leaving the URL slot empty.
3. `references.relative` is relative to the *issue's own* project, so a cross-project issue (e.g. an issue in `wjs/specs` linked from an MR in this repo) arrives as a bare `#iid`, indistinguishable from a same-project reference — and was mislabeled with this repo's own project name while still linking to the other project's URL.
4. Tagging (`git tag -a`) was not idempotent: a run interrupted between the release commit and the tag left an untagged commit, and the next run's plain `git tag -a` aborted under `set -e` because by then the tag might already exist from a partial retry.

**Files:**
- Modify: `scripts/release.sh` (`changelog_prepend_section`, `changelog_related_issues_to_entries`, `changelog_resolve_issue_ref`, new `CHANGELOG_FS` + `changelog_project_short_from_issue_url`, new `git_tag_release_or_verify`, `release_already_prepared_locally`, `main`)
- Modify: `scripts/tests/test_changelog_format.sh`, `scripts/tests/test_gitlab_related_issues.sh`, `scripts/tests/test_changelog_fallback.sh`, `scripts/tests/test_git_flow.sh`, `scripts/tests/test_resume.sh`

**Interfaces:**
- Produces: `CHANGELOG_FS` (module-level `$'\x1f'` constant — the field separator for every `jq`-to-`read` handoff below, replacing tab), `changelog_project_short_from_issue_url <issue_web_url>` (prints the path segment immediately before `/-/`, empty if there is none), `git_tag_release_or_verify <tag> <message> [<ref>]` (creates the tag if missing; if it already exists and points at `<ref>` — default `HEAD` — succeeds as a no-op; if it points elsewhere, fails with an inspect/resolve message rather than overwriting it).
- Changes existing behavior of: `changelog_prepend_section` (now returns 1 without writing anything if the target already has a section for the same version), `changelog_related_issues_to_entries`/`changelog_resolve_issue_ref` (now read/print `CHANGELOG_FS`-joined fields instead of tab-separated, and derive the entry's project from `web_url` via `changelog_project_short_from_issue_url` before falling back to `references.relative`), `release_already_prepared_locally` (drops the `v<version>` tag requirement — checks `setup.cfg`'s version at `wjs-production`'s `HEAD` instead, since tagging is no longer a precondition for detecting a resumable state).

- [ ] **Step 1: `changelog_prepend_section` — refuse a duplicate version section**

```bash
changelog_prepend_section() {
  # changelog_prepend_section - add a release section at the top of the changelog
  #
  # Refuses if the file already has a section for the same version, instead of
  # stacking a second copy: main()'s prepare branch is not idempotent, so a
  # release run interrupted before it finished (or re-run after the release was
  # already pushed) used to prepend a duplicate "## [<version>]" section and
  # commit it as a second, near-identical "Release <version>" commit.
  #
  # Arguments:
  #   $1 - path to CHANGELOG.md (created, with a "# Changelog" header, if absent)
  #   $2 - section to prepend; its first line must be the "## [<version>] - <date>" header
  # Returns:
  #   0 on success
  #   1 if $1 already contains a section for $2's version (message on stderr)
  local path="$1" section="$2" tmp version
  version="$(sed -n '1s/^## \[\([^]]*\)\].*/\1/p' <<< "$section")"
  if [[ -n "$version" && -f "$path" ]] && grep -qF -- "## [$version]" "$path"; then
    echo "changelog_prepend_section: $path already has a '## [$version]' section." >&2
    echo "A previous release run already wrote it. Drop that section (or reset the branch to origin) before re-running." >&2
    return 1
  fi
  tmp="$(mktemp)"
  if [[ ! -f "$path" ]]; then
    printf '# Changelog\n\n%s\n' "$section" > "$path"
    return 0
  fi
  if head -n1 "$path" | grep -qx '# Changelog'; then
    {
      printf '# Changelog\n\n%s\n\n' "$section"
      tail -n +2 "$path" | sed '/./,$!d'
    } > "$tmp"
  else
    {
      printf '%s\n\n' "$section"
      cat "$path"
    } > "$tmp"
  fi
  mv "$tmp" "$path"
}
```

Add to `scripts/tests/test_changelog_format.sh`: a prepend of a version already present must fail and leave the file untouched; a genuinely new version must still land on top. And `main` must propagate the failure: change `changelog_prepend_section CHANGELOG.md "$section"` to `changelog_prepend_section CHANGELOG.md "$section" || return 1`.

- [ ] **Step 2: `CHANGELOG_FS` + `changelog_project_short_from_issue_url` — IFS-safe fields and correct cross-project attribution**

```bash
# Field separator for every jq -> shell handoff below: a literal unit
# separator (0x1f), NOT a tab. Tab is an IFS-whitespace character, so
# `IFS=$'\t' read` silently collapses an empty middle field (dev-less
# versions like "2.0.16+ally1") into the next field, dropping the suffix.
# Same trap already documented in version_release_from_dev; here it silently
# turned an issue with no `references.relative` into an entry whose ref was
# the issue title and whose URL was empty. 0x1f is not IFS-whitespace, so
# empty fields are preserved.
CHANGELOG_FS=$'\x1f'

changelog_project_short_from_issue_url() {
  # changelog_project_short_from_issue_url - owning project of an issue URL
  #
  # An issue/work-item URL is
  #   https://<host>/<group>[/<subgroup>...]/<project>/-/{issues,work_items}/<iid>
  # so the path segment immediately before "/-/" names the project that owns
  # the issue, whatever group or subgroup nesting precedes it.
  #
  # Arguments:
  #   $1 - issue web_url
  # Returns:
  #   0 always; prints the project short name, or nothing if $1 has no "/-/"
  local url="$1" path="${1%%/-/*}"
  [[ "$path" == "$url" ]] && return 0
  printf '%s\n' "${path##*/}"
}
```

`changelog_related_issues_to_entries` now derives the ref from `web_url` first (via `changelog_project_short_from_issue_url` + the issue's own `iid`), falling back to `own_project_short` + `references.relative` only when the URL doesn't parse:

```bash
changelog_related_issues_to_entries() {
  local json="$1" own_project_short="$2" mr_title="$3" mr_iid="$4"
  local count
  count="$(jq 'length' <<< "$json")"
  if [[ "$count" -eq 0 ]]; then
    changelog_format_no_issue_entry "$mr_title" "$mr_iid"
    return 0
  fi
  local relative iid title url project_and_iid issue_project
  while IFS="$CHANGELOG_FS" read -r relative iid title url; do
    # Derive the ref from web_url, not from references.relative: `relative` is
    # relative to the issue's *own* project, so a cross-project issue arrives
    # as a bare "#iid" exactly like a same-project one. Prefixing that with
    # this project's name mislabels it — specs#2957 rendered as
    # wjs-profile-project#2957, pointing at a /wjs/specs/ URL. web_url is the
    # only field guaranteed to agree with the link the entry actually carries.
    issue_project="$(changelog_project_short_from_issue_url "$url")"
    if [[ -z "$iid" && "$url" =~ /([0-9]+)$ ]]; then
      iid="${BASH_REMATCH[1]}"
    fi
    if [[ -n "$issue_project" && -n "$iid" ]]; then
      project_and_iid="${issue_project}#${iid}"
    elif [[ "$relative" == \#* ]]; then
      project_and_iid="${own_project_short}${relative}"
    else
      project_and_iid="$relative"
    fi
    changelog_format_entry "$project_and_iid" "$title" "$url" "$mr_title" "$mr_iid"
  done < <(jq -r --arg fs "$CHANGELOG_FS" '
    .[]
    | [(.references.relative // ""), (.iid // "" | tostring), .title, .web_url]
    | join($fs)' <<< "$json")
}
```

`changelog_resolve_issue_ref` switches its `jq` output from `@tsv` to `join($fs)` with `--arg fs "$CHANGELOG_FS"`, and its one caller in `changelog_build_section` switches `IFS=$'\t' read` to `IFS="$CHANGELOG_FS" read`.

Add to `scripts/tests/test_gitlab_related_issues.sh`: a null `references.relative`, a missing `references` key, and an issue with neither ref field usable (all three must fall back to `#iid`/`web_url` without shifting the URL), plus cross-project and same-project bare-`#iid` cases, plus direct unit tests for `changelog_project_short_from_issue_url`. Add to `scripts/tests/test_changelog_fallback.sh`: an issue with an *empty* title must not shift its URL into the title slot (the exact IFS-collapse `CHANGELOG_FS` exists to prevent) — **and if this test replaces the shared fake `glab` stub with one that always exits 0, restore a stub that fails for unresolvable refs before the pre-existing "unresolvable ref returns failure" assertion later in the file, or that assertion silently stops testing anything.**

- [ ] **Step 3: `git_tag_release_or_verify` — idempotent tag creation**

```bash
git_tag_release_or_verify() {
  # git_tag_release_or_verify - create the release tag, idempotently
  #
  # Tagging is a separate step from the release commit, so a run interrupted
  # between the two left an untagged release commit; plain `git tag -a` would
  # then abort the next run mid-sequence (set -e) because the tag exists. This
  # accepts a tag that already points at the target commit and only fails when
  # it points somewhere else — a state that needs a human decision.
  #
  # Arguments:
  #   $1 - tag name (e.g. "v2.0.20")
  #   $2 - tag message
  #   $3 - commit-ish to tag (optional, default HEAD)
  # Returns:
  #   0 if the tag now exists at the target commit (created, or already there)
  #   1 if the tag exists at a different commit (message on stderr)
  local tag="$1" message="$2" ref="${3:-HEAD}" target existing
  target="$(git rev-parse --verify "${ref}^{commit}")"
  if existing="$(git rev-parse --verify --quiet "refs/tags/${tag}^{commit}")"; then
    if [[ "$existing" == "$target" ]]; then
      echo "release.sh: tag $tag already points at $(git rev-parse --short "$target") (previous run created it), keeping it"
      return 0
    fi
    echo "release.sh: tag $tag already exists but points at $(git rev-parse --short "$existing"), not $(git rev-parse --short "$target")." >&2
    echo "Inspect it ('git show $tag'), then either delete it ('git tag -d $tag') or reset the branch, and re-run." >&2
    return 1
  fi
  git tag -a "$tag" -m "$message" "$target"
}
```

Add to `scripts/tests/test_git_flow.sh`: creates a missing tag; accepts re-running against a tag already at the target (must not die under `set -e`); refuses (and leaves untouched) a tag that points at a different commit.

- [ ] **Step 4: `release_already_prepared_locally` — drop the tag requirement; move tagging out of the fresh/resumed branch in `main`**

```bash
release_already_prepared_locally() {
  # True (prints the release version) when a previous run already committed
  # the release on wjs-production but was interrupted or told 'n' before
  # pushing: HEAD's subject is a "Release <version>" commit, setup.cfg at
  # that commit records the same version, AND wjs-production is still ahead
  # of origin (otherwise this is just the normal, already-pushed state every
  # release sits in between cycles, which also matches the first two
  # checks). Lets main() skip straight to the push gates on a resume
  # instead of re-merging/re-changelog-ing/re-committing.
  #
  # Deliberately does NOT require the v<version> tag. The tag used to be part
  # of this check, but main() creates it one step *after* the release commit,
  # so a run interrupted in that window left a commit this guard could not
  # recognise — and the next run prepared the whole release again, stacking a
  # duplicate changelog section and a second, identically-titled
  # "Release <version>" commit. git_tag_release_or_verify makes the tagging
  # step itself idempotent instead, and main() calls it on both paths.
  local subject version cfg_version ahead
  subject="$(git log -1 --format='%s' wjs-production)"
  [[ "$subject" =~ ^Release\ ([0-9].*)$ ]] || return 1
  version="${BASH_REMATCH[1]}"
  cfg_version="$(version_read_setup_cfg <(git show 'wjs-production:setup.cfg'))" || return 1
  [[ "$cfg_version" == "$version" ]] || return 1
  ahead="$(git rev-list --count origin/wjs-production..wjs-production)"
  [[ "$ahead" -gt 0 ]] || return 1
  printf '%s\n' "$version"
}
```

In `main`, drop the `git tag -a` call from inside the `else` (fresh-release) branch, and instead call `git_tag_release_or_verify` once, unconditionally, right after the `if release_already_prepared_locally; then ... else ... fi` block — so both the fresh and the resumed path go through it:

```bash
  # Both paths converge here, and tagging is idempotent: a run interrupted
  # between the release commit and the tag gets its tag on the next run,
  # rather than the next run re-preparing the release from scratch.
  git_tag_release_or_verify "v${release_version}" "Release ${release_version}" wjs-production || return 1
```

Add to `scripts/tests/test_resume.sh`: a run interrupted between the release commit and the tag (simulated by dropping the tag a prior run created) must still be recognised as a resume and get its tag recreated, not treated as a fresh release.

- [ ] **Step 5: Run tests to verify they pass**

Run: `bash scripts/tests/run_tests.sh`
Expected: every block `N run, 0 failed`; overall exit code `0` (108 assertions total across the suite at this point).

- [ ] **Step 6: Commit**

```bash
git add scripts/release.sh scripts/tests/
git commit -m "fix: harden changelog dedup, cross-project attribution, and idempotent tagging"
```

---

## Manual rollout (outside TDD scope — human-gated, per `.claude/rules/version-control.md` and the design's own rollout plan)

These are **not** implementation tasks — they involve real, hard-to-reverse GitLab/git state and must be done by the user, not autonomously:

1. **Authenticate `glab` for this host** (currently missing, per the design spec's own findings): `glab auth login --hostname gitlab.sissamedialab.it`.
2. **Real dry-run validation**: clone this repo to a scratch directory, run `scripts/release.sh` there (its own `origin` remote, so pushes are harmless to retry, but still confirm `n` at both gates for a pure dry run), and diff the generated `CHANGELOG.md` section against the real `v2.0.19` entry (`git show 6e549f08:CHANGELOG.md` in this repo) for format parity.
3. **Real release run** in this repo, once satisfied — this is the first time the script pushes for real; the user drives the two `y/n` gates.
4. **Copy `scripts/release.sh` into the 4 sibling repos** (`wjs-search-user`, `wjs-submission-project`, `wjs-themes`, `wjs-utils-project`, all checked out as siblings of this repo). Per `.claude/rules/version-control.md`, each of those is a separate repository needing its own issue and branch — ask the user for (or file) an issue number per repo before branching there; do not reuse this repo's branch/issue for those commits.

---

## Self-Review

**Spec coverage** — every numbered item in `docs/superpowers/specs/2026-07-17-release-script-design.md` maps to a task:
- Preflight (tools/clean-tree/host+project/auth/branches) → Task 8, wired in Task 11.
- Steps 1–2 (sync, capture prev tag) → Task 9 (`git_ff_branch`), wired in Task 11.
- Step 3/8 (merge with resumability + conflict handling) → Task 9 (`git_merge_or_skip`), wired in Task 11.
- Step 4 (release version) / Step 9 (dev version) → Task 2.
- Step 5 (changelog, `related_issues` + 404-fallback probe, prepend, duplicate-section guard, `CHANGELOG_FS`/cross-project fix) → Tasks 3, 5, 6, 7, hardened in Task 14.
- Step 6 (version bump, pre-commit, `git add -A`, commit) → Task 11, hardened in Task 13.
- Step 7 (idempotent tag) → Task 14 (`git_tag_release_or_verify`), superseding Task 11's plain `git tag -a`.
- Steps 10–11 (confirm + push gates) → Task 10, wired in Task 11.
- Resume detection independent of the tag (`release_already_prepared_locally`) → introduced in Task 13, corrected in Task 14 to drop the tag requirement.
- `+suffix` preservation, no-`v`-in-commit, "Release ..." wording, never-fabricate-URLs → asserted directly in Task 2/3/7/11 tests.
- Rollout/validation plan → the "Manual rollout" section above (explicitly kept out of the automated task list, since it involves live GitLab auth and irreversible pushes).
- Out of scope per the design (towncrier, GitLab Release objects) → correctly absent from every task.

**Placeholder scan** — no task contains "TBD", "similar to Task N", or unshown code; every step that touches code includes the literal diff/content to add.

**Type consistency** — function names and signatures were cross-checked across tasks: `gitlab_related_issues_json` (Task 5) is consumed as-is by Task 7; `changelog_extract_issue_refs`/`changelog_resolve_issue_ref` (Task 6) match Task 7's fallback branch exactly; `git_merge_or_skip` (Task 9) is the only merge entrypoint `main` (Task 11) calls, for both directions; `CHANGELOG_FS` (Task 14) replaces every tab-separated `read`/`@tsv` introduced in Tasks 5 and 6, consistently, not just at one call site.

**Post-implementation note:** Tasks 13 and 14 were not part of the original task sequence — they document fixes applied after Task 12's rollout-readiness point, found by a final whole-branch review (Task 13) and a later manual pass (Task 14). They're recorded here, after the fact, in the same task format as Tasks 1–12 so this document stays a complete and accurate build log of `scripts/release.sh` as it actually ships, not just as originally planned. Anyone re-deriving this script from scratch should implement Tasks 1–12 in order, then apply Task 13 and Task 14 as the corrections they are — do not skip them as "already superseded, therefore optional."
