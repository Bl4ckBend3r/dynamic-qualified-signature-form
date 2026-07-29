# Training-selection rework

## Foundation added

- `forms.training_selection_open` controls whether a form accepts new training selections.
- `submission_trainings` stores a durable snapshot per submission and training, including lock status, agreement file and audit metadata.
- Migration `20260729_0026_training_selection_rework.py` creates the schema without deleting legacy `selected_trainings` JSON.

## Compatibility and rollout

Existing `selected_trainings` remains the legacy read fallback. A backfill and the public selection screen must be deployed together with the application-level selection service; changing the declaration flow before that would leave active submissions without a safe selection path.

## Risks / follow-up

The requested change also requires a coordinated update of declaration upload, public selection UI, per-training agreement generation, public status and email contexts. Those paths currently use `selected_trainings` directly. They must be migrated transactionally to `submission_trainings` before the declaration field is removed.

No commit or push was made.
