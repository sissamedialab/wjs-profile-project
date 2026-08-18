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

# --- gitlab related_issues data fetch ----------------------------------------

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

# Field separator for every jq -> shell handoff below: a literal unit
# separator (0x1f), NOT a tab. Tab is an IFS-whitespace character, so
# `IFS=$'\t' read` collapses runs of tabs and strips leading ones — one empty
# field shifts every later field left by a slot. Same trap already documented
# in version_release_from_dev; here it silently turned an issue with no
# `references.relative` into an entry whose ref was the issue title and whose
# URL was empty. 0x1f is not IFS-whitespace, so empty fields are preserved.
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

# --- changelog description-regex fallback -----------------------------------
# Used only if gitlab_related_issues_json reports exit 44 (endpoint 404s on
# an older self-hosted GitLab). Mirrors the reference patterns the
# releasing-wjs-python-package skill already parsed by hand.

changelog_extract_issue_refs() {
  # changelog_extract_issue_refs - extract issue references from a merge request description
  #
  # Parses a merge request description to find all issue references using three patterns:
  # 1. Full URLs to issues/work_items (e.g., https://gitlab.com/group/project/-/issues/123)
  # 2. Cross-project references (e.g., other-project#456)
  # 3. Same-project references (e.g., #789)
  #
  # This function serves as a fallback when the GitLab API's related_issues endpoint
  # is unavailable (returns 404 on older self-hosted instances). It extracts references
  # that would normally be fetched via the API, enabling changelog generation on legacy
  # GitLab versions.
  #
  # Arguments:
  #   $1 - merge request description text to parse
  #   $2 - short name of the current project (used to qualify same-project #N references)
  # Returns:
  #   0 always (prints one issue reference per line in "project-short#iid" format to stdout)
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
  # changelog_resolve_issue_ref - resolve an issue reference to its title and URL
  #
  # Queries the GitLab API to fetch the title and web URL for a given issue reference.
  # The reference format is "project-short#iid" (e.g., "wjs-profile#123"), which is
  # split into project name and issue IID, then combined with the group prefix to form
  # the full project path for the API call.
  #
  # This function is used as a fallback when the related_issues API endpoint is
  # unavailable (404 on older GitLab instances), allowing changelog generation to
  # resolve issue references extracted from merge request descriptions.
  #
  # Arguments:
  #   $1 - GitLab hostname (e.g., "gitlab.com" or self-hosted instance domain)
  #   $2 - group prefix path (e.g., "sissa/wjs" for project path construction)
  #   $3 - issue reference in "project-short#iid" format (e.g., "wjs-profile#123")
  # Returns:
  #   0 if issue was successfully resolved (prints title and URL to stdout, separated
  #     by CHANGELOG_FS — see the comment on that variable for why not a tab)
  #   1 if API call failed or issue does not exist (no output, error suppressed to /dev/null)
  local host="$1" group_prefix="$2" ref="$3" project_short issue_iid project_path json
  project_short="${ref%%#*}"
  issue_iid="${ref##*#}"
  project_path="${group_prefix}/${project_short}"
  if json="$(glab api "projects/$(url_encode "$project_path")/issues/${issue_iid}" --hostname "$host" 2>/dev/null)"; then
    jq -r --arg fs "$CHANGELOG_FS" '[.title, .web_url] | join($fs)' <<< "$json"
  else
    return 1
  fi
}

# --- changelog section builder ----------------------------------------------

