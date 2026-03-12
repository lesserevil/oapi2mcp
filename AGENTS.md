# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Beads Storage Mode: Git-native, No Dolt

**This repo uses `no-db: true` mode.** Beads is configured to use `.beads/issues.jsonl` as the
sole source of truth — no Dolt database, no central server.

**Rules (permanent — do not change these):**
- `no-db: true` MUST remain set in `.beads/config.yaml`. Never set it to `false`.
- Never commit or restore the `dolt/` directory. It is gitignored and must stay that way.
- `issues.jsonl` and `interactions.jsonl` ARE tracked by git. Commit them alongside code changes.
- On branches: create/update issues freely. The append-only JSONL format means merging branches
  almost never produces conflicts. If a conflict does occur in `issues.jsonl`, resolve it by
  keeping both conflicting lines (both are valid events).
- Never run `bd daemon` or commands that require the Dolt backend.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
```

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds

