# CHANGELOG

<!-- version list -->

## v0.3.1 (2026-10-17)

### Bug Fixes

- Cap retry_after_hook waits at RetryConfig.max_wait
  ([#15](https://github.com/lawrnsfeng/sprout/pull/15),
  [`3e69b13`](https://github.com/lawrnsfeng/sprout/commit/3e69b13032e3e85cc01dfaae122b7be7dd92ab50))

- Honour max_wait for retry hooks, validate configs, build docs in PRs
  ([#15](https://github.com/lawrnsfeng/sprout/pull/15),
  [`3e69b13`](https://github.com/lawrnsfeng/sprout/commit/3e69b13032e3e85cc01dfaae122b7be7dd92ab50))

- Validate executor and retry config values ([#15](https://github.com/lawrnsfeng/sprout/pull/15),
  [`3e69b13`](https://github.com/lawrnsfeng/sprout/commit/3e69b13032e3e85cc01dfaae122b7be7dd92ab50))

### Continuous Integration

- Build docs on pull requests ([#15](https://github.com/lawrnsfeng/sprout/pull/15),
  [`3e69b13`](https://github.com/lawrnsfeng/sprout/commit/3e69b13032e3e85cc01dfaae122b7be7dd92ab50))


## v0.3.0 (2026-10-14)

### Bug Fixes

- Adopt only parentless children in the TreeNode constructor
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Bind the gate to the running loop, harden cleanup, validate policies
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Cancel in-flight fetches when the traversal is cancelled
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Count attempts where the fetch happens; retry only Exception
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Handle cancellation and timeout races in the executor loop
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Keep TreeNode.parent out of the dataclass fields
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Link children passed to the TreeNode constructor
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Rebuild the concurrency gate only in run_sync
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Scope dedup state to the traversal and pacing to the gate
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Stop the tenacity deprecation warning and keep the traceback tail
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Tag sprout's own node timeout and validate node_timeout
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

### Documentation

- Correct two claims about report.errors and run_sync
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Cover outcomes, has_failures and seven things the guide left out
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Describe Executor reuse and two more limits of blocking fetchers
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Document node timeout, error hook, subtree, on_duplicate and run_sync
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Replace the PyPI install line with the git install
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- State the limits of blocking fetchers and of path under allow
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

### Features

- Add a per-node timeout and per-node timing ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Add a synchronous entry point for blocking fetchers
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Add an on_duplicate policy ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Add TreeNode.outcomes and TraversalReport.has_failures
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Add TreeNode.parent, path and subtree ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Close consumer-reported API gaps ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

- Report node errors to the progress hook ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

### Refactoring

- Add TreeNode.node_id and drop redundant mark parameters
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))

### Testing

- Cover a fetch that starts after the clock has expired
  ([#14](https://github.com/lawrnsfeng/sprout/pull/14),
  [`15111fb`](https://github.com/lawrnsfeng/sprout/commit/15111fb724124fc96ee802882f2c43c387ea6ad8))


## v0.2.0 (2026-10-13)

### Documentation

- Update README & mkdocs ([#12](https://github.com/lawrnsfeng/sprout/pull/12),
  [`a339eaa`](https://github.com/lawrnsfeng/sprout/commit/a339eaa9a3be18cb3d67d7e4bbf4ec643bb9f9f1))

### Features

- Ship py.typed so dependents get the type hints
  ([#13](https://github.com/lawrnsfeng/sprout/pull/13),
  [`f077764`](https://github.com/lawrnsfeng/sprout/commit/f077764cf49dee102874c210692943b9216c643c))


## v0.1.0 (2026-07-18)

- Initial Release
