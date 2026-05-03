# Agent Operating Instructions

This repository's operational rules, sprint workflow, authoritative document
paths, and current sprint state are documented in **CLAUDE.md** at this same
directory level.

All agents (Claude Code, Codex, Cursor, Aider, or others) should read
CLAUDE.md before any code modification, test run, or repository action.
CLAUDE.md is the single source of truth for:

- Pinned Python interpreter path (the project conda env is not on PATH by
  default; default `python` resolves to a different version with no project
  deps)
- Bash invocation discipline (single-command preferred; no chained or piped
  compounds during sprint workflow)
- Git workflow conventions (branch preservation, squash-merge, no GPG prefix
  workarounds)
- Sprint phase tracking, authoritative spec/ledger paths, deferred items
- Audit-vs-implement role split between agents

If CLAUDE.md is unclear or appears stale relative to repo state, raise the
discrepancy rather than acting on inference.

This AGENTS.md exists as a discovery pointer; it intentionally does not
duplicate CLAUDE.md content to avoid drift.
