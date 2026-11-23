# Lumenflow Public API Contract

Client contract: **v0.3**

Backend status: **Implemented behind server-side kill switches**

This document defines the public contract expected by the Skill. The client is pinned to Lumenflow's official API host and refuses arbitrary hosts, even when an environment variable or CLI option supplies one. Never point the Skill at Web or internal APIs.

## Contents

1. Transport
2. Capabilities
3. Response schemas
4. Errors
5. Idempotency and retries
6. Security invariants

## 1. Transport

- Production base URL: `https://api.lumenflow.net/open/v1`
- Authentication: `Authorization: Bearer <token>`
- Media type: `application/json`
- Client header: `User-Agent: lumenflow-agent-skill/0.3.0rc1`
- Request tracing: return `X-Request-Id` on every response
- Polling: return `Retry-After` when the service wants a specific interval

The client refuses:

- non-HTTPS base URLs;
- credentials, query strings, or fragments in the base URL;
- base paths not ending in `/open/v1`;
- all API and download redirects.
- API hosts not compiled into the official client allowlist.

## 2. Capabilities

| Capability | Method and path | Scope |
|---|---|---|
| Verify token | `GET /session` | Any public scope |
| List public styles | `GET /styles` | `style:read` |
| Create project | `POST /projects` | `project:write` |
| Quote parsing | `GET /projects/{project_id}/parse-quote` | `project:write` |
| Parse project | `POST /projects/{project_id}/parse` | `project:write` |
| Read progress | `GET /projects/{project_id}/progress` | `project:read` |
| Read originals and storyboard business text | `GET /projects/{project_id}/review-materials` | `project:content:read` |
| Generate scenes | `POST /projects/{project_id}/generations` | `generation:write` |
| List result assets | `GET /projects/{project_id}/assets` | `asset:read` |
| Seedance 2.5 workbench | See `seedance25.md` | `seedance25:*` |

Batch operations, empty creation and failure queries are defined in [batch.md](batch.md). Seedance 2.5 is not a project mode; read [seedance25.md](seedance25.md) for its isolated contract.

## 3. Response schemas

The separately authorized business-text review DTO, paging limits and analysis workflow are defined in [review.md](review.md). Generic record/progress responses still exclude scripts and prompts.

These are public DTOs, not internal database objects. Unknown fields must not be added without a contract review.

### `GET /session`

```json
{
  "authenticated": true,
  "scopes": [
    "style:read",
    "project:read",
    "project:write",
    "generation:write",
    "asset:read",
    "seedance25:read",
    "seedance25:asset:write",
    "seedance25:generation:write"
  ],
  "expires_at": "2026-10-30T00:00:00Z"
}
```

Do not return another user's identity or account profile.

### `GET /styles`

```json
{
  "styles": [
    {
      "style_id": "sty_public_01",
      "name": "Public style name",
      "description": "Public product description"
    }
  ],
  "aspect_ratios": ["16:9", "9:16", "1:1"],
  "default_style_id": "sty_public_01",
  "default_aspect_ratio": "16:9"
}
```

`style_id` is a stable public ID. Do not return provider, channel, model, routing, configuration, price-rule, or prompt identifiers.

### `POST /projects`

Required header: `Idempotency-Key`

```json
{
  "title": "Episode 1",
  "script": "User-provided UTF-8 script",
  "style_id": "sty_public_01",
  "aspect_ratio": "9:16"
}
```

```json
{
  "project_id": "prj_public_01",
  "status": "created",
  "project_url": "https://lumenflow.example/project/prj_public_01"
}
```

Do not accept a user, owner, organization, provider, channel, or internal model selector.

### `POST /projects/{project_id}/parse`

Required header: `Idempotency-Key`

Request body: `{"quote_fingerprint":"<confirmed parse quote>"}`. Obtain it from `GET /projects/{project_id}/parse-quote`, show the script, first-episode and total credits, and wait for the user to accept.

```json
{
  "project_id": "prj_public_01",
  "status": "parsing",
  "task_id": "tsk_public_01"
}
```

### `GET /projects/{project_id}/progress`

