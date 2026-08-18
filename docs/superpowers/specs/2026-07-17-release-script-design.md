# Design: `scripts/release.sh` — bash replacement for the `releasing-wjs-python-package` skill

## Context

`.claude/skills/releasing-wjs-python-package/SKILL.md` describes the
`wjs-develop` → `wjs-production` release flow for `wjs-*` Python packages:
merge, version bump, changelog built from merged GitLab MRs/issues, tag, merge
back, dev-version bump. It's currently executed by Claude reading the skill
and improvising each `git`/GitLab MCP call.

This spec converts it into a standalone bash script that a human (or CI) can
run directly, with no Claude/MCP dependency, while keeping every convention
the skill enforces (exact commit wording, tag format, changelog format).

The same `wjs-develop`/`wjs-production` + `setup.cfg`-version pattern exists
in `wjs-search-user`, `wjs-submission-project`, `wjs-themes`, and
`wjs-utils-project` — the script is designed to be copied as-is into all five
repos, not just this one.

## Non-goals

- Not for `janeway` — it has no `setup.cfg`/version-in-setup.cfg convention
  and isn't a `wjs-*` library package.
- No towncrier / changelog-fragment adoption in this effort — that changes
  how every MR is authored and is a separate follow-up if ever pursued.
- No GitLab Release object creation — tag-only, matching current behavior.

## Requirements confirmed with the user

- Fully standalone: the script authenticates to GitLab itself (via `glab`,
  not MCP), so it works outside a Claude session or from CI.
- GitLab access via the `glab` CLI (`glab api ...` for endpoints without a
  dedicated subcommand), not raw `curl`+`jq` scaffolding.
- Changelog data comes from GitLab's `closes_issues` and `related_issues` MR
  endpoints instead of regex-parsing MR description text (see below).
- Generic across all five `wjs-*` repos; checked into each repo's own
  `scripts/release.sh` (copied file, not a shared external script).
- Single command, no `prepare`/`push` split — interactive `y/n` prompts at
  the two push gates, matching the skill's current confirm-with-the-user
  behavior.
- No GitLab Release object — tag-only.
- A `+suffix` local version marker (e.g. `2.0.16.dev1+ally1`, present in 3 of
  the 5 repos) is preserved unchanged through both the release version and
  the next dev version — treated as a permanent per-repo marker, not
  something the release process touches.

## Changelog data source — API research

- `issues_mentioned_but_not_closing` (GitLab MR `!52711`) was **never
  merged** — it's a 2021 draft and does not exist in the real API. Do not
  build against it.
- `related_issues` (`GET /projects/:id/merge_requests/:merge_request_iid/related_issues`,
  shipped via GitLab MR `!155422`) is real and documented. It collects
  issues referenced in the MR's title, description, commits, and notes —
  both closing and merely-mentioned — in one call, and each returned issue
  object already carries `title`, `web_url`, and `references.full`
  (`group/project#iid`), so no separate per-issue lookup is needed.
- `closes_issues` (`GET /projects/:id/merge_requests/:merge_request_iid/closes_issues`)
  returns the subset that will actually close on merge. GitLab's
  auto-close-on-merge behavior requires the MR to target the project's
  **default branch** — confirmed via `get_project` that
  `wjs-profile-project`'s default branch is `wjs-develop` (with
  `autoclose_referenced_issues: true`), which is exactly where feature MRs
  land, so this endpoint should populate correctly for this workflow.
- Availability caveat: this is a self-hosted instance and its GitLab version
  couldn't be confirmed without auth. `related_issues` is recent enough that
  an older self-hosted install might 404. The script must probe it once and
  fall back to the pre-existing description-regex approach if so, rather
  than hard-failing.
- `glab` is installed on the dev machine (`v1.36.0`) but only authenticated
  for `gitix.iast.it` / `srv-gitlab.er-go.it` — **not**
  `gitlab.sissamedialab.it` yet. The script's preflight must check
  `glab auth status --hostname <repo's host>` and fail with a clear
  "run `glab auth login --hostname <host>`" message if not authenticated,
  rather than a confusing API error later.

## Script structure

Single file, `scripts/release.sh`, run from the repo root on any branch. No
subcommands, no flags required for normal use. Bash + `git` + `glab` + `jq` +
`pre-commit`; no Python/other runtime needed.

### Preflight

1. Required tools present: `git`, `glab`, `jq`, `pre-commit` — else abort
   with an install hint.
2. Clean working tree (`git status --porcelain` empty) — else abort.
3. Determine GitLab host + project path from `git remote get-url origin`.
4. `glab auth status --hostname <host>` succeeds — else abort with the
   `glab auth login` hint.
5. Both `wjs-develop` and `wjs-production` exist locally or on `origin`.

### Steps (mirrors the skill 1:1 unless noted)

1. **Sync.** `git fetch origin`; fast-forward `wjs-develop` and
   `wjs-production` to `origin/<branch>`. Divergence (local commits origin
   lacks) aborts with a status dump — never force anything.
