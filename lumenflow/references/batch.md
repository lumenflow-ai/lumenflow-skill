# Batch creation and failure queries (v0.3)

Eligible ordinary users can use granted basic operations on their own projects. Token scopes and resource restrictions still apply. Cross-customer access requires a separate explicit administrator grant for the operation. Know the target project ID; never enumerate or guess customers. The actor is the Token holder; generation charges the project owner using existing pricing/refund logic. Do not send an owner or billing identity.

Commands:

- `create-empty --title TITLE --idempotency-key KEY`: manual project, no script, no parsing. The service creates an empty first episode; list episodes before adding more.
- `episodes PROJECT_ID`, `scene-records PROJECT_ID`, `asset-records PROJECT_ID`, `failures PROJECT_ID`: GET the matching project subpath. Each returns `items` and `next_cursor`; use `--after CURSOR --limit 50` to continue (maximum 100). Report incomplete results if a cursor remains. Failures are current failed scenes, not historical tasks; use episode_id/episode_number and scene ID/scene_number together.
- `batch-episodes PROJECT_ID --file FILE --idempotency-key KEY`: POST episodes/batch, scope episode:write. Only manual/empty projects, maximum 20. No scripts or paid parsing.
- `batch-assets`: POST asset-records/batch, scope asset:write, maximum 50. Creates records only, no images or fees.
- `batch-scenes`: POST scenes/batch, scope scene:write, maximum 50.
- `batch-prompts`: PATCH scenes/prompts/batch, scope scene:write, maximum 50.

Administrator/Token limits may be smaller. JSON files must be at most 1 MiB. Every batch is all-or-nothing, but multiple batches are NOT one transaction. Keep the same key and exact payload after transport uncertainty. Do not silently split, change a key, or overwrite on conflict.

Payload examples (replace IDs with server-returned IDs):

```json
{"items":[{"title":"第二集"}]}
{"items":[{"category":"character","name":"主角","description":"角色说明","prompt":"用户提供的业务提示词","states":["日常服装"],"aspect_ratio":"9:16"}]}
{"items":[{"episode_id":"ep_...","prompt":"人物走向窗边","duration":"4s","asset_ids":[]}]}
{"items":[{"scene_id":"scn_...","expected_edit_version":"VERSION_FROM_SCENE_RECORDS","prompt":"新的业务提示词"}]}
```

Asset categories: character, scene, prop, pose_ref. Name <=80 characters, description/prompt <=4000, states <=20 strings of <=120 characters. Scene prompts <=20000 characters, duration 4s–30s. Prompts and generated media are not returned by these record queries.

Read scene-records immediately before updates; use each edit_version exactly. For corrections based on existing text, use [review.md](review.md) and compare its edit_version too; a metadata-only refresh must not authorize a stale correction. Never refresh a version and automatically retry a rejected overwrite: re-check user intent. A running scene cannot be edited. Prompt edits preserve completed media. Character links follow prompt mentions plus explicit asset_ids; omitted asset_ids preserves non-character links, explicit [] clears non-character links except mentions. All reference IDs must belong to the path project.

Reject requests for system prompts/configuration. Batch input permits business prompt text only. Audit stores actor, Token, project owner, affected resource IDs, changed fields and before/after versions, NOT prompt body history. No user identity or internal model/provider details appear in public responses.
