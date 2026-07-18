---
name: github-issue
description: "Use when the user references one or more GitHub issues (e.g. 'fix #42', 'work on #10 and #11', 'resolve the bug') OR when no issue is mentioned but an implementation task is in progress. Manages the full GitHub issue lifecycle: pull context → mark in-progress → implement → verify criteria → mark in-review. Supports multiple issue numbers in one invocation."
origin: user
---

# github-issue

Full GitHub issue lifecycle management for implementation tasks. Accepts one or more issue numbers.

## Phase 0: Resolve Repository Context

Before anything else:

1. Resolve `owner`/`repo` from the git remote:
   ```
   git remote get-url origin
   ```
   Strip the `git@github.com:` or `https://github.com/` prefix and the trailing `.git` to get `owner/repo`.
   - If the remote is not a `github.com` URL: **STOP** and ask the user to confirm the correct GitHub remote — this skill requires one.
2. Call `mcp__github__get_me()` to get the acting username. Cache it as `<ME>`.

Cache `owner`, `repo`, and `<ME>` for the rest of the run.

## Argument Parsing

Extract all issue numbers from the invocation. Accepted formats:

- Single: `/github-issue #42` or `/github-issue 42`
- Space-separated: `/github-issue #42 #43`
- Comma-separated: `/github-issue #42, #43`
- Mixed prose: `fix #10 and #11` → extract `[10, 11]`
- No number → empty list → trigger Phase B

Collect them into an ordered list: `ISSUES = [<number>, ...]`

## Decision Tree

```
ISSUES list non-empty?
├── YES → Phase A for each issue (see execution order below)
└── NO  → Phase B: Discover or Create Issues
```

## Multi-Issue Execution Order

When `ISSUES` has more than one entry:

1. **Pull & enrich all issues first** (A1–A4 for every issue in parallel where possible) before writing any code — ensures full cross-issue context before implementation starts.
2. **Post a combined analysis comment** on each issue referencing the others: "Working alongside #43."
3. **Implement together** — treat the set as one coherent unit of work; avoid duplicating changes across issues.
4. **Mark each issue in-review** individually once its own acceptance criteria are met (they may complete at different times).

---

## Phase A: Pull & Enrich Issue

### A1. Fetch the Issue (with retry)

```
mcp__github__issue_read(method: "get", owner: "<owner>", repo: "<repo>", issue_number: <N>)
```

- On failure: retry up to **3 times** with 2-second gaps.
- After 3 failures: **STOP and report** to the user:
  > "Could not fetch issue #<N> after 3 attempts. Context is insufficient to proceed safely. Please verify the issue number or GitHub connectivity."

### A2. Pull All Context

After fetching the issue, pull every piece of context available:

1. **Comments** — `mcp__github__issue_read(method: "get_comments", ...)`
2. **Labels** — `mcp__github__issue_read(method: "get_labels", ...)`
3. **Hierarchy** — if the issue's `has_parent`/`has_children` flags are set, pull `get_parent` and/or `get_sub_issues` for full context.

There is no separate attachments/diffs fetch on GitHub — the issue body and comments already carry everything, including inline images (rendered as markdown image links in the body/comment text).

For each fetch: on error, retry up to **3 times**.
If any fetch still fails after retries, note it explicitly:
> "Warning: Could not fetch [comments/labels/hierarchy] for issue #<N>. Proceeding with partial context — implementation accuracy may be reduced."

If **critical** context (the issue body itself) is unavailable, abort and report:
> "Insufficient context to proceed. Cannot fetch issue body for #<N> after 3 retries."

### A3. Mark In Progress

Ensure the `in-progress` label exists:

```
mcp__github__get_label(owner: "<owner>", repo: "<repo>", name: "in-progress")
```

If it doesn't exist (404), create it:

```bash
gh label create in-progress --color 1D76DB --description "Claude is actively working this" --repo <owner>/<repo>
```

Then apply the label and self-assign:

```
mcp__github__issue_write(
  method: "update",
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  labels: [<existing labels...>, "in-progress"],
  assignees: ["<ME>"]
)
```

If label creation fails (no `gh` auth, no write access): warn and continue with just the assignee as the in-progress signal — don't block on it.

### A4. Enrich Description (if missing)

Read the issue body. If **acceptance criteria** or **verification steps** are absent:

1. Derive them from: issue title, comments, and the current codebase state.
2. Update the body to add the missing sections:

```
mcp__github__issue_write(
  method: "update",
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  body: "<existing body>\n\n## Acceptance Criteria\n- [ ] ...\n\n## Verification Steps\n1. ..."
)
```

### A5. Post Analysis Comment

Post a structured analysis comment before starting implementation:

```
mcp__github__add_issue_comment(
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  body: "## Analysis\n\n**Root cause / scope:** ...\n\n**Implementation plan:**\n1. ...\n2. ...\n\n**Files affected:** ...\n\n**Risks / unknowns:** ...\n\n*Posted by Claude Code*"
)
```

---

## Phase B: Discover or Create Issues (no issue mentioned)

### B1. Scan Open Issues

```
mcp__github__search_issues(query: "repo:<owner>/<repo> is:issue is:open <keywords>")
```

or, for a broader unfiltered scan:

