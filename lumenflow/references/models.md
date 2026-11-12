# Select models for a canvas or workflow

Query the current resource's model list. It contains only publicly usable names, opaque `selection_id` values, supported parameters/durations, prices and delivery details. It never grants new scopes or access to another resource. A selection belongs to the current user, Token and resource. Do not hard-code or infer internal model IDs, providers or channels.

Match the user's requested model against returned names. If absent or ambiguous, stop and ask the user to choose; never silently substitute a different model, resolution or subsidy offering. Subsidized models are distinct offerings: `delivery` explains generation resolution, final delivery resolution and included enhancement. A 480p generation with included 720p enhancement is not a direct 480p delivery. If the user explicitly requires a 480p file, do not substitute this offering.

## Workflow

Create/import the project and its scenes first, then select a model for that project. This affects this submission only, preserving the project's default model.

```sh
python3 scripts/lumenflow.py models PROJECT_ID
python3 scripts/lumenflow.py generation-quote PROJECT_ID --selection-id SELECTION_ID --scene-id SCENE_ID
# Explain the exact selected model, scenes, delivery and total credits; wait for confirmation.
python3 scripts/lumenflow.py generate PROJECT_ID --selection-id SELECTION_ID --scene-id SCENE_ID --quote-fingerprint QUOTE_FINGERPRINT --confirm-generation --idempotency-key UNIQUE_KEY
python3 scripts/lumenflow.py progress PROJECT_ID
python3 scripts/lumenflow.py download PROJECT_ID --output-dir OUTPUT_DIR
```

Omit `--scene-id` for all eligible scenes, or use `--retry-failed` for failed scenes. Use the same scene selection for quote and generation. Quotes expire after 120 seconds. On `quote_changed`, obtain a new quote and renewed consent; do not automatically generate. Reuse the original idempotency key for a retry of an accepted submission, including after quote expiry. Do not repeat an unknown-outcome job with a new key.

Model listing requires `project:read`; quoting and generation additionally require `generation:write` and the existing effective batch limit. Downloads require `asset:read`. Model visibility and billing follow the project owner. The server rechecks account, Token, resource range, organization locks, compatibility, current price and existing queue/billing rules. No client-selected payment pool or raw model parameter is accepted.

## Canvas

Use `canvas-models CANVAS_ID`, then `canvas-quote CANVAS_ID --file request.json` and `canvas-generate CANVAS_ID --file confirmed-request.json --idempotency-key UNIQUE_KEY --confirm-generation`. Follow [canvas.md](canvas.md) for revisions, source versions and allowed parameters. Keep `selection_id`, parameters and revisions from the same current resource. For subsidy models, the public parameter is the final delivery resolution; preserve the offered value and explain the lower internal generation resolution shown by `delivery`.

Read structure and only the authorized authored text needed by this task. Upload, authored-content reading, editing, generation and download retain their independent scopes. Model selection does not add any permission.

## Delivery and failure

Wait for included enhancement. Canvas status exposes `enhancement.phase` and public refunded credits; pending/indeterminate enhancement remains generating, and enhancement failure is failed. Workflow progress also remains generating until the enhanced version becomes current. Failed enhancement never supplies raw output as the finished result. No automatic model/provider fallback or regeneration. Download commands only return authorized current finished generated media; never show upload tickets, signed URLs, internal configuration or raw upstream errors.
