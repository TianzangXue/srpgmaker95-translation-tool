# SRPG95 DAT Growth Preparation Plan

## Goal

This phase does not implement a full DAT growth tool yet. It focuses on two prerequisites:

- proving which UI text really comes from DAT files
- producing a decision-complete DAT growth specification for later work

The formal mainline remains:

- long `SMAP opcode 1 / 201` dialogue is handled by native pack-layer wrapping and pagination
- `opcode 45` and related UI are still handled by `stable-menu16`

## Current Findings

Many high-value UI strings are now confirmed to come from fixed-slot DAT files rather than EXE literals.

Confirmed DAT-backed consumers include:

- `sub_412448`
  - `UNIT.DAT` names
  - `CLASS.DAT` names
  - `WORD.DAT` short labels
- `sub_420564 -> sub_414344`
  - `WORD.DAT` menu labels
  - command-state text driven by `ITEM.DAT / UNIT.DAT`
- `sub_414B14`
  - `ITEM.DAT` names
  - `WORD.DAT` reward and prompt labels
- `sub_414D40`
  - `UNIT.DAT` names
  - `MAGIC.DAT` names
  - `WORD.DAT` summary labels
- `sub_41C960`
  - `UNIT.DAT` names
  - `WORD.DAT` status-card labels
- `sub_4177E8 / sub_417D9C / sub_40F4C4 / sub_40F650 / sub_40F930`
  - `ITEM.DAT / MAGIC.DAT` names and descriptions
  - `WORD.DAT` equipment and help labels

## New Reports

This phase formally produces:

- `reports/ui_dat_crosswalk.json`
- `reports/dat_ui_priority.json`

### ui_dat_crosswalk

This report answers:

- what the UI surface is
- which runtime function owns it
- which draw path it uses
- whether the text comes from DAT, SMAP, EXE literals, or is still unknown
- which DAT file, field pattern, and slot limit applies when it is DAT-backed

### dat_ui_priority

This report answers:

- which DAT files are worth entering the next implementation wave
- the UI value of each text field
- the slot pressure of each text field
- the estimated growth difficulty
- the currently recommended strategy

## Current Priority

First tier:

- `WORD.DAT`
- `MAPNAME.DAT`
- `ITEM.DAT`
- `MAGIC.DAT`
- `UNIT.DAT`

Second tier:

- `CLASS.DAT`
- `SWNAME.DAT`
- `VARNAME.DAT`
- `GEOLOGY.DAT`
- `ANIME.DAT`

## Recommended Strategies

### WORD.DAT

- role: high-frequency short-label hub
- recommendation: `compress_translation`
- why: it touches many surfaces, but most slots are still short enough that compact translation is safer than immediate structural growth

### MAPNAME.DAT

- role: map-title cache
- recommendation: `keep_fixed`
- why: loading is confirmed, but the final draw-site still deserves one last proof pass

### ITEM.DAT

- role: item and equipment names plus descriptions
- recommendation:
  - `name` -> `compress_translation`
  - `desc` -> `needs_pointer_rework`

### MAGIC.DAT

- role: spell names plus descriptions
- recommendation:
  - `name` -> `compress_translation`
  - `desc` -> `needs_pointer_rework`

### UNIT.DAT

- role: unit names plus battle and death text
- recommendation:
  - `name` -> `compress_translation`
  - `death_message` -> `needs_runtime_patch`

## First Growth Candidates

The two strongest first prototype targets remain:

1. `ITEM.DAT.desc`
2. `MAGIC.DAT.desc`

Why:

- both are high-value descriptive text fields
- the fixed 70-byte slot becomes a Chinese translation bottleneck quickly
- the two fields are structurally similar and should benefit from the same implementation path

## What This Phase Does Not Do

This phase does not implement:

- a full DAT growth tool
- deeper runtime dialogue work beyond the current formal dialogue-safe baseline
- deeper `HARMONY.DLL` work

## Recommended Next Step

1. prototype DAT growth around `ITEM.DAT.desc`
2. reuse the same approach for `MAGIC.DAT.desc`
3. finish the last draw-site confirmation for `MAPNAME.DAT`
4. then decide whether any subset of `WORD.DAT` truly needs structural growth
