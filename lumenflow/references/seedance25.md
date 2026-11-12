# Seedance 2.5 Public Workbench

Use this contract only for a reference-driven single video. Standard Lumenflow projects use `references/public-api.md`.

## Public boundary

- Namespace: `/workbenches/seedance-2.5`
- Public input asset ID: `swa_*`
- Public job ID: `swj_*`
- Public result asset ID: `out_*`
- Never request or expose a carrier project ID, scene ID, task ID, prompt internals, model routing, provider data, storage URL, system prompt, or system configuration.
- The token user must own every reference and job. Not-owned and nonexistent resources use the same not-found response.

## Capabilities

| Capability | Method and path | Scope |
|---|---|---|
| Limits and pricing | `GET /workbenches/seedance-2.5/capabilities` | `seedance25:read` |
| Upload reference | `POST /workbenches/seedance-2.5/assets` | `seedance25:asset:write` |
| Create job | `POST /workbenches/seedance-2.5/jobs` | `seedance25:generation:write` |
| Read job | `GET /workbenches/seedance-2.5/jobs/{job_id}` | `seedance25:read` |
| Result grants | `GET /workbenches/seedance-2.5/jobs/{job_id}/assets` | `seedance25:read` |

Uploads require `Idempotency-Key` and multipart field `file`; the deterministic client also sends the original UTF-8 filename. Accept only explicit local JPG, PNG, WebP, MP3, or WAV files, each no larger than 30 MiB for images or 15 MiB for audio. Never download a remote URL and re-upload it.

Create jobs with `Idempotency-Key` and exactly:

```json
{
  "prompt": "User-authored prompt",
  "duration_seconds": 10,
  "aspect_ratio": "16:9",
  "asset_ids": ["swa_..."]
}
```

Duration is an integer from 4 through 30. Aspect ratio is one of `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9`, or `adaptive`. References are ordered: at most 30 images and 10 audio files, with no video. The UTF-8 prompt limit is 9800 bytes.

## Consent and recovery

- Require explicit authorization for one billable generation and pass `--confirm-generation`.
- Reuse the same idempotency root when resuming an upload or job submission whose result is unknown.
- Poll only `pending`, `queued`, `generating`, `completed`, or `failed`.
- Stop after 30 minutes and return `swj_*`; do not create a replacement job.
- On `completed`, call `seedance25-download`; never print the short-lived download URL.