```json
{
  "project_id": "prj_public_01",
  "project_url": "https://lumenflow.example/project/prj_public_01",
  "status": "generating",
  "scenes": {
    "total": 10,
    "completed": 4,
    "running": 2,
    "pending": 3,
    "failed": 1
  },
  "scene_items": [
    {
      "scene_id": "scn_public_01",
      "index": 1,
      "status": "completed",
      "error": null
    }
  ]
}
```

Do not return script text, prompts, provider data, upstream errors, stack traces, storage paths, or internal task metadata.

### `POST /projects/{project_id}/generations`

Required header: `Idempotency-Key`

Use the current quote’s selection ID and fingerprint after the user accepts. Generate all eligible scenes:

```json
{"mode": "all", "selection_id": "<quoted selection>", "quote_fingerprint": "<confirmed quote>"}
```

Generate selected scenes:

```json
{
  "mode": "selected",
  "selection_id": "<quoted selection>",
  "quote_fingerprint": "<confirmed quote>",
  "scene_ids": ["scn_public_01", "scn_public_02"]
}
```

Retry failed scenes:

```json
{"mode": "retry_failed", "selection_id": "<quoted selection>", "quote_fingerprint": "<confirmed quote>"}
```

Response:

```json
{
  "project_id": "prj_public_01",
  "status": "generating",
  "task_id": "tsk_public_02",
  "accepted_scene_ids": ["scn_public_01"],
  "estimated_credits": 40
}
```

### `GET /projects/{project_id}/assets`

```json
{
  "project_id": "prj_public_01",
  "assets": [
    {
      "asset_id": "ast_public_01",
      "scene_id": "scn_public_01",
      "filename": "scene-001.mp4",
      "content_type": "video/mp4",
      "size_bytes": 123456,
      "download_url": "https://api.lumenflow.net/open/v1/downloads/dlg_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    }
  ]
}
```

The URL must be an HTTPS ticket on the official API host, with path `/open/v1/downloads/dlg_` followed by 43 URL-safe characters, short-lived and authorized for the current user and asset. The client sends no Bearer token when downloading it.

## 4. Errors

Return a JSON error envelope:

```json
{
  "error": {
    "code": "rate_limited",
    "message": "Try again later.",
    "retriable": true,
    "request_id": "req_public_01"
  }
}
```

Use the public codes in [errors.md](errors.md). Do not include raw upstream errors, internal identifiers, SQL, stack traces, server paths, prompts, configuration, or signed URLs.

Resources outside the effective administrator/Token project range return the same not-found response as nonexistent resources. Basic project capabilities are owner-scoped. Only explicitly granted internal scopes may cover customer projects; workbench jobs remain owner-only.

## 5. Idempotency and retries

- Require caller-retained `Idempotency-Key` for project creation, parsing, uploads and generation. CLI writes require `--idempotency-key`; generate one offline with `new-key` before a new operation. Workflows derive stable step keys from the supplied root. On uncertainty rerun only identical input with the original key/root, or query known resource status.
- Treat the same key plus the same body as the same operation.
- Return a conflict when a key is reused with a different body.
- Retry only 429, 502, 503, 504, and transport failures.
- Honor `Retry-After`; otherwise use bounded exponential backoff with jitter. Stop rather than sleeping beyond the 120-second request budget or retrying before the server delay. Network/read-timeout uncertainty for keyed writes is reported as `operation_outcome_unknown`; never submit a new key.
- Never retry 400, 401, 402, 403, 404, or 409 automatically.

## 6. Security invariants

- Derive the user from the token; never accept a target user field.
- Recheck administrator/Token scopes, project range and child-resource membership on every request, replay and download. Scene generation charges the project owner and audits the actual Token holder.
- Restrict every endpoint by scope and explicit capability allowlist.
- Build every response from an explicit public-field allowlist.
- Never serialize an internal database row or upstream response directly.
- Never expose or make queryable system prompts, internal prompts, provider routes, system configuration, feature flags, credentials, storage configuration, queues, or risk thresholds.
- Enforce rate, concurrency, quota, credit, audit, revocation, and kill-switch controls on the service.
