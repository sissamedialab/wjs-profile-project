---
description: Version control rules — branch naming/workflow, GitLab flow branching model, Conventional Commits, MR process
---

# Version Control Rules

The conventions for branches, commits, and merge requests in this repo. They apply
repo-wide — follow them whenever you create a branch or write a commit/MR. Every
feature or fix must be developed on its own branch; **never commit work directly to
`wjs-develop`** (this repo's main/integration branch) or to `wjs-production`.

## Branching model

Nephila's company-wide convention follows the GitLab flow (flexibly): one primary
merge branch, with all development happening in separate **feature branches**, always
integrated via a **Merge Request** — never committed directly to the main or
deployment branches, except for CI/deployment fixes. This repo's actual model (see
`branches.md` at the repo root for the full picture — release cadence, hotfix
procedures, test-environment mapping — it documents current practice but isn't
committed to the repo) instantiates that convention as follows:

- **Main/integration branch** — `wjs-develop`. All feature and bugfix branches merge
  back into it via MR (see *Workflow* below).
- **Release/deployment branch** — `wjs-production`. Not `release/<version>` or
  `support/<version>` — `wjs-production` itself is released **weekly, every Tuesday**,
  via a merge MR from `wjs-develop`, cut as a tag, and deployed through the usual
  tag → pre-production → production procedure. A short freeze window precedes each
  Tuesday release (only critical hotfixes may land in `wjs-develop` during it).
- **Integration branch** — used for a complex or cross-repo feature instead of a
  plain feature branch (decided at planning time, based on estimated dev/UAT time):
  one per affected repo, sharing the same branch name, with a single coordinating
  owner. It is validated on a dedicated `t1`–`t5` test environment and merges into
  `wjs-develop` through a real, reviewable MR like any other feature branch — it is
  **not** merged locally and is **not** disposable.
- **Hotfixes** — bypass the weekly release only when the change affects production
  now/imminently *and* can't wait for Tuesday; require the tech lead's explicit
  approval and a `hotfix` tag. Target `wjs-production` directly via its own branch,
  or (if `wjs-production` itself has an unreleased regression) a `wjs-hotfix` branch
  cut from the last production tag — never a `release/<version>`-style branch.
  Every active integration-branch owner must rebase onto the new base the same day
  a hotfix is forward-ported.

## Branch naming

```
feature/issue-<issue-number>-<short-name>
bugfix/issue-<issue-number>-<short-name>
```

- **Prefix** — `feature/` for new functionality, `bugfix/` for fixing defects.
  (`doc/`, `removal/`, and `misc/` are also acceptable for documentation-only,
  API-removal, and general cleanup work respectively, following the same
  `issue-<number>-<short-name>` suffix.)
- **`<issue-number>`** — the GitLab issue the branch implements. Mandatory — this is
  how branches, MRs, and the issue tracker stay linked (this repo ties branches to
  GitLab issues, not Taiga/Sentry).
- **`<short-name>`** — a few kebab-case words describing the work.

Examples: `feature/issue-2805-improve-activity-page`,
`bugfix/issue-2568-load-jcap-keywords`.

Branch names are always written in **English**, never Italian — even when the
working conversation is in Italian. One issue → one branch; if a change spans
multiple issues, prefer separate branches.

## Workflow

1. Make sure `wjs-develop` is up to date, then branch from it:
   ```bash
   git switch wjs-develop && git pull
   git switch -c feature/issue-1234-my-feature
   ```
2. Do the work on that branch; keep commits scoped to the issue.
3. Run `pre-commit run --all-files` before committing (style is CI-enforced — see
   `.claude/rules/linting.md`).
4. Open a merge request back into `wjs-develop` referencing the issue (see
   *Merge request description* below) — unless the change is a hotfix or belongs to
   an integration branch, in which case follow the *Branching model* rules above for
   the correct target.

## Commit messages (and MR titles)

Use [Conventional Commits](https://www.conventionalcommits.org/). The same format
applies to merge request titles and descriptions. Commit messages and MR
titles/descriptions are always written in **English**, never Italian.

```
<type>(<scope>): <subject>

<body>

<footer>
```

- `<type>` — **required**. `feat` (new feature → MINOR) or `fix` (bug fix → PATCH).
  Also allowed (no version effect): `build`, `chore`, `ci`, `docs`, `perf`,
  `refactor`, `revert`, `style`, `test`.
- `<scope>` — optional noun in parentheses naming the affected area, e.g.
  `fix(parser):`.
- `<subject>` — short summary after the colon and space.
- `<body>` — optional, after a blank line; the *why* and extra context.
- `<footer>` — optional git trailers (`key: value`); the place for issue references
  and breaking changes.

**Breaking change** (→ MAJOR): either a `!` before the colon (`feat(api)!: ...`) or a
`BREAKING CHANGE: <description>` footer (token in uppercase). With `!`, the footer may
be omitted.

Examples:

```
feat(catalog): add lancio document type
fix: make parameters not required
docs: update CLAUDE.md with MR guidelines
chore: release 5.4.0
```

## Committing

The commit cadence is agreed at flow start (step 4 of `/nephila-flow`): by default
development leaves changes in the working tree and Claude presents the files and
proposed message for each commit for your review at the end; if you opt for
per-change commits, each commit still follows the rules above and the end-of-flow
review covers the resulting series. Claude decides how many commits to propose.
**Push happens only after your explicit confirmation.**

## Merge request description

Open MRs against `wjs-develop` (or the correct target per *Branching model* above)
and use the project's actual MR template at
`.gitlab/merge_request_templates/base.md` as the description base — fill every
section, leave no placeholder text. The template is:

```markdown
## Description

Full text description of the solution applied

This applies only for code changes (Python or JS)

It can be skipped if proper inline comments are added

## Page/URL

Add here an example URL (when applicable) that brings to the page subject of the MR

*

## Issues Items [Reference syntax <repository>#number]:

*
*

## Required actions:

* [ ] migrations
* [ ] manual changes on database (if so, which)
* [ ] new dependencies

### Actions details


## Other notes
```

Note this repo's template does **not** carry a dedicated "AI Usage" section, and
there is no `mr-ai-reminder.sh` (or similar) hook enforcing one here — that's a
generic Nephila convention that doesn't apply to this repo's committed template as-is.

When opening the MR: title in Conventional Commits style; apply the `State: QA`
label when it's ready for review; assign the MR to yourself; add as reviewer the
person the user specifies — always ask the user for the reviewer, never pick one
yourself; populate `## Issues Items [Reference syntax <repository>#number]:` with
the GitLab issue reference the branch ties back to (see *Branch naming* above), in
`<repository>#<number>` form (e.g. `wjs-profile-project#2931`) — **never** the full
issue URL, otherwise GitLab's API `related_issues` won't pick the issue up and the
MR won't be linked to it; check off `## Required actions:` items that apply and
describe them under `### Actions details`. The MR title and description are always
in **English**. Let the user validate the description and the **target branch**
before opening the MR — never assume or pick the target branch yourself.

## pre-commit

- Install once, globally: `pipx install pre-commit`.
- Enable per project at clone: `pre-commit install --install-hooks`.
- Run before committing: `pre-commit run --all-files`.

Tool stack, where each tool's config lives: `.claude/rules/linting.md`.

---

*These rules mirror the company knowledge base (`nephila_gitlab` →
`docs/workflow/development/` (index.md, commit-message.md) and
`docs/practices/guidelines-python.md`), adapted above wherever this repo's actual
practice (`branches.md`, `.gitlab/merge_request_templates/base.md`) diverges from
the generic convention.*