2. **Capture previous tag.**
   `git describe --tags --abbrev=0 wjs-production`.
3. **Merge develop → production.**
   `git switch wjs-production && git merge --no-ff wjs-develop -m "Merge branch 'wjs-develop' into wjs-production"`.
   - *Resumability:* if a previous run left this merge conflicted and the
     user resolved + committed it by hand, detect that `wjs-production`'s
     tip is already a merge whose parents include the old
     `wjs-develop` tip, and skip straight to step 4 instead of
     re-merging.
   - On conflict: stop with the tree mid-merge and print resolve
     instructions (`git add`, `git commit`, then re-run the script). Do not
     auto-abort, do not use `-X ours`/`-X theirs`.
4. **Determine release version.** Read `version =` from `setup.cfg`
   (`[metadata]` section). Regex-split into
   `<core>(.dev<N>)?(+<suffix>)?`. Release version = `<core>` +
   `+<suffix>` if present (dev part dropped, suffix preserved).
5. **Build the changelog.**
   - `git log <prev-tag>..wjs-develop --first-parent --format='%H%x00%B%x00---%x00'`
     (NUL-delimited to survive multi-line bodies safely). **Correction to the
     original design**, found during implementation (see the implementation
     plan's Task 7 design note): the traversal walks `wjs-develop`'s own
     first-parent history, not `wjs-production` filtered to `--merges`— the
     literal command first proposed here structurally excludes every
     non-merge commit, which contradicts this same step's "non-merge commits
     in range → listed by subject" requirement below. Each commit is
     classified as: (a) has a `See merge request ...!<iid>` trailer → a real
     MR, build changelog entries for it; (b) no trailer, 2+ parents → this
     script's own merge-back from a prior cycle, skip silently; (c) no
     trailer, single parent, subject starts with `Release ` → this script's
     own release/dev-bump commit, skip silently; (d) no trailer, single
     parent, anything else → a genuine direct push, one plain-subject line.
   - Per commit with a trailer: extract MR IID from the
     `See merge request <project>!<iid>` trailer.
   - `glab api "projects/:id/merge_requests/<iid>/related_issues"`; if that
     404s, fall back to parsing the MR description (fetched via
     `glab api "projects/:id/merge_requests/<iid>"`) for `Closes #`/`Fixes`/
     shorthand refs/full URLs — same patterns as today's skill.
   - `related_issues` alone is sufficient: it already returns every issue
     connected to the MR (closing or merely mentioned) with `title`,
     `web_url`, and `references.full` (`group/project#iid`) on each. There
     is no need to also call `closes_issues` — that endpoint was only
     useful during research to confirm auto-close would fire on this
     workflow's target branch, not as a data source for the script.
   - **Field separator hardening (post-implementation fix):** the JSON→shell
     handoff for each related issue uses a literal unit separator (`$'\x1f'`,
     `CHANGELOG_FS` in the script) instead of a tab. A tab is IFS-whitespace,
     so `IFS=$'\t' read` silently collapsed an empty leading field (e.g. a
     null/missing `references.relative`) into the next field, shifting the
     issue's title into the ref slot and leaving the URL slot empty. `0x1f`
     is never IFS-whitespace, so empty fields survive the round-trip intact.
   - **Cross-project issue attribution (post-implementation fix):** the
     project/iid shown in each entry is derived from the issue's `web_url`
     (via the segment immediately before `/-/`), not from
     `references.relative`. `references.relative` is relative to the
     *issue's own* project, so a cross-project issue (e.g. an issue in
     `wjs/specs` linked from an MR in this repo) also arrives as a bare
     `#iid` — indistinguishable from a same-project reference — and was
     previously mislabeled with this repo's own project name while still
     linking to the other project's URL. `web_url` is the only field
     guaranteed to agree with the link the entry actually carries.
   - No resolvable issue → `- No linked issue — <MR title> (!<iid>)`. Never
     fabricate a URL.
   - Non-merge commits in range → listed by subject, no issue line.
   - Format per entry:
     `- [<project>#<issue-iid>: <issue title>](<issue web_url>) — <MR title> (!<mr-iid>)`
   - Prepend `## [<version>] - <YYYY-MM-DD>` section to `CHANGELOG.md`
     (create the file if absent). **Duplicate-section guard
     (post-implementation fix):** refuses (returns non-zero, changelog left
     untouched) instead of prepending if `CHANGELOG.md` already has a
     `## [<version>]` section — a release run re-prepared after already
     writing that section (interrupted before finishing, or re-run after the
     release was already pushed) used to stack a second, near-identical copy
     and commit it as a second "Release <version>" commit.
