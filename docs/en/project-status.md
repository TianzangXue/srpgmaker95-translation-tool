# SRPG95 Project Status

## Current Formal Mainline

The project now has a complete formal mainline:

- unpack, import, repack, and runtime patching are connected into one stable flow
- long Chinese dialogue is officially handled by native pack-layer wrapping and pagination
- `stable-menu16` is the only formally recommended runtime profile
- `stable-menu16` remains the only formally recommended runtime profile
- `strong-dialogue` remains only as an experimental dialogue profile

## Completed Capabilities

- structured unpacking
- fixed DAT / `MAPC` / `SMAP` repacking
- `SMAP` event-length and trailer preservation
- top-level `project` workflow
- `txt_src / txt_zh / txt_map / machine / reports` workspace layout
- blank `txt_zh` seed mainline
- `always` import mode mainline
- authoritative write-back for source-identical UI text
- standalone `opcode 201` dialogue continuation write-back
- `opcode 45` menu GDI bridge
- hover highlight fixes
- the 12px menu UI font
- `BGM / EFS / BMP` resource alias compatibility
- the currently confirmed DAT-backed UI Chinese display chain

## Current Default Rules

- default `--zh-seed empty`
- default `--import-mode always`
- non-empty `txt_zh` blocks are authoritative
- UI/fixed text and dialogue blocks use different write-back rules
- fixed-slot DAT overflow is still a hard error

## Formally Recommended Commands

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## Current Recommended Runtime Profile

`stable-menu16` currently includes:

- the `opcode 45` menu GDI bridge
- hover highlight fixes
- the 12px menu UI font
- `BGM / EFS / BMP` resource alias compatibility
- the currently formal DAT-backed UI surface set

## Confirmed UI Surface Set

At minimum, the formal migrated set includes:

- `unit_sheet_panel`
- `compact_unit_card`
- `sortie_unit_roster`
- `item_magic_help_panel`
- `battle_unit_hover_name`
- `camp_shop_item_list`
- `battle_skill_popup`

## Areas Still Open For Research

These remain valid future research directions:

- DAT growth
- broader UI surface coverage
- deeper `HARMONY.DLL` analysis
- deeper dialogue/runtime research beyond the current stable-menu16 baseline

## Report Set

The formal workflow currently produces:

- `txt_consistency.json`
- `txt_import_result.json`
- `dialogue_layout_plan.json`
- `dialogue_runtime_audit.json`
- `standalone_dialogue_audit.json`
- `pack_risks.json`
- `pack_result.json`
- `project_build_result.json`
- `ui_dat_crosswalk.json`
- `dat_ui_priority.json`
- `ui_label_audit.json`
- `ui_patch_coverage.json`
- `untranslated_gdi_ui_blocks.json`
