# SRPG95 Unpack and Repack Tool

## Scope

`srpg95tool` currently covers:

- structured unpacking
- structured write-back
- `SMAP` event-level rebuilds
- runtime patch application
- risk and audit report generation

The formal production entry point now prefers the `project` subcommands. The lower-level commands remain available for debugging and research.

## Main CLI

```powershell
python -m srpg95tool unpack <game_dir> <out_dir>
python -m srpg95tool inspect <out_dir>
python -m srpg95tool verify-roundtrip <game_dir> <out_dir>
python -m srpg95tool simulate-pack <unpack_dir>
python -m srpg95tool pack <game_dir> <unpack_dir> <out_dir>
python -m srpg95tool inspect-runtime <game_dir>
python -m srpg95tool patch-runtime <game_dir> <out_dir> --profile stable-menu16
python -m srpg95tool patch-runtime <game_dir> <out_dir> --profile strong-dialogue
python -m srpg95tool project init <game_dir> <workspace_dir>
python -m srpg95tool project import-txt <workspace_dir>
python -m srpg95tool project doctor <workspace_dir>
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

## Formal Mainline vs. Low-Level Commands

Formal mainline:

- `project init`
- `project doctor`
- `project build`

Lower-level commands remain useful for:

- inspecting unpack output directly
- validating `simulate-pack` in isolation
- studying runtime patches in isolation
- debugging intermediate artifacts from a specific stage

## Output Layout

The low-level unpack layout is still:

```text
<out_dir>/
  manifest.json
  databases/
  maps/
  texts/
  raw/
  reports/
```

The project workspace layout is:

```text
<workspace>/
  machine/
  txt_src/
  txt_zh/
  txt_map/
  reports/
```

## Translation Input Layers

The formal human input layer is now:

- `txt_zh/`

The machine-compatible layers still exist, but are no longer the primary human editing surface:

- `machine/texts/text_index.jsonl`
- `machine/texts/catalog/*.jsonl`
- `machine/texts/dialogue_catalog/*.jsonl`

## Long Dialogue Strategy

Long dialogue is now officially solved in the pack layer rather than through `strong-dialogue`:

- detect `opcode 1 + contiguous opcode 201`
- auto-wrap body text
- auto-paginate when needed
- add new `opcode 1 + 201...` pages when needed
- recalculate event length, declaration length, and chunk chains

## DAT Boundary

Fixed DAT files still use a hard-limit model:

- overflow is a hard error
- no silent truncation
- no automatic expansion

The formal DAT growth-preparation outputs are:

- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`
- `docs/en/dat-growth-plan.md`

## Runtime Profiles

### stable-menu16

The only formally recommended profile. It includes:

- the `opcode 45` menu GDI bridge
- hover highlight fixes
- the 12px menu UI font
- `BGM / EFS / BMP` resource alias compatibility
- the current DAT-backed UI Chinese display chain

### strong-dialogue

Still available, but treated as an experimental profile rather than a formal mainline path.

At this stage it remains the experimental profile for the opcode 1/201 dialogue safety chain;
formal docs and day-to-day builds still recommend `stable-menu16`.
