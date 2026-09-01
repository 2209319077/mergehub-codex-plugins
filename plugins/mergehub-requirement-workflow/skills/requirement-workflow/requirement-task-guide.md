# MergeHub Requirement Workflow Guide

## 使用前信息

每次使用需求任务工具时，向用户确认：

- `apiBaseUrl`：例如 `http://127.0.0.1:8080`
- `tokenValue`：用户当前 MergeHub 登录 token
- `tokenName`：默认 `satoken`

不要把 token 写入代码、提交信息、工作日志或文档。

## 推荐流程

1. 使用 `list_requirement_tasks` 获取分配给当前用户的需求任务。
2. 让用户选择要实现的任务和目标项目/仓库。
3. 调用 `get_requirement_document` 读取需求正文、需求文档链接、关联成员和任务；优先传 `taskId`，也可以传 `requirementId`、`requirementNo` 或 `requirementUuid`。
4. 基于读取到的最新需求内容生成实现方案，再调用 `submit_requirement_plan` 回传 MergeHub。
5. 等任务负责人在 MergeHub 确认方案。
6. 调用 `start_development_run`，传入 `branchType`、`functionSlug`、`projectId`、`repositoryId`、`baseBranch`。
   工具会先获取任务并检查 `latestPlan.planStatus` 是否为 `approved`。
   如果方案未通过或不存在，必须先问用户：`MergeHub 方案未通过，是否确认无视未通过的实现方案直接开发？`
   只有用户明确确认后，才能再次调用 `start_development_run` 并传入 `ignoreUnapprovedPlan: true`。
   该绕过路径不会创建 MergeHub development run，只返回本地直接开发上下文和规范分支名；后续进度、commit、产物同步需要等方案通过并补建 run 后再绑定。
7. 按返回的 `developmentBranch` 创建开发分支。
8. 开发中用 `sync_development_progress` 同步进度。
9. 每个 commit message 必须包含 `REQ000001`。调用 `record_development_commit` 时建议传 `requirementNo` 做本地校验。
10. SQL、Nacos、XXL-JOB、开关、配置等产物用 `record_development_artifact` 回传。只要 `hasChange` 不是 `false`，必须把可直接交付、审核或执行的完整原文放入 `artifactContent`，不能只上传摘要、节选、diff、省略内容或外链。
11. 开发结束时可以用 `sync_development_progress` 把 development run 更新为 `completed`，但这只代表本次开发运行结束，不代表需求任务完成。
12. 调用 `submit_development_self_test` 提交研发自测记录；只有 `result=passed` 后才允许进入下一步。
13. 调用 `mark_development_done` 将研发任务置为完成。MergeHub 会在所有研发类任务完成后通知测试同学。

如果 `get_requirement_document` 返回的 `documentUrl` 是外部钉钉文档链接，而 MergeHub 没有同步文档正文，Codex 只能基于平台内的 `description` 和文档链接制定方案，并需要在回复里说明外部正文未读取。

## 分支与提交规则

- 功能开发分支：`feature_{function_slug}_REQ000001`
- Bug 修复分支：`hotfix_{function_slug}_REQ000001`
- `function_slug` 只能包含小写字母、数字和下划线。
- commit message 必须包含 `REQ000001` 或 `[REQ000001]`。

## 产物上传规则

- 上传前读取产物文件的完整内容，并保持执行语句、配置键、顺序和必要格式完整。
- 接入说明、API 文档、部署手册、测试报告、迁移说明等面向人阅读的说明类产物，统一使用完整的 GitHub-Flavored Markdown；`artifactType` 使用 `MARKDOWN` 或语义清晰的 `*_DOC`、`*_GUIDE`，产物名称优先使用 `.md` 后缀，确保 MergeHub 按 Markdown 渲染。
- SQL、JSON、YAML、XML、properties、CSV 等需要被机器直接应用的产物，继续上传完整原始格式，不要为了统一展示而改写成 Markdown 说明文档；MergeHub 会按原生类型展示。
- 一个交付包含多个可独立应用的文件时，每个文件单独登记一个产物，并分别上传完整正文。
- 多个文件必须整体应用时，可以合并登记，但要用清晰的文件标题分隔，并包含每个文件的全部内容。
- `artifactName`、`externalUrl`、进度摘要、日报摘要和自测中的 `artifactSummary` 都不能替代 `artifactContent`。
- 禁止用“核心内容如下”、部分行、diff 或 `...` 代替完整产物。
- `hasChange=false` 且确实没有变更产物时，才可以不传 `artifactContent`。
- 不上传密钥、token、生产密码或客户敏感值；只对敏感值本身做明确标记的脱敏，其余产物结构仍须完整保留。

## 示例

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenName": "satoken",
  "tokenValue": "<用户现场提供>",
  "taskId": 12
}
```

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenValue": "<用户现场提供>",
  "runId": 90,
  "artifactType": "FRONTEND_GUIDE",
  "artifactName": "REQ000006-frontend-integration-guide.md",
  "hasChange": true,
  "artifactContent": "# 前端接入与联调说明\n\n## 必须实现\n\n1. 新增统一登录入口。\n2. 使用一次性 code 换取 Token。\n\n## 验证\n\n- 登录成功后可正常进入系统。",
  "environment": "all"
}
```

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenValue": "<用户现场提供>",
  "runId": 90,
  "artifactType": "sql",
  "artifactName": "V20260820__add_release_index.sql",
  "hasChange": true,
  "artifactContent": "ALTER TABLE mh_release_order\n    ADD INDEX idx_mh_release_order_project_status_time (project_id, status, planned_at);",
  "environment": "production"
}
```

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenName": "satoken",
  "tokenValue": "<用户现场提供>",
  "taskId": 12,
  "planContent": "实现方案正文",
  "riskSummary": "涉及订单导出 SQL 和权限菜单配置"
}
```

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenValue": "<用户现场提供>",
  "taskId": 12,
  "requirementNo": "REQ000001",
  "branchType": "feature",
  "functionSlug": "order_export",
  "projectId": 3,
  "repositoryId": 8,
  "baseBranch": "master",
  "codexSessionId": "当前 Codex 会话标识"
}
```

```json
{
  "apiBaseUrl": "http://127.0.0.1:8080",
  "tokenValue": "<用户现场提供>",
  "taskId": 12,
  "developmentRunId": 90,
  "result": "passed",
  "environment": "local",
  "scopeSummary": "本次需求涉及的接口、页面和回归范围",
  "testPoints": "自测点列表",
  "commitSummary": "已登记的 commit 摘要",
  "artifactSummary": "SQL、配置、开关等产物摘要"
}
```
