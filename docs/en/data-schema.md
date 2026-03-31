# SRPG95 Data Schema

## Goal

The current schema supports:

- structured unpacking
- structured translation
- `SMAP` event rebuild and repack
- long-dialogue auto-wrap and auto-pagination
- project-level import, audit, and risk reporting

## Fixed DAT

Fixed DAT exports still preserve:

- `record_id`
- `offset`
- `size`
- `fields`
- `raw_record_hex`
- `raw_record_sha256`

Text fields still preserve:

- `text_id`
- `text`
- `source_bytes_hex`
- `actual_text_bytes_hex`
- `max_bytes`
- `null_terminated`
- `source_offset_in_file`

These fields still obey fixed-slot limits.

## MAPC

`MAPC_*.json` still uses linear `tiles` as its primary payload.

## SMAP Top Level

Each map still exports to:

- `maps/SMAP_<id>/smap.json`
- `maps/SMAP_<id>/events/*.json`

`smap.json` still preserves:

- `ev_chunks`
- `event_declarations`
- static sections
- raw hashes

## SMAP Event Objects

Event objects still preserve:

- `declared_length`
- `padded_length`
- `command_bytes_length`
- `chunk_chain`
- `raw_event_bytes_hex`
- `raw_event_sha256`
- `commands`

These fields remain the authoritative repack-length contract.

## Machine Layer and TXT Workspace

The formal project workspace contains:

- `machine/`
- `txt_src/`
- `txt_zh/`
- `txt_map/`
- `reports/`

In that workspace:

- `txt_src` = read-only source baseline
- `txt_zh` = formal human input
- `txt_map` = machine-owned mapping layer

## Authoritative Block Semantics

The formal workflow now uses these concepts explicitly:

- `translation_present`
- `same_as_source`
- `authoritative_blocks`
- `same_as_source_authoritative_blocks`

Meaning:

- empty `txt_zh` block = untranslated
- non-empty `txt_zh` block = authoritative and written back
- a source-identical block may still be a valid authoritative block

## Main Index and Catalog Layers

These compatibility layers still exist:

- `texts/text_index.jsonl`
- `texts/catalog/*.jsonl`
- `texts/dialogue_catalog/*.jsonl`

But the formal human entry point has moved to `txt_zh/`.

## Dialogue Layout Contract

The pack stage runs a dedicated dialogue-layout pass:

- `speaker` must stay on one line
- `speaker` above `79` bytes is a hard error
- `body` supports `\n`
- `body` supports `\f`
- body text auto-wraps by `cp936` byte length by default
- each page allows up to `4` body lines
- overflow creates a new page automatically
- new pages repeat the speaker by default

## Standalone Dialogue Continuations

`dialogue_block` entries in `txt_map` are not the only form of dialogue display text.

Some continuation lines that still render inside the previous dialogue box are exported as:

- `entry_kind = fixed_text`
- `opcode_id = 201`
- `display_role = dialogue`

These standalone `201` continuation lines are now also written back during the pack stage. This fix stays entirely in the pack pipeline and does not depend on any runtime-profile change.

## Report Set

The formal workflow now emits at least:

- `txt_consistency.json`
- `txt_import_result.json`
- `dialogue_layout_plan.json`
- `standalone_dialogue_audit.json`
- `pack_risks.json`
- `pack_result.json`
- `project_build_result.json`
- `ui_dat_crosswalk.json`
- `dat_ui_priority.json`
- `ui_label_audit.json`
- `ui_patch_coverage.json`
- `untranslated_gdi_ui_blocks.json`

## Runtime Positioning

In the formal workflow:

- `stable-menu16` is the only recommended runtime profile
- `stable-menu16` remains the formal stable UI baseline
- `strong-dialogue` remains only as an experimental dialogue profile
- long dialogue is formally solved by the pack-layer schema and layout rules
