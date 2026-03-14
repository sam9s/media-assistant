# Development Rules

These rules apply to coding agents working in this repository. They are not for Raven's runtime personality or OpenClaw behavior.

## Validation Before Backup

For pipeline logic changes:

1. edit locally
2. deploy to VPS
3. run internal sanity checks
4. run at least one real end-to-end validation
5. only then commit, push, and sync the VPS repo

Do not treat a new pipeline feature as final or backed up until it has passed real workflow validation.

## Protect Working Pipelines

- Prefer targeted fixes over broad rewrites.
- Do not change unrelated working pipelines while fixing one subsystem.
- If a change adds behavior-heavy logic, validate the old behavior still works before considering the change complete.

## Risk Handling

- Ask first before destructive cleanup, service restarts, or anything that could disrupt the VPS.
- If a result is ambiguous, do not guess and commit the guess as source of truth.

## Repo Discipline

- Keep local, GitHub, and VPS aligned only after validation is complete.
- Do not use GitHub as the new source of truth for an unvalidated pipeline change.
