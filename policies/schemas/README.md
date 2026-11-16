# Legacy schemas (reference only)

These schemas describe the pre-v2 sealed/ledger record shapes. They are
kept for reference and are not used by the v2 harness. The live shapes are
`schemas/task.json`, `schemas/proof.json`, and `schemas/gate.json`.
Fields named `sig_status`, `record_hash`, or `capability_id` are legacy and
must not be emitted by v2 agents or the `factory` CLI.
