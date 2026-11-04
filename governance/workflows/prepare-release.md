---
name: prepare-release
description: Prepare a release (changelog, version, validation, notes) and stop for human approval before any tag or publication.
---

# Workflow: Prepare Release

## Objective
Get a release candidate ready and verified, leaving the irreversible step to a human.

## Inputs
- Target version and the commit or branch to release.

## Preconditions
- Level 3 applies to the whole workflow. The human has asked for a release and approved preparing it.

## Files to inspect
- `CHANGELOG.md`, version-bearing files (`package.json` and any application manifests)
- The commit range since the last release, and open pull requests intended for it
- CI results for the release commit

## Steps
1. Confirm the release commit has green required CI checks; do not assume.
2. Review the commit range; make sure every `feat`, `fix`, `security` and `perf` change has a changelog entry.
3. Move "Unreleased" entries under the new version heading with the date.
4. Propose version bumps in manifests and lockfiles per semantic versioning; apply them only with approval.
5. Run `npm run validate` and any stack build and test steps; capture results.
6. Draft release notes from the changelog: highlights, breaking changes, upgrade steps, known issues.
7. Stop. Present the checklist and wait for the human to approve tagging and publishing.

## Required validation
- Full validation plus build, with results attached to the release notes.

## Documentation requirements
- Changelog finalised; setup and operations docs match the release.

## Approval requirements
- Creating a tag, pushing, publishing or announcing is Level 3 and is done by or with a human.

## Expected outputs
A release checklist, finalised changelog, draft release notes, and a clear statement that tagging and publishing are pending approval.