changelog_extract_mr_iid() {
  local body="$1"
  if [[ "$body" =~ See\ merge\ request\ [^\!]*\!([0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  fi
}

changelog_build_section() {
  # changelog_build_section - generate a changelog section for a release version
  #
  # Constructs a formatted changelog section by walking the git history from the
  # previous tag to the develop branch tip, extracting merge request information,
  # and linking related issues. For each merge commit, it attempts to fetch related
  # issues via the GitLab API's related_issues endpoint. If that endpoint is
  # unavailable (404 on older GitLab instances), it falls back to parsing issue
  # references from merge request descriptions using regex patterns.
  #
  # The function filters out non-GitLab merges (e.g., this script's own merge-backs)
  # and release commits, formatting only user-contributed changes as changelog entries.
  # Each entry includes issue references, titles, URLs, and associated merge request
  # information in a consistent Markdown format.
  #
  # Arguments:
  #   $1 - GitLab hostname (e.g., "gitlab.com" or self-hosted instance domain)
  #   $2 - full project path (e.g., "sissa/wjs/wjs-profile")
  #   $3 - group prefix path (e.g., "sissa/wjs" for issue reference resolution)
  #   $4 - previous release tag (e.g., "v2.0.18" - starting point for history walk)
  #   $5 - develop branch name (e.g., "wjs-develop" - ending point for history walk)
  #   $6 - new release version (e.g., "2.0.19" - used in section header)
  #   $7 - release date in YYYY-MM-DD format (e.g., "2026-07-18" - used in section header)
  # Returns:
  #   0 on success (prints formatted changelog section to stdout)
  #   1 if fetching related issues encounters a hard failure (404 triggers fallback, not failure)
  local host="$1" project_path="$2" group_prefix="$3" prev_tag="$4" develop_branch="$5" version="$6" date="$7"
  local own_project_short related_issues_supported=1
  own_project_short="$(project_short_name "$project_path")"

  changelog_section_header "$version" "$date"
  printf '\n'

  local sha body mr_iid json status subject parent_count _separator
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
        if IFS="$CHANGELOG_FS" read -r title url < <(changelog_resolve_issue_ref "$host" "$group_prefix" "$ref" 2>/dev/null); then
          changelog_format_entry "$ref" "$title" "$url" "$mr_title" "$mr_iid"
          found=1
        fi
      done <<< "$refs"
      [[ "$found" -eq 0 ]] && changelog_format_no_issue_entry "$mr_title" "$mr_iid"
    fi
  done < <(git log "${prev_tag}..${develop_branch}" --first-parent --format='%H%x00%B%x00---%x00')
}

# --- preflight ---------------------------------------------------------------

preflight_check_tools() {
  # preflight_check_tools - verify all required external tools are installed
  #
  # Checks that git, glab, jq, and pre-commit are present in PATH. These tools
  # are essential for the release script to function: git for repository
  # operations, glab for GitLab API access, jq for JSON parsing, and pre-commit
  # for code formatting hooks.
  #
  # Arguments: none
  # Returns:
  #   0 if all tools are available
  #   1 if one or more tools are missing (error message printed to stderr)
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
  # preflight_check_clean_tree - verify the git working tree has no uncommitted changes
  #
  # Ensures the repository working tree is clean before starting the release process.
  # A clean tree is required to avoid mixing release commits with unrelated changes
  # and to ensure all modifications are properly tracked and attributed.
  #
  # Arguments: none
  # Returns:
  #   0 if the working tree is clean (no modified, staged, or untracked files)
  #   1 if there are uncommitted changes (error message and git status printed to stderr)
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "release.sh: working tree is not clean. Commit or stash changes first." >&2
    git status --short >&2
    return 1
  fi
}

preflight_check_branches() {
  # preflight_check_branches - verify required branches exist locally or on origin
  #
  # Ensures both wjs-develop and wjs-production branches exist either as local
  # branches or as remote-tracking branches on origin. These branches are essential
  # for the release workflow: wjs-develop is the source of changes to be released,
  # and wjs-production is the target branch for stable releases.
  #
  # Arguments: none
  # Returns:
  #   0 if both wjs-develop and wjs-production exist locally or on origin
  #   1 if either branch is missing (error message printed to stderr)
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
  # preflight_check_gitlab_auth - verify authentication to the GitLab host
  #
  # Ensures the user is authenticated to the GitLab instance before attempting
  # any API operations. Authentication is required for fetching merge request data,
  # issue information, and other GitLab resources during the changelog generation
  # and release process.
  #
  # Arguments:
  #   $1 - GitLab hostname (e.g., "gitlab.com" or a self-hosted instance domain)
  # Returns:
  #   0 if authenticated to the specified GitLab host
  #   1 if not authenticated (error message with login instructions printed to stderr)
  local host="$1"
  if ! gitlab_check_auth "$host"; then
    echo "release.sh: not authenticated to $host. Run: glab auth login --hostname $host" >&2
    return 1
  fi
}

# --- git flow helpers --------------------------------------------------------

git_ff_branch() {
  # git_ff_branch - switch to a branch and fast-forward it to match origin
  #
  # Ensures the specified branch exists locally and is synchronized with its
  # remote counterpart on origin using a fast-forward merge. If the branch
  # doesn't exist locally, it is created tracking origin/<branch>. If it exists
  # but has diverged from origin (fast-forward merge fails), the function reports
  # an error and instructs the user to resolve the divergence manually.
  #
  # Arguments:
  #   $1 - branch name to switch to and synchronize (e.g., "wjs-develop")
  # Returns:
  #   0 if the branch was successfully switched to and synchronized
  #   1 if the branch has diverged from origin (error message and git status printed to stderr)
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
    echo "release.sh: merge of $source into $target did not complete (conflict or other git failure)." >&2
    echo "Resolve it (check 'git status'), then: git add <files> && git commit, and re-run this script." >&2
    return 1
  fi
}

# --- interactive push gates ---------------------------------------------

release_confirm_develop_ready() {
  local develop_branch="$1"
  echo "--- commits ready to push on $develop_branch ---"
  git log "origin/${develop_branch}..${develop_branch}" --oneline
  local answer
  read -r -p "Continue toward pushing $develop_branch? [y/N] " answer
  if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "release.sh: aborted. Nothing pushed; everything prepared locally is safe — re-run this script to pick up where you left off."
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
    echo "release.sh: aborted. Nothing pushed; everything prepared locally is safe — re-run this script to pick up where you left off."
    return 1
  fi
  git push origin "$production_branch" "$develop_branch"
  git push origin "$tag"
}

# --- entrypoint -------------------------------------------------------------

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
    changelog_prepend_section CHANGELOG.md "$section" || return 1

    sed -i "s/^version = .*/version = ${release_version}/" setup.cfg
    pre-commit run --all-files || true
    git add -A
    git commit -m "Release ${release_version}"
  fi

  # Both paths converge here, and tagging is idempotent: a run interrupted
  # between the release commit and the tag gets its tag on the next run,
  # rather than the next run re-preparing the release from scratch.
  git_tag_release_or_verify "v${release_version}" "Release ${release_version}" wjs-production || return 1

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

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
