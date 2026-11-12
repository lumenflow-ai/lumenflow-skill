# Public States

Use only these states to control the Skill workflow. Treat unknown states as `invalid_response`; do not infer internal state.

## Project states

| State | Meaning | Agent action |
|---|---|---|
| `created` | Project exists; parsing has not started | Start parsing only when requested by the workflow |
| `parsing` | Script parsing is running | Poll using `Retry-After` |
| `ready` | Parsing finished; scenes can be generated | Stop unless generation was explicitly requested |
| `generating` | One or more scenes are running | Poll using `Retry-After` |
| `partially_completed` | Generation stopped with both completed and failed scenes | Report counts and ask before a large retry |
| `completed` | All requested scenes completed | Offer or perform requested download |
| `parse_failed` | Parsing failed | Report the public error and stop |
| `content_rejected` | Input was rejected | Report the public error and stop |
| `failed` | The requested generation workflow failed | Report the public error and stop |

## Scene states

| State | Meaning |
|---|---|
| `pending` | Not submitted |
| `ready` | Eligible for generation |
| `queued` | Accepted and waiting |
| `generating` | Currently generating |
| `completed` | Result is available |
| `failed` | Generation failed with a public error |

## Rules

- Do not map or expose internal queue, provider, billing, or database states.
- Do not treat an unknown state as success.
- Do not restart parsing or generation solely because polling timed out.
- Preserve the project ID so the user can resume with `progress`.

## Seedance 2.5 job states

| State | Meaning | Agent action |
|---|---|---|
| `pending` | Job exists but is not yet queued | Poll |
| `queued` | Job is accepted and waiting | Poll |
| `generating` | References are preparing or video is generating | Poll |
| `completed` | Result is ready | Download when requested |
| `failed` | Job stopped with a public failure | Report and stop |

Preserve only the public `swj_*` job ID. Treat any other workbench state as `invalid_response`.
