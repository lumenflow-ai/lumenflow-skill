# Public Errors

Show only the public code, message, request ID, retry guidance, and an actionable user step.

| Code | Retry automatically | User action |
|---|---:|---|
| `authentication_required` | No | Configure the API key |
| `token_invalid` | No | Create or configure a valid token |
| `token_expired` | No | Create a new token |
| `token_revoked` | No | Create a new token if access is still needed |
| `scope_missing` | No | Create a token with the required public scope |
| `account_unavailable` | No | Resolve the account issue in Lumenflow |
| `project_not_found` | No | Check the project ID and current account |
| `asset_not_found` | No | Refresh the public asset list |
| `reference_asset_not_found` | No | Re-upload the missing reference under the same user |
| `workbench_job_not_found` | No | Check the public `swj_*` ID and current token |
| `workbench_job_not_ready` | No | Continue bounded polling |
| `scene_edit_conflict` | No | Read the current scene edit version and re-check intended edits; never refresh and overwrite blindly |
| `project_state_conflict` | No | Check progress before another write |
| `invalid_input` | No | Correct the public input |
| `content_rejected` | No | Revise the user-provided content |
| `insufficient_credits` | No | Add credits or reduce the request |
| `rate_limited` | Yes | Wait for `Retry-After` |
| `quota_exceeded` | No | Wait for the quota window or reduce scope |
| `generation_failed` | No | Review failed scenes and ask before retrying |
| `generation_confirmation_required` | No | Obtain explicit authorization for one generation |
| `result_not_ready` | Yes | Poll progress later |
| `operation_outcome_unknown` | No new submission | Query known status; retain the original key and exact input for recovery |
| `upload_rejected` | No | Correct the documented media issue after querying upload status |
| `download_failed` | No | Retry the download after checking connectivity; no partial file is published |
| `download_too_large` | No | Use a smaller result; the hard limit is 500,000,000 bytes |
| `service_unavailable` | Yes | Use bounded retry, then stop |
| `unsafe_response_blocked` | No | Stop and report the request ID privately |

## Handling rules

- Retry only when both the code and HTTP status are retriable.
- Never replace an authentication or permission failure with another credential.
- Never expose the raw response body.
- Never expose provider, prompt, configuration, stack, SQL, storage, host, or signed-URL details.
- Use the same not-found semantics for nonexistent and unauthorized resources.
