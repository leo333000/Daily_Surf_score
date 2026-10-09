---
name: spec-pipeline
description: Builds a feature from a prompt in four stages (specification, testing strategy, implementation, and tests) using the spec-writer, test-strategist, implementer, and test-implementer agents. Use when asked to build something with the spec pipeline, or from a spec through to tested code.
argument-hint: <what to build>
---
Build the following request using the spec pipeline:

$ARGUMENTS

Subagents can't hand work to each other, so you coordinate: run each agent with the Agent tool, pass file paths between them, and check each result before moving on. Each agent starts with no memory of this conversation, so give it everything it needs in its prompt.

## 0. Prepare
- If the request above is empty, ask the user what to build and stop.
- Pick a short kebab-case name for the feature (for example `csv-to-json`). Both documents go in `docs/specs/<name>/`.
- Include the project rules from `CLAUDE.md` (such as which virtual environment to use) in every agent's prompt.

## 1. Specification: `spec-writer`
Give it the request word for word and the output path `docs/specs/<name>/spec.md`.
- If it reports any **Blocking** open questions, ask the user, add their answers to the spec's Assumptions section, then continue. Otherwise continue without stopping.

## 2. Testing strategy: `test-strategist`
Give it the spec path and the output path `docs/specs/<name>/test-strategy.md`.
- If it reports gaps in the spec that change expected behavior, fix the spec (ask the user if you can't decide), then have `test-strategist` update its document.

## 3. Build: `implementer` and `test-implementer`, in parallel
Start both agents in the same message:
- `implementer`: the spec path.
- `test-implementer`: the testing strategy path and the spec path.

Wait for both to finish.

## 4. Verify
Run the full test suite with the command from the testing strategy.
- If everything passes, go to step 5.
- If anything fails, compare each failure with the spec. The spec decides who is right:
  - The code doesn't match the spec: send the failure output and the spec path back to `implementer`.
  - The test doesn't match the spec: send the failure output, strategy path, and spec path back to `test-implementer`.
  - The spec itself is ambiguous: ask the user, update the spec, then send the fix to whichever agent needs it.

  Run the suite again after each round. Stop after 3 fix rounds and report what still fails.

## 5. Report to the user
- Paths to the spec and the testing strategy
- The files each build agent created
- Final test results (passed and failed counts)
- Assumptions that were made, and anything left unresolved
