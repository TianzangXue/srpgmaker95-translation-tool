# SRPG95 Project Workflow

## Overview

The formal production path is now the `project` workflow:

1. `project init`
2. edit `txt_zh/`
3. `project doctor`
4. `project build`

Official defaults:

- default `--zh-seed empty`
- default `--import-mode always`
- `stable-menu16` is the only formally recommended runtime profile
- `strong-dialogue` remains available, but only as an experimental profile

## Workspace Layout

```text
<workspace>/
  machine/
  txt_src/
  txt_zh/
  txt_map/
  reports/
```

- `machine/`: internal structured layer used by import, validation, packing, and reports
- `txt_src/`: read-only source-text baseline
- `txt_zh/`: formal human translation input
- `txt_map/`: machine-owned mapping layer, do not edit manually
- `reports/`: project-level validation and build reports

## Formal Commands

Initialize a project:

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

Explicitly request the blank seed mode:

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed empty
```

Legacy-compatible seed mode:

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir> --zh-seed copy-source
```

Validate TXT and build risks:

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

Run the full import, pack, and runtime patch flow:

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

Import TXT back into the machine layer only:

```powershell
python -m srpg95tool project import-txt <workspace_dir>
```

If you explicitly need the old behavior, switch the import mode:

```powershell
python -m srpg95tool project doctor <workspace_dir> --import-mode diff-only
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir> --import-mode diff-only
python -m srpg95tool project import-txt <workspace_dir> --import-mode diff-only
```

## TXT Rules

- One TXT file per source file
- `txt_src/` and `txt_zh/` filenames must match
- The visible block separator is a standalone `====` line
- Block order must not change
- Block count must not change
- Multi-line text is allowed directly
- Empty `txt_zh` block means untranslated and is not written back
- `\0` means explicit empty string and is written back
- Any other non-empty block is an `authoritative block`
- A non-empty block is still written back even when it is identical to `txt_src`

Dialogue block rules:

- first line = speaker
- remaining lines = body
- a standalone `\f` line forces a page break

Escaping rule:

- a literal `====` content line is exported as `\====`

## Import Modes

- `always`
  - default mode
  - every non-empty `txt_zh` block is treated as authoritative input and written back
- `diff-only`
  - legacy-compatible mode
  - a block is only written back if it differs from `txt_src`

Example:

- source: `真千代`
- translation: `真千代`

In `always` mode:

- the block is written back
- it counts toward `authoritative_blocks`
- it counts toward `same_as_source_authoritative_blocks`

In `diff-only` mode:

- the block is skipped
- it counts toward `unchanged_blocks`

## Formal Runtime Strategy

- `SMAP opcode 1 / 201`
  - long Chinese dialogue is handled by native pack-layer wrapping and pagination
  - `stable-menu16` stays on the formal stable UI baseline and does not enable the experimental opcode 1/201 side-buffer dialogue chain
- `opcode 45`
  - menu text is rendered through the `stable-menu16` GDI path
- DAT-backed UI
  - the formal migrated set now includes the unit sheet, compact unit card, sortie roster, item/magic help panel, battle hover labels, camp shop, and battle skill popup surfaces
- `DAT`
  - fixed-slot validation still applies
  - overflow remains a hard error with no silent truncation

## Main Reports

- `reports/txt_consistency.json`
- `reports/txt_import_result.json`
- `reports/dialogue_layout_plan.json`
- `reports/dialogue_runtime_audit.json`
- `reports/pack_risks.json`
- `reports/pack_result.json`
- `reports/project_build_result.json`
- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`
- `reports/ui_label_audit.json`
- `reports/ui_patch_coverage.json`
- `reports/untranslated_gdi_ui_blocks.json`

## Formal Conclusions

- Use `project` subcommands as the daily production entry point
- Keep `unpack / simulate-pack / pack / patch-runtime` for debugging and research
- Treat `txt_zh` as the formal human input surface
- Treat UI/fixed text and dialogue blocks as different write-back categories
- Treat `stable-menu16` as the single formal baseline for migrated UI text
- Treat `strong-dialogue` as an experimental dialogue-runtime branch only
