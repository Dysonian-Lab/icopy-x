# Codified multi-agent workflows

Reusable, version-controlled orchestration for Claude Code's `Workflow` tool.
These files are the team's development methodology made runnable, so it is
applied consistently rather than reinvented per task.

## `multi-agent-dev.js`

Codifies the four-role pipeline from `docs/RE_CHRONICLE.md` §11 ("The
Multi-Agent Workflow"): **Implement → Clean-Room Review → Test → Run**, fanned
out across independent workstreams.

- **Implement** writes code from a spec.
- **Clean-Room Review** checks that code against the spec alone, to catch drift.
- **Test** authors tests independently, to verify behaviour rather than to pass.
- **Run / Integrate** executes the suite and loops bounded fixes to green.

An optional `foundation` workstream runs first and sequentially, because the
fan-out workstreams build on the shared contract it establishes. Fan-out
workstreams should touch disjoint files; one whose files overlap another's can
set `isolate: true` to run its implement step in a private git worktree.

### Usage

```
Workflow({ name: 'multi-agent-dev', args: {
  feature: 'short name',
  testCmd: 'python -m pytest tests/ui/test_i18n_*.py -q',
  reviewRounds: 1,
  foundation: { key: 'core', spec: '…', files: ['…'] },   // optional
  workstreams: [
    { key: 'ws-a', spec: '…', files: ['…'] },
    { key: 'ws-b', spec: '…', files: ['…'], isolate: true },
  ],
}})
```

Each workstream's `spec` is the contract its Implement, Review, and Test agents
share. Keep specs concrete and name the exact files a workstream may touch.
