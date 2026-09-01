# MergeHub Codex Plugins

MergeHub Codex plugin marketplace for team use.

Repository:

```text
https://github.com/2209319077/mergehub-codex-plugins
```

## Included Plugins

- `mergehub-requirement-workflow`: Pull requirement tasks and docs from MergeHub, upload implementation plans, record development runs, submit self-tests, mark tasks done, and keep structured work records for MergeHub daily reports.

## Access Guide

### 1. Add The Marketplace

Run this once in a local terminal:

```bash
codex plugin marketplace add 2209319077/mergehub-codex-plugins
```

If Codex already has this marketplace and you need the latest plugin metadata:

```bash
codex plugin marketplace upgrade 2209319077/mergehub-codex-plugins
```

### 2. Install The Plugin

Open the Codex plugin marketplace and install:

```text
MergeHub Requirement Workflow
```

The underlying plugin name is:

```text
mergehub-requirement-workflow
```

### 3. Configure MergeHub Access

The plugin needs the current MergeHub API address and your own login token.

Recommended local development values:

```bash
export MERGEHUB_API_BASE_URL="http://127.0.0.1:8080"
export MERGEHUB_TOKEN_NAME="satoken"
export MERGEHUB_TOKEN_VALUE="<your-current-mergehub-token>"
```

For shared, staging, or production environments, replace `MERGEHUB_API_BASE_URL` with the target MergeHub backend URL.

Do not commit tokens, passwords, cookies, or production secrets to any repository.

### 4. Verify The Plugin

After installation, ask Codex:

```text
Pull my MergeHub requirement tasks.
```

If the token and API URL are correct, Codex should be able to list tasks assigned to the current MergeHub user.

### 5. Common Issues

If the plugin is not visible in Codex:

- Make sure the marketplace was added with `codex plugin marketplace add 2209319077/mergehub-codex-plugins`.
- Run `codex plugin marketplace upgrade 2209319077/mergehub-codex-plugins` after repository updates.
- Restart Codex if the marketplace list does not refresh.

If task listing fails:

- Confirm MergeHub backend is reachable from the local machine.
- Confirm `MERGEHUB_API_BASE_URL` has no trailing path such as `/api`.
- Confirm `MERGEHUB_TOKEN_NAME` is `satoken` unless the backend uses a different Sa-Token header.
- Refresh `MERGEHUB_TOKEN_VALUE` from a current MergeHub login session.

## Repository Layout

```text
.agents/plugins/marketplace.json
plugins/mergehub-requirement-workflow/
```

Codex reads `.agents/plugins/marketplace.json`, then resolves the plugin from `./plugins/mergehub-requirement-workflow`.
