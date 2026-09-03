/**
 * multi-agent-dev — the repo's proven feature-development methodology, codified.
 *
 * Origin: docs/RE_CHRONICLE.md §11 ("The Multi-Agent Workflow"). During the
 * Phase 3 reimplementation the team ran a four-role pipeline per unit of work:
 *
 *   1. Implement       — write code from a spec
 *   2. Clean-Room Review — check the code against the spec WITHOUT deference to
 *                          the implementation's own reasoning; catch spec drift
 *   3. Test            — author tests INDEPENDENTLY (to verify behaviour, not to pass)
 *   4. Run             — execute the suite, feed failures back to Implement
 *
 * This workflow fans that pipeline out across several independent workstreams
 * (the caller's decomposition) and runs each workstream through the four roles,
 * then integrates and runs the whole suite with a bounded fix loop.
 *
 * Design notes:
 *   - A `foundation` workstream (optional) runs FIRST and sequentially, because
 *     the fan-out workstreams build on the contract it establishes (shared API,
 *     data files, key lists). Its output lands on the working tree so the rest
 *     can read it.
 *   - Fan-out workstreams should touch DISJOINT files; they then run safely on a
 *     shared working tree with no worktree overhead. Set `isolate: true` on a
 *     workstream whose files overlap another's to run its implement step in a
 *     private git worktree instead.
 *   - Implement/Review/Test are separate agents so the reviewer and the test
 *     author never inherit the implementer's blind spots.
 *
 * Invoke with:
 *   Workflow({ name: 'multi-agent-dev', args: {
 *     feature: 'short name',
 *     testCmd: 'python -m pytest tests/ui/test_x_*.py -q',
 *     reviewRounds: 1,
 *     foundation: { key, spec, files },        // optional, runs first
 *     workstreams: [ { key, spec, files, isolate? }, ... ],
 *   }})
 */

export const meta = {
  name: 'multi-agent-dev',
  description: 'Fan feature workstreams out through implement / clean-room review / independent test / run',
  whenToUse: 'Building a feature across several independent workstreams using the repo’s Implement→Review→Test→Run methodology (docs/RE_CHRONICLE.md §11).',
  phases: [
    { title: 'Foundation', detail: 'shared contract the other workstreams build on' },
    { title: 'Implement', detail: 'one implement agent per workstream' },
    { title: 'Clean-Room Review', detail: 'each workstream checked against its spec' },
    { title: 'Test', detail: 'independent tests authored per workstream' },
    { title: 'Integrate', detail: 'run the whole suite, loop fixes to green' },
  ],
}

const cfg = args || {}
const FEATURE = cfg.feature || 'feature'
const TEST_CMD = cfg.testCmd || 'python -m pytest tests/ui -q'
const REVIEW_ROUNDS = typeof cfg.reviewRounds === 'number' ? cfg.reviewRounds : 1
const FIX_ROUNDS = typeof cfg.fixRounds === 'number' ? cfg.fixRounds : 2
const workstreams = Array.isArray(cfg.workstreams) ? cfg.workstreams : []

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    changedFiles: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['summary', 'changedFiles'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    approved: { type: 'boolean' },
    driftFromSpec: { type: 'string' },
    issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['approved', 'issues'],
}

const TEST_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    testFiles: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'testFiles'],
}

const RUN_SCHEMA = {
  type: 'object',
  properties: {
    green: { type: 'boolean' },
    failing: { type: 'array', items: { type: 'string' } },
    report: { type: 'string' },
  },
  required: ['green', 'report'],
}

function filesLine(ws) {
  return Array.isArray(ws.files) && ws.files.length
    ? `Edit ONLY these files (create them if missing): ${ws.files.join(', ')}.`
    : `Edit only files this spec names; do not touch other workstreams' files.`
}

function implementPrompt(ws, feedback) {
  return [
    `You are the IMPLEMENT agent for the "${FEATURE}" feature, workstream "${ws.key}".`,
    `Read the neighbouring source first and match the repo's existing conventions and style.`,
    filesLine(ws),
    `Do NOT write the feature's verification tests — an independent Test agent does that.`,
    feedback ? `\nA prior review or test run found problems you MUST fix:\n${feedback}\n` : ``,
    `\nSPEC:\n${ws.spec}\n`,
    `Implement it fully, then report a structured summary and the list of files you changed.`,
  ].join('\n')
}

