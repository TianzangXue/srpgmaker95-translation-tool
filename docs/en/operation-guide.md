# SRPG95 Operation Guide

## Recommended Entry Point

For day-to-day work, use the `project` workflow only:

1. `project init`
2. edit `txt_zh/`
3. `project doctor`
4. `project build`

Lower-level commands such as `unpack`, `simulate-pack`, `pack`, and `patch-runtime` are still available, but they are primarily for debugging, research, and targeted validation.

## Initialize

```powershell
python -m srpg95tool project init <game_dir> <workspace_dir>
```

Default behavior:

- exports a full `txt_src/` source baseline
- creates a blank `txt_zh/` seed
- generates `txt_map/`
- builds the internal `machine/` layer

## Translate

Day-to-day editing should happen only in:

- `txt_zh/dat/*.txt`
- `txt_zh/smap/*.txt`

Do not:

- edit `txt_src/`
- edit `txt_map/`
- reorder blocks
- change block counts

Source-identical text is still valid authoritative input as long as the `txt_zh` block is non-empty.

## Validate

```powershell
python -m srpg95tool project doctor <workspace_dir>
```

This checks:

- TXT and sidecar consistency
- import statistics
- fixed-slot overflow risk
- dialogue layout risk
- untranslated blocks on migrated UI surfaces

## Build

```powershell
python -m srpg95tool project build <game_dir> <workspace_dir> <out_dir>
```

The formal build chain performs:

1. TXT import
2. `simulate-pack`
3. `pack`
4. `patch-runtime --profile stable-menu16`

## Default Policies

- default `--zh-seed empty`
- default `--import-mode always`
- default runtime profile = `stable-menu16`

Use these only for explicit legacy compatibility:

```powershell
--zh-seed copy-source
--import-mode diff-only
```

## Runtime Strategy

`stable-menu16` is the only formally recommended profile. It includes:

- the `opcode 45` menu GDI bridge
- hover highlight fixes
- the 12px menu UI font
- `BGM / EFS / BMP` resource alias compatibility
- the currently confirmed DAT-backed UI Chinese display chain

`strong-dialogue` is still present, but only as an experimental dialogue profile.

## Long Dialogue

Long dialogue is now officially handled by the pack layer:

- single-line speaker
- body auto-wrap by `cp936` byte count
- up to 4 body lines per page
- automatic pagination after that
- automatic speaker repetition on new pages

## DAT Limits

Fixed-slot DAT files still follow a hard-limit model:

- overflow is an error
- no silent truncation
- no automatic growth

If a build fails, inspect:

- `reports/pack_risks.json`
- `reports/project_build_result.json`
- `reports/dat_ui_priority.json`

## Minimal Daily Flow

```powershell
python -m srpg95tool project init "01闇鍋企画前編" "project_01"
python -m srpg95tool project doctor "project_01"
python -m srpg95tool project build "01闇鍋企画前編" "project_01" "build_01"
```