```
mcp__github__list_issues(owner: "<owner>", repo: "<repo>", state: "OPEN")
```

For each open issue, assess relevance to the current request:
- Compare title + body keywords against the user's request
- Mark issues as **relevant** if they describe the same feature, bug area, or user flow

### B2a. If Relevant Issues Found

For each relevant issue: follow **Phase A** (pull, enrich, mark in-progress, comment, implement).

### B2b. If No Relevant Issues Exist

Create a set of well-scoped issues. For each logical unit of work:

Ensure the `claude` label exists (same get/create pattern as A3, e.g. `gh label create claude --color 6E56CF --description "Created by Claude Code" --repo <owner>/<repo>`), then:

```
mcp__github__issue_write(
  method: "create",
  owner: "<owner>", repo: "<repo>",
  title: "<concise title>",
  body: "<comprehensive description — see template below>",
  labels: ["claude"]
)
```

#### Issue Body Template (for Claude-created issues)

```markdown
## Summary
<1-2 sentence description of what this issue addresses>

## Context
<Why this work is needed. Link to related issues or PRs if available.>

## Technical Details
<Implementation notes, affected files/modules, constraints>

## Acceptance Criteria
- [ ] <specific, testable criterion>
- [ ] <specific, testable criterion>
- [ ] ...

## Verification Steps
1. <How to manually verify the criteria above>
2. ...

## Out of Scope
<What this issue explicitly does NOT cover>

---
*Created by Claude Code*
```

After creating issues, follow **Phase A** for each one.

---

## Implementation Phase

With context loaded and issues marked in-progress:

1. Implement the changes needed to satisfy all acceptance criteria.
2. After each logical unit of work, check off the corresponding acceptance criterion mentally.
3. Run tests, build, and linting as appropriate for the project — for sprout that's `make lint`, `make tc`, and `make test` (or `make check` to run all three).
4. Verify **every** acceptance criterion is met before moving on.

---

## Completion Phase

### When All Acceptance Criteria Are Met

1. **Check off all acceptance criteria in the issue body:**

Fetch the current issue body, replace every `- [ ]` under the Acceptance Criteria section with `- [x]`, then save:

```
mcp__github__issue_write(
  method: "update",
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  body: "<body with all acceptance criteria checkboxes checked>"
)
```

2. **Swap the label from `in-progress` to `in-review`:**

Ensure `in-review` exists (same get/create pattern as A3), then:

```
mcp__github__issue_write(
  method: "update",
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  labels: [<labels minus "in-progress">, "in-review"]
)
```

The issue `state` stays **open** throughout — this skill never closes issues. Closing is a human/PR-merge decision.

3. **Post completion comment:**

```
mcp__github__add_issue_comment(
  owner: "<owner>", repo: "<repo>", issue_number: <N>,
  body: "## Implementation Complete\n\n**Changes made:**\n- ...\n\n**Acceptance criteria status:**\n- [x] ...\n- [x] ...\n\n**Testing notes:** ...\n\n*Marked in-review by Claude Code*"
)
```

---

## Checklist

Before completing this skill's run:

- [ ] Issue fetched successfully (or failure reported)
- [ ] Comments/labels/hierarchy pulled (or partial-context warning posted)
- [ ] Issue labeled **in-progress** and self-assigned
- [ ] Acceptance criteria exist in body (added if missing)
- [ ] Analysis comment posted
- [ ] Implementation done
- [ ] All acceptance criteria verified
- [ ] Acceptance criteria checkboxes checked off on the issue
- [ ] Issue labeled **in-review**
- [ ] Completion comment posted

---

## Error Handling Reference

| Situation | Action |
|-----------|--------|
| Issue fetch fails 3× | STOP — report insufficient context |
| Comment/label fetch fails 3× | Warn, continue with partial context |
| `in-progress`/`in-review`/`claude` label missing | Create it via `gh label create` (Bash), don't block on it |
| `gh label create` fails (no `gh` auth, no repo write access) | Warn, fall back to assignee-only signal (skip label step) |
| No remote, or remote isn't `github.com` | STOP — ask the user to confirm the correct GitHub remote |
| No matching issue found in Phase B | Create a new issue (B2b), don't ask permission first |
| Body update fails | Post intended body as a comment instead |
| Neither `mcp__github__*` tools nor an authenticated `gh` CLI are available | STOP — report to the user that GitHub write access isn't wired up in this session, and ask them to either authenticate `gh`, enable the GitHub MCP server, or paste the drafted issue body manually |

---

## Quick Reference — Tools Used

| Tool | Purpose |
|------|---------|
| `mcp__github__get_me` | Resolve acting username |
| `mcp__github__issue_read` | Fetch issue, comments, labels, hierarchy |
| `mcp__github__issue_write` | Create/update issues, labels, assignees, body |
| `mcp__github__add_issue_comment` | Post analysis/completion comments |
| `mcp__github__search_issues` / `list_issues` | Discover relevant open issues |
| `mcp__github__get_label` | Check whether a label exists |
| `gh label create` (Bash) | Create a label — no MCP equivalent exists |
| `gh issue create` (Bash) | Fallback for issue creation when the `mcp__github__*` tools aren't available but `gh` is authenticated |