function reviewPrompt(ws) {
  return [
    `You are the CLEAN-ROOM REVIEW agent for workstream "${ws.key}" of "${FEATURE}".`,
    `Judge the code on disk ONLY against the spec below. Do not assume the implementation is correct;`,
    `look for spec drift, wrong names, missing behaviour, broken conventions, and edge cases.`,
    `Read the changed files (${Array.isArray(ws.files) ? ws.files.join(', ') : 'as named in the spec'}) yourself.`,
    `\nSPEC:\n${ws.spec}\n`,
    `Return approved=true only if the code faithfully and completely satisfies the spec.`,
    `Otherwise list concrete, actionable issues.`,
  ].join('\n')
}

function testPrompt(ws) {
  return [
    `You are the TEST agent for workstream "${ws.key}" of "${FEATURE}".`,
    `Author tests that verify the BEHAVIOUR described in the spec — derive expectations from the spec,`,
    `not from whatever the implementation happens to do. Tests must fail if the behaviour is wrong.`,
    `Place them under tests/ui/ with a name beginning "test_i18n_" so the suite command can select them.`,
    `Follow the existing test conventions (see tests/ui/*.py and the root conftest sys.path setup).`,
    `\nSPEC:\n${ws.spec}\n`,
    `Write the test file(s), then report a summary and the test file paths.`,
  ].join('\n')
}

async function runWorkstream(ws, phaseName) {
  const P = (fallback) => phaseName || fallback

  let impl = await agent(implementPrompt(ws, null), {
    phase: P('Implement'), label: `impl:${ws.key}`, schema: IMPL_SCHEMA,
    isolation: ws.isolate ? 'worktree' : undefined,
  })

  let review = await agent(reviewPrompt(ws), {
    phase: P('Clean-Room Review'), label: `review:${ws.key}`, schema: REVIEW_SCHEMA,
  })

  let round = 0
  while (review && review.approved === false && round < REVIEW_ROUNDS) {
    round++
    const feedback = (review.issues || []).map((i) => `- ${i}`).join('\n')
    await agent(implementPrompt(ws, feedback), {
      phase: P('Implement'), label: `fix:${ws.key}#${round}`, schema: IMPL_SCHEMA,
      isolation: ws.isolate ? 'worktree' : undefined,
    })
    review = await agent(reviewPrompt(ws), {
      phase: P('Clean-Room Review'), label: `review:${ws.key}#${round}`, schema: REVIEW_SCHEMA,
    })
  }

  const test = await agent(testPrompt(ws), {
    phase: P('Test'), label: `test:${ws.key}`, schema: TEST_SCHEMA,
  })

  return {
    key: ws.key,
    approved: !!(review && review.approved),
    reviewIssues: (review && review.issues) || [],
    implSummary: impl && impl.summary,
    testFiles: (test && test.testFiles) || [],
  }
}

// ---- Foundation (sequential, lands the shared contract on the tree) ----
let foundation = null
if (cfg.foundation) {
  phase('Foundation')
  log(`Foundation: ${cfg.foundation.key}`)
  foundation = await runWorkstream(cfg.foundation, 'Foundation')
}

// ---- Fan out the independent workstreams ----
log(`Fanning out ${workstreams.length} workstream(s): ${workstreams.map((w) => w.key).join(', ')}`)
const wsResults = (await parallel(
  workstreams.map((ws) => () => runWorkstream(ws, null))
)).filter(Boolean)

// ---- Integrate: run the whole suite, loop fixes to green ----
phase('Integrate')
const allTestFiles = wsResults.flatMap((r) => r.testFiles)
let run = await agent(
  [
    `You are the RUN + INTEGRATE agent for "${FEATURE}".`,
    `All workstreams have been implemented and independent tests written (${allTestFiles.length} test file(s)).`,
    `Run the test command below and report results honestly.`,
    `If it fails, fix the smallest thing that makes a genuine failure pass WITHOUT weakening the tests,`,
    `re-run, and repeat up to ${FIX_ROUNDS} times. Never edit a test just to make it pass unless the test`,
    `itself contradicts the spec — say so explicitly if you do.`,
    `\nTEST COMMAND:\n${TEST_CMD}\n`,
    `Report green=true only if the command exits 0 with no failures.`,
  ].join('\n'),
  { label: 'integrate+run', schema: RUN_SCHEMA }
)

return {
  feature: FEATURE,
  foundation: foundation && { key: foundation.key, approved: foundation.approved },
  workstreams: wsResults.map((r) => ({ key: r.key, approved: r.approved, reviewIssues: r.reviewIssues })),
  run,
}
