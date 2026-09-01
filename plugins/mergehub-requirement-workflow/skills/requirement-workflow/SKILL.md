---
name: requirement-workflow
description: Use when operating MergeHub requirement tasks from Codex, uploading plans, syncing development progress, recording commits or artifacts, or recording Codex work items for MergeHub daily reports.
---

# MergeHub Requirement Workflow

Use this skill to operate MergeHub requirement delivery from Codex and keep structured work records for MergeHub daily reports.

## When To Record Work

Record a work item when a task produces durable value:

- implemented or changed code
- fixed or diagnosed a bug
- designed a feature or API
- ran meaningful verification
- changed configuration, schema, deployment, or release behavior
- committed, pushed, deployed, or prepared release notes

Do not record ordinary chat, repeated failed attempts, command noise, or temporary exploration unless it changes the final conclusion.

## Tool Workflow

Use the `mergehub_requirement_workflow` MCP tools:

1. `record_work_item`: append or update one local work item in SQLite.
2. `sync_to_mergehub`: upload pending local items to MergeHub.
3. `get_today_worklog`: inspect the local work record draft.
4. `configure_mergehub`: save API URL and token when the user provides them.
5. `health_check`: inspect local storage and sync configuration.

Requirement-task tools are also available in this plugin:

1. `list_requirement_tasks`: pull tasks assigned to the current MergeHub user.
2. `get_requirement_task`: inspect one task.
3. `get_requirement_document`: fetch the linked requirement description, document URL, members, and tasks before drafting a plan. Prefer passing `taskId`; `requirementId`, `requirementNo`, and `requirementUuid` are also supported.
4. `submit_requirement_plan`: upload Codex's implementation plan.
5. `start_development_run`: start development and receive the required `feature_..._REQ000001` or `hotfix_..._REQ000001` branch.
   This tool first fetches the task and verifies that `latestPlan.planStatus` is `approved`.
   If the plan is missing, submitted, rejected, or superseded, stop and ask the user:
   `MergeHub 方案未通过，是否确认无视未通过的实现方案直接开发？`
   Only after the user explicitly confirms may you call it again with `ignoreUnapprovedPlan: true`.
   In that override path, the tool returns a direct-development context instead of creating a MergeHub development run.
6. `sync_development_progress`: update development-run progress. `runStatus=completed` does not complete the task.
7. `record_development_commit`: upload a commit; pass `requirementNo` to locally verify the commit message carries the requirement number.
8. `record_development_artifact`: upload SQL, config, XXL-JOB, feature flag, or other delivery artifacts. For every changed artifact, pass the complete deliverable text in `artifactContent`; never replace it with a summary, excerpt, diff, ellipsis, or external URL.
9. `submit_development_self_test`: submit the developer self-test result for the task.
10. `mark_development_done`: after a passed self-test, mark the development task done and let MergeHub notify testers when all development tasks are done.

Before drafting or resubmitting an implementation plan, call `get_requirement_document` and use the latest MergeHub requirement description and document URL as input. If the returned `documentUrl` points to an external DingTalk document whose body is not synced into MergeHub, state that only the in-platform description and external URL were available.

To finish a development task, first sync final progress, record required commits and artifacts, submit a self-test with `result=passed`, then call `mark_development_done`. Do not treat a completed development run as task completion.

## Artifact Content

Before calling `record_development_artifact`, read the complete source artifact and upload its full deliverable content in `artifactContent`, preserving executable statements, configuration keys, ordering, and formatting needed to apply or review it.

- Upload the complete SQL script, configuration document, job definition, permission/menu definition, feature-flag configuration, migration note, or other text artifact.
- Write human-readable explanatory artifacts—such as integration guides, API documentation, deployment instructions, test reports, and migration notes—as complete GitHub-Flavored Markdown. Use `artifactType=MARKDOWN` or a descriptive `*_DOC`/`*_GUIDE` type and prefer an `.md` artifact name so MergeHub can render it correctly.
- Keep machine-applied artifacts such as SQL, JSON, YAML, XML, properties, and CSV in their complete native format. Do not wrap or rewrite those files as prose Markdown; MergeHub renders supported native formats separately.
- Do not upload only a summary, selected lines, a diff, a shortened excerpt, or content containing omission markers such as `...`.
- Do not use `artifactName`, `externalUrl`, progress summaries, worklog summaries, or self-test `artifactSummary` as a substitute for `artifactContent`.
- If one delivery contains multiple independently applicable files, record each file as its own artifact with its complete content. If they must be applied together, include every file in full with unambiguous file headings.
- For `hasChange=false`, omit `artifactContent` only when there is genuinely no changed artifact to deliver.
- Keep secrets and sensitive values out of MergeHub. Redact only the sensitive value, mark the exact redaction location, and otherwise retain the complete deliverable structure.

For requirement-task tools, do not rely on saved token configuration. Ask the user for the current MergeHub base URL and token value when needed. Keep `tokenName` defaulted to `satoken` unless the user says otherwise.

If MergeHub sync fails, keep the local record and mention the sync failure briefly.

## Work Item Shape

Prefer one work item per coherent objective. Use this structure:

- `projectName`: product or repository, for example `MergeHub`
- `role`: `development`, `testing`, `product`, or `ops`
- `summary`: concise outcome, not a vague activity
- `details`: specific changes or analysis points
- `deliverables`: files, APIs, pages, docs, SQL, commits, or deployment artifacts
- `verification`: commands run, checks passed, or manual validation status
- `risks`: unverified areas, production caveats, assumptions, or follow-up questions
- `spReadyContent`: Markdown suitable as SP evaluation input
- `metadata`: repo path, branch, command names, commit hash, or source context when useful

## SP-Ready Markdown

`spReadyContent` should be understandable without the original conversation:

```markdown
### 工作项：<summary>

- 项目：<projectName>
- 角色：<角色中文名>
- 背景：<why this work mattered>
- 工作内容：
  - <concrete action>
- 交付物：
  - <artifact>
- 验证方式：
  - <command or manual validation>
- 风险与待确认：
  - <risk or assumption>
```

## Privacy

Never record raw secrets, tokens, production passwords, private customer data, or full sensitive logs. Summarize them as redacted facts.

See `requirement-task-guide.md` for the requirement-task workflow and examples.