6. **Version bump + release commit.** Set `setup.cfg`'s `version` to the
   release version. Run `pre-commit run --all-files`; if it modifies files,
   `git add -A` (not just `setup.cfg`/`CHANGELOG.md`) so files `pre-commit`
   reformatted outside those two are not left stranded uncommitted — a gap
   found post-implementation that could abort a later `git switch` after the
   release was already tagged. `git commit -m "Release <version>"` — no `v`
   prefix in the commit message.
7. **Tag.** `git tag -a v<version> -m "Release <version>"`, made idempotent
   (post-implementation fix, `git_tag_release_or_verify` in the script):
   tagging is a separate step from the release commit, so a run interrupted
   between the two used to leave an untagged release commit that a plain
   `git tag -a` would then abort on (tag "already exists" under `set -e`) on
   the next attempt. The idempotent version accepts a tag that already
   points at the target commit and only fails when it points somewhere
   else — a state that needs a human decision. This step runs unconditionally
   after step 3–6 (fresh) or the resume check below converge, so a resumed
   run still gets its tag created.
8. **Merge back.**
   `git switch wjs-develop && git merge --no-ff wjs-production -m "Merge branch 'wjs-production' into wjs-develop"`.
   Same conflict/resumability handling as step 3.
9. **Dev version bump.** Patch component of `<core>` + 1, append
   `.dev1`, reapply `+<suffix>` if present. Commit as
   `git commit -m "Release <new-dev-version>"` (same "Release ..." wording,
   not "Bump version").
10. **Confirm before pushing `wjs-develop`.** Print
    `git log origin/wjs-develop..wjs-develop --oneline` and prompt
    `y/n`. Abort cleanly on `n` (nothing pushed, all local commits remain
    for manual follow-up).
11. **Confirm before pushing `wjs-production` + tag.** Print what will be
    pushed (`git log origin/wjs-production..wjs-production --oneline` and
    `git tag --points-at wjs-production`), prompt `y/n`, then
    `git push origin wjs-production wjs-develop` and `git push origin v<version>`.

### Idempotency / resumability summary

Every step checks whether its effect is already present (tag exists, commit
message matches expected pattern at HEAD, merge already has the expected
parent) before acting, so re-running the script after a manual conflict
resolution — or after saying `n` at a confirm prompt — continues from the
right place instead of redoing or duplicating work.

**Resume detection (post-implementation fix):** the check that decides
whether to skip straight to the push gates (`release_already_prepared_locally`
in the script) does **not** require the `v<version>` tag to exist. It
originally did, but step 7 creates the tag one step *after* the release
commit, so a run interrupted in that exact window left a commit the guard
couldn't recognise — and the next run re-prepared the whole release from
scratch, stacking a duplicate changelog section and a second, identically-
titled "Release \<version\>" commit. The guard instead checks: `HEAD`'s
subject on `wjs-production` is a `Release <version>` commit, `setup.cfg` at
that commit records the same `<version>`, and `wjs-production` is still
ahead of `origin` (excluding the normal, already-pushed state every release
sits in between cycles). Making step 7's tagging itself idempotent (see
above) then covers the tag independently of this check, on both the fresh
and the resumed path.

## Edge cases (from the skill's "Common mistakes" table — must still hold)

| Case | Script behavior |
|---|---|
| Commit message `Release v2.0.19` | Never produced — script always omits `v` in commit messages, only the tag gets it |
| Dev-bump commit named "Bump version to ..." | Never produced — always "Release \<version\>" |
| Placeholder/fabricated issue URLs | Never produced — unresolvable refs always become "No linked issue" |
| Pushing immediately after committing | Never — both pushes are behind explicit `y/n` prompts |
| Skipping `pre-commit` | Always run on the release commit |
| Semver-bumping instead of stripping `.devN` | Version logic only ever strips `.devN`/reapplies it; never bumps minor/major |
| `+suffix` local version markers | Preserved unchanged through release and next-dev versions |
| Interrupted between the release commit and the tag | Resume is detected without needing the tag (via `setup.cfg`'s version at `HEAD`); the tag is (re-)created idempotently on the next run |
| Cross-project issue linked from an MR (bare `#iid` relative ref) | Attributed to the issue's own project via its `web_url`, never mislabeled with this project's name |
| Re-running after `CHANGELOG.md` already has this version's section | Refused with a clear message instead of stacking a duplicate section/commit |

## Rollout / validation plan

1. Implement and dry-run against `wjs-profile-project` (this repo), since
   its full release history is available for comparison.
2. Compare the script's generated changelog section against the actual
   `v2.0.19` (or latest) release commit's `CHANGELOG.md` diff for format
   parity.
3. Do a real release run once satisfied.
4. Copy `scripts/release.sh` verbatim into `wjs-search-user`,
   `wjs-submission-project`, `wjs-themes`, `wjs-utils-project`.

## Follow-ups (explicitly out of scope here)

- Towncrier-based changelog fragments authored per-MR, replacing git
  archaeology at release time entirely.
- GitLab Release objects (notes attached to the tag, visible in
  Deployments > Releases).
