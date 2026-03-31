"""Project-level TXT workflow for the SRPG MAKER 95 toolchain.

This module defines the stable production workflow built around
`project init`, `project doctor`, and `project build`. It keeps the
translator-facing TXT workspace, the internal machine layer, and the
report set in sync without changing the lower-level unpack/pack/runtime
commands that still exist for debugging and research.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import SOURCE_ENCODING, TOOL_VERSION, json_dump, jsonl_dump, sort_paths, utc_timestamp
from .pack import pack_game, simulate_pack
from .runtime_patch import build_runtime_patch_plan, patch_runtime, stable_gdi_text_filters
from .unpack import unpack_game

BLOCK_SEPARATOR = "===="
ESCAPE_PREFIX = "\\"
PROJECT_RUNTIME_PROFILE = "stable-menu16"
PROJECT_STRUCTURE_VERSION = 1
DEFAULT_IMPORT_MODE = "always"
SUPPORTED_IMPORT_MODES = ("always", "diff-only")
DEFAULT_ZH_SEED_MODE = "empty"
SUPPORTED_ZH_SEED_MODES = ("empty", "copy-source")


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    machine: Path
    txt_src: Path
    txt_zh: Path
    txt_map: Path
    reports: Path


def _workspace_paths(workspace_dir: Path) -> WorkspacePaths:
    root = workspace_dir.resolve()
    return WorkspacePaths(
        root=root,
        machine=root / "machine",
        txt_src=root / "txt_src",
        txt_zh=root / "txt_zh",
        txt_map=root / "txt_map",
        reports=root / "reports",
    )


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
    else:
        path.chmod(0o666)
        path.unlink()


def _clear_readonly_and_retry(func: Any, path: str, _exc_info: Any) -> None:
    retry_path = Path(path)
    try:
        retry_path.chmod(0o666)
    except OSError:
        pass
    func(path)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest_zh_seed_mode(manifest: dict[str, Any]) -> str:
    return str(manifest.get("zh_seed_mode", DEFAULT_ZH_SEED_MODE))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    jsonl_dump(path, rows)


def _copy_report_if_exists(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_bitmap_resource_audit(machine_dir: Path, playable_dir: Path, report_dir: Path) -> dict[str, Any]:
    rows = _load_jsonl(machine_dir / "texts" / "resource_index.jsonl")
    bmp_dir = playable_dir / "BMP"
    audited_rows = [
        row
        for row in rows
        if str(row.get("source_file", "")).startswith("MAP/SMAP_")
        and (
            row.get("resource_role") == "bitmap_ref"
            or row.get("opcode_id") == 13
            or str(row.get("original_text", "")).lower().endswith(".bmp")
        )
    ]
    items: list[dict[str, Any]] = []
    missing_alias_count = 0
    missing_source_count = 0

    for row in audited_rows:
        original_name = str(row.get("original_text", ""))
        source_path = bmp_dir / original_name
        source_exists = source_path.exists()
        alias_name: str | None = None
        alias_exists = False
        alias_status = "not_applicable"
        try:
            cp932_bytes = original_name.encode(SOURCE_ENCODING)
        except UnicodeEncodeError:
            alias_status = "source_name_not_cp932_encodable"
        else:
            if all(byte < 0x80 for byte in cp932_bytes):
                alias_status = "ascii_name_no_alias_needed"
            else:
                try:
                    alias_name = cp932_bytes.decode("cp936")
                except UnicodeDecodeError:
                    alias_status = "cp936_decode_failed"
                else:
                    alias_exists = alias_name == original_name or (bmp_dir / alias_name).exists()
                    alias_status = "ok" if alias_exists else "missing_alias"

        if not source_exists:
            missing_source_count += 1
        if alias_status == "missing_alias":
            missing_alias_count += 1

        items.append(
            {
                "text_id": row.get("text_id"),
                "source_file": row.get("source_file"),
                "resource_role": row.get("resource_role"),
                "original_name": original_name,
                "source_exists": source_exists,
                "alias_name": alias_name,
                "alias_exists": alias_exists,
                "alias_status": alias_status,
            }
        )

    payload = {
        "status": "ok" if missing_source_count == 0 and missing_alias_count == 0 else "error",
        "audited_bitmap_ref_count": len(audited_rows),
        "missing_source_count": missing_source_count,
        "missing_alias_count": missing_alias_count,
        "items": items,
    }
    json_dump(report_dir / "bitmap_resource_audit.json", payload)
    return payload


def _escape_line(line: str) -> str:
    if line == BLOCK_SEPARATOR or line.startswith(ESCAPE_PREFIX):
        return ESCAPE_PREFIX + line
    return line


def _unescape_line(line: str) -> str:
    if line.startswith(ESCAPE_PREFIX):
        candidate = line[1:]
        if candidate == BLOCK_SEPARATOR or candidate.startswith(ESCAPE_PREFIX):
            return candidate
    return line


def _serialize_blocks(block_texts: list[str]) -> str:
    escaped_blocks: list[str] = []
    for block_text in block_texts:
        lines = block_text.splitlines() if block_text else []
        escaped_lines = [_escape_line(line) for line in lines]
        escaped_blocks.append("\n".join(escaped_lines))
    return f"\n{BLOCK_SEPARATOR}\n".join(escaped_blocks) + "\n"


def _parse_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    blocks: list[list[str]] = [[]]
    for line in lines:
        if line == BLOCK_SEPARATOR:
            blocks.append([])
            continue
        blocks[-1].append(_unescape_line(line))
    return ["\n".join(block) for block in blocks]


def _txt_relative_parts(sidecar: dict[str, Any]) -> tuple[str, str]:
    source_file = sidecar["source_file"]
    source_path = Path(source_file)
    if source_path.name.upper().startswith("SMAP_"):
        return "smap", f"{source_path.stem.lower()}.txt"
    return "dat", f"{source_path.stem.lower()}.txt"


def _read_catalog_rows_by_source(machine_dir: Path) -> dict[str, list[dict[str, Any]]]:
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for path in sort_paths(list((machine_dir / "texts" / "catalog").glob("*.jsonl"))):
        for row in _load_jsonl(path):
            rows_by_source.setdefault(row["source_file"], []).append(row)
    return rows_by_source


def _read_dialogue_rows_by_source(machine_dir: Path) -> dict[str, list[dict[str, Any]]]:
    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    for path in sort_paths(list((machine_dir / "texts" / "dialogue_catalog").glob("*.jsonl"))):
        for row in _load_jsonl(path):
            rows_by_source.setdefault(row["source_file"], []).append(row)
    return rows_by_source


def _read_text_index_map(machine_dir: Path) -> dict[str, dict[str, Any]]:
    return {row["text_id"]: row for row in _load_jsonl(machine_dir / "texts" / "text_index.jsonl")}


def _build_source_sidecar(
    *,
    source_file: str,
    catalog_rows: list[dict[str, Any]],
    dialogue_rows: list[dict[str, Any]],
    text_index_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    dialogue_text_ids = {row["speaker_text_id"] for row in dialogue_rows}
    for row in dialogue_rows:
        dialogue_text_ids.update(row["body_text_ids"])

    blocks: list[dict[str, Any]] = []

    for row in catalog_rows:
        if row["text_id"] in dialogue_text_ids:
            continue
        blocks.append(
            {
                "block_index": -1,
                "entry_kind": "fixed_text" if row["container_type"] != "smap_event" else "smap_text",
                "primary_id": row["text_id"],
                "text_id": row["text_id"],
                "dialogue_id": None,
                "source_file": row["source_file"],
                "container_id": row["container_id"],
                "field_path": row["field_path"],
                "speaker_text_id": None,
                "body_text_ids": [],
                "source_offset_in_file": row["source_offset_in_file"],
                "max_bytes": row["max_bytes"],
                "supports_length_growth": row["supports_length_growth"],
                "layout_policy": row.get("layout_policy") or "single_text",
                "original_text": row["original_text"],
            }
        )

    for row in dialogue_rows:
        speaker_index = text_index_map.get(row["speaker_text_id"], {})
        blocks.append(
            {
                "block_index": -1,
                "entry_kind": "dialogue_block",
                "primary_id": row["dialogue_id"],
                "text_id": None,
                "dialogue_id": row["dialogue_id"],
                "source_file": row["source_file"],
                "container_id": row["event_id"],
                "field_path": "dialogue_block",
                "speaker_text_id": row["speaker_text_id"],
                "body_text_ids": list(row["body_text_ids"]),
                "source_offset_in_file": speaker_index.get("source_offset_in_file", 0),
                "max_bytes": None,
                "supports_length_growth": True,
                "layout_policy": row.get("layout_policy", "auto_wrap_and_paginate"),
                "original_text": "\n".join([row["original_speaker"], *row["original_body_lines"]]),
            }
        )

    blocks.sort(key=lambda item: (item["source_offset_in_file"], item["primary_id"]))
    for index, block in enumerate(blocks):
        block["block_index"] = index

    source_kind = "smap" if Path(source_file).name.upper().startswith("SMAP_") else "dat"
    return {
        "source_file": source_file,
        "file_kind": source_kind,
        "block_separator": BLOCK_SEPARATOR,
        "block_count": len(blocks),
        "blocks": blocks,
    }


def _collect_sidecars(machine_dir: Path) -> dict[str, dict[str, Any]]:
    catalog_by_source = _read_catalog_rows_by_source(machine_dir)
    dialogue_by_source = _read_dialogue_rows_by_source(machine_dir)
    text_index_map = _read_text_index_map(machine_dir)
    sidecars: dict[str, dict[str, Any]] = {}
    for source_file in sorted(set(catalog_by_source) | set(dialogue_by_source)):
        sidecars[source_file] = _build_source_sidecar(
            source_file=source_file,
            catalog_rows=catalog_by_source.get(source_file, []),
            dialogue_rows=dialogue_by_source.get(source_file, []),
            text_index_map=text_index_map,
        )
    return sidecars


def _write_txt_exports(paths: WorkspacePaths, zh_seed_mode: str = DEFAULT_ZH_SEED_MODE) -> dict[str, Any]:
    if zh_seed_mode not in SUPPORTED_ZH_SEED_MODES:
        raise ValueError(f"Unsupported zh_seed_mode: {zh_seed_mode}")
    sidecars = _collect_sidecars(paths.machine)
    exported_files = 0
    exported_blocks = 0
    for sidecar in sidecars.values():
        bucket, file_name = _txt_relative_parts(sidecar)
        src_path = paths.txt_src / bucket / file_name
        zh_path = paths.txt_zh / bucket / file_name
        map_path = paths.txt_map / bucket / file_name.replace(".txt", ".map.json")
        block_texts = [block["original_text"] for block in sidecar["blocks"]]
        src_content = _serialize_blocks(block_texts)
        zh_blocks = block_texts if zh_seed_mode == "copy-source" else ["" for _ in block_texts]
        zh_content = _serialize_blocks(zh_blocks)
        src_path.parent.mkdir(parents=True, exist_ok=True)
        zh_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.write_text(src_content, encoding="utf-8", newline="\n")
        zh_path.write_text(zh_content, encoding="utf-8", newline="\n")
        json_dump(map_path, sidecar)
        exported_files += 1
        exported_blocks += len(block_texts)
    return {
        "txt_file_count": exported_files,
        "txt_block_count": exported_blocks,
        "zh_seed_mode": zh_seed_mode,
    }


def _build_project_manifest(game_dir: Path, workspace: WorkspacePaths, zh_seed_mode: str = DEFAULT_ZH_SEED_MODE) -> dict[str, Any]:
    return {
        "tool_version": TOOL_VERSION,
        "project_structure_version": PROJECT_STRUCTURE_VERSION,
        "created_at_utc": utc_timestamp(),
        "game_dir": str(game_dir.resolve()),
        "workspace_dir": str(workspace.root),
        "machine_dir": str(workspace.machine),
        "txt_src_dir": str(workspace.txt_src),
        "txt_zh_dir": str(workspace.txt_zh),
        "txt_map_dir": str(workspace.txt_map),
        "runtime_profile": PROJECT_RUNTIME_PROFILE,
        "zh_seed_mode": zh_seed_mode,
    }


def _project_manifest_path(paths: WorkspacePaths) -> Path:
    return paths.root / "project_manifest.json"


def _load_project_manifest(paths: WorkspacePaths) -> dict[str, Any]:
    manifest_path = _project_manifest_path(paths)
    if not manifest_path.exists():
        return {}
    return _load_json(manifest_path)


def init_project(game_dir: Path, workspace_dir: Path, zh_seed_mode: str = DEFAULT_ZH_SEED_MODE) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    paths = _workspace_paths(workspace_dir)
    if zh_seed_mode not in SUPPORTED_ZH_SEED_MODES:
        raise ValueError(f"Unsupported zh_seed_mode: {zh_seed_mode}")
    for path in (paths.machine, paths.txt_src, paths.txt_zh, paths.txt_map, paths.reports):
        _remove_path(path)
    paths.root.mkdir(parents=True, exist_ok=True)
    manifest = unpack_game(game_dir, paths.machine)
    export_summary = _write_txt_exports(paths, zh_seed_mode)
    project_manifest = _build_project_manifest(game_dir, paths, zh_seed_mode)
    json_dump(_project_manifest_path(paths), project_manifest)
    json_dump(
        paths.reports / "project_init_result.json",
        {
            "status": "ok",
            "machine_manifest_path": str((paths.machine / "manifest.json").resolve()),
            "project_manifest_path": str(_project_manifest_path(paths).resolve()),
            **export_summary,
        },
    )
    return {
        "status": "ok",
        "workspace_dir": str(paths.root),
        "machine_manifest": manifest,
        **export_summary,
    }


def _read_sidecars(paths: WorkspacePaths) -> list[tuple[Path, dict[str, Any]]]:
    sidecars: list[tuple[Path, dict[str, Any]]] = []
    for path in sort_paths(list(paths.txt_map.rglob("*.map.json"))):
        sidecars.append((path, _load_json(path)))
    return sidecars


def _consistency_error(kind: str, path: Path, detail: str) -> dict[str, Any]:
    return {"type": kind, "path": str(path), "detail": detail}


def _gather_txt_consistency(paths: WorkspacePaths) -> tuple[list[dict[str, Any]], list[tuple[Path, Path, Path, dict[str, Any]]]]:
    errors: list[dict[str, Any]] = []
    valid_sets: list[tuple[Path, Path, Path, dict[str, Any]]] = []
    for map_path, sidecar in _read_sidecars(paths):
        bucket, file_name = _txt_relative_parts(sidecar)
        src_path = paths.txt_src / bucket / file_name
        zh_path = paths.txt_zh / bucket / file_name
        if not src_path.exists():
            errors.append(_consistency_error("missing_txt_src", src_path, "Source TXT file is missing"))
            continue
        if not zh_path.exists():
            errors.append(_consistency_error("missing_txt_zh", zh_path, "Translated TXT file is missing"))
            continue
        src_blocks = _parse_blocks(src_path)
        zh_blocks = _parse_blocks(zh_path)
        expected = sidecar["block_count"]
        if len(src_blocks) != expected:
            errors.append(
                _consistency_error(
                    "src_block_count_mismatch",
                    src_path,
                    f"Expected {expected} blocks from sidecar, found {len(src_blocks)}",
                )
            )
            continue
        if len(zh_blocks) != expected:
            errors.append(
                _consistency_error(
                    "zh_block_count_mismatch",
                    zh_path,
                    f"Expected {expected} blocks from sidecar, found {len(zh_blocks)}",
                )
            )
            continue
        valid_sets.append((src_path, zh_path, map_path, sidecar))
    return errors, valid_sets


def _rebuild_translation_state(paths: WorkspacePaths, import_mode: str = DEFAULT_IMPORT_MODE) -> dict[str, Any]:
    if import_mode not in SUPPORTED_IMPORT_MODES:
        raise ValueError(f"Unsupported import_mode: {import_mode}")
    manifest = _load_project_manifest(paths)
    consistency_errors, valid_sets = _gather_txt_consistency(paths)

    catalog_paths = sort_paths(list((paths.machine / "texts" / "catalog").glob("*.jsonl")))
    dialogue_paths = sort_paths(list((paths.machine / "texts" / "dialogue_catalog").glob("*.jsonl")))
    text_index_path = paths.machine / "texts" / "text_index.jsonl"

    catalog_data = {path.name: _load_jsonl(path) for path in catalog_paths}
    dialogue_data = {path.name: _load_jsonl(path) for path in dialogue_paths}
    text_index_rows = _load_jsonl(text_index_path)
    text_index_by_id = {row["text_id"]: row for row in text_index_rows}

    catalog_by_text_id: dict[str, dict[str, Any]] = {}
    for rows in catalog_data.values():
        for row in rows:
            row["translation"] = ""
            row["translation_status"] = "untranslated"
            row["translation_present"] = False
            row["same_as_source"] = False
            catalog_by_text_id[row["text_id"]] = row

    dialogue_by_id: dict[str, dict[str, Any]] = {}
    for rows in dialogue_data.values():
        for row in rows:
            row["translation_speaker"] = ""
            row["translation_body"] = ""
            row["translation_status"] = "untranslated"
            row["translation_present"] = False
            row["same_as_source"] = False
            dialogue_by_id[row["dialogue_id"]] = row

    for row in text_index_rows:
        row["translation"] = ""
        row["translation_status"] = "untranslated"
        row["translation_present"] = False
        row["same_as_source"] = False

    imported_blocks = 0
    translated_blocks = 0
    unchanged_blocks = 0
    written_blocks = 0
    same_as_source_blocks = 0
    authoritative_blocks = 0
    same_as_source_authoritative_blocks = 0
    empty_blocks = 0
    explicit_empty_blocks = 0
    import_errors: list[dict[str, Any]] = list(consistency_errors)

    if not consistency_errors:
        for src_path, zh_path, _map_path, sidecar in valid_sets:
            src_blocks = _parse_blocks(src_path)
            zh_blocks = _parse_blocks(zh_path)
            for block_meta, src_block, zh_block in zip(sidecar["blocks"], src_blocks, zh_blocks):
                imported_blocks += 1
                blocks_match_source = zh_block == src_block
                if import_mode == "diff-only" and blocks_match_source:
                    unchanged_blocks += 1
                    continue
                if blocks_match_source:
                    same_as_source_blocks += 1
                explicit_empty = zh_block == "\\0"
                translation_present = bool(zh_block) or explicit_empty
                translated_text = "" if explicit_empty else zh_block
                if not translation_present:
                    empty_blocks += 1
                if explicit_empty:
                    explicit_empty_blocks += 1
                if block_meta["entry_kind"] == "dialogue_block":
                    dialogue_row = dialogue_by_id.get(block_meta["dialogue_id"])
                    if dialogue_row is None:
                        import_errors.append(
                            {
                                "type": "missing_dialogue_row",
                                "path": str(zh_path),
                                "detail": f"Dialogue row not found for {block_meta['dialogue_id']}",
                            }
                        )
                        continue
                    if translated_text:
                        lines = translated_text.splitlines()
                        speaker = lines[0] if lines else ""
                        body = "\n".join(lines[1:]) if len(lines) > 1 else ""
                        dialogue_row["translation_speaker"] = speaker
                        dialogue_row["translation_body"] = body
                        dialogue_row["translation_status"] = "translated"
                        dialogue_row["translation_present"] = True
                        dialogue_row["same_as_source"] = blocks_match_source
                        written_blocks += 1
                        translated_blocks += 1
                        authoritative_blocks += 1
                        if blocks_match_source:
                            same_as_source_authoritative_blocks += 1
                    else:
                        dialogue_row["translation_speaker"] = ""
                        dialogue_row["translation_body"] = ""
                        dialogue_row["translation_status"] = "translated" if explicit_empty else "untranslated"
                        if explicit_empty:
                            dialogue_row["translation_present"] = True
                            dialogue_row["same_as_source"] = False
                            written_blocks += 1
                            translated_blocks += 1
                            authoritative_blocks += 1
                    continue

                text_id = block_meta["text_id"]
                catalog_row = catalog_by_text_id.get(text_id)
                if catalog_row is None:
                    import_errors.append(
                        {
                            "type": "missing_catalog_row",
                            "path": str(zh_path),
                            "detail": f"Catalog row not found for {text_id}",
                        }
                    )
                    continue
                catalog_row["translation"] = translated_text
                catalog_row["translation_status"] = "translated" if translation_present else "untranslated"
                catalog_row["translation_present"] = translation_present
                catalog_row["same_as_source"] = blocks_match_source if translation_present else False
                if text_id in text_index_by_id:
                    text_index_by_id[text_id]["translation"] = translated_text
                    text_index_by_id[text_id]["translation_status"] = "translated" if translation_present else "untranslated"
                    text_index_by_id[text_id]["translation_present"] = translation_present
                    text_index_by_id[text_id]["same_as_source"] = blocks_match_source if translation_present else False
                if translation_present:
                    written_blocks += 1
                    translated_blocks += 1
                    authoritative_blocks += 1
                    if blocks_match_source:
                        same_as_source_authoritative_blocks += 1

    for path in catalog_paths:
        _write_jsonl(path, catalog_data[path.name])
    for path in dialogue_paths:
        _write_jsonl(path, dialogue_data[path.name])
    _write_jsonl(text_index_path, text_index_rows)

    txt_consistency = {
        "status": "ok" if not consistency_errors else "error",
        "error_count": len(consistency_errors),
        "errors": consistency_errors,
        "checked_file_count": len(valid_sets) + len(consistency_errors),
    }
    txt_import_result = {
        "status": "ok" if not import_errors else "error",
        "import_mode": import_mode,
        "zh_seed_mode": _manifest_zh_seed_mode(manifest),
        "imported_blocks": imported_blocks,
        "written_blocks": written_blocks,
        "translated_blocks": translated_blocks,
        "unchanged_blocks": unchanged_blocks,
        "same_as_source_blocks": same_as_source_blocks,
        "authoritative_blocks": authoritative_blocks,
        "same_as_source_authoritative_blocks": same_as_source_authoritative_blocks,
        "empty_blocks": empty_blocks,
        "explicit_empty_blocks": explicit_empty_blocks,
        "error_count": len(import_errors),
        "errors": import_errors,
    }
    json_dump(paths.reports / "txt_consistency.json", txt_consistency)
    json_dump(paths.reports / "txt_import_result.json", txt_import_result)
    json_dump(paths.machine / "reports" / "txt_consistency.json", txt_consistency)
    json_dump(paths.machine / "reports" / "txt_import_result.json", txt_import_result)
    return {"txt_consistency": txt_consistency, "txt_import_result": txt_import_result}


def import_project_txt(workspace_dir: Path, import_mode: str = DEFAULT_IMPORT_MODE) -> dict[str, Any]:
    paths = _workspace_paths(workspace_dir)
    result = _rebuild_translation_state(paths, import_mode)
    result["import_mode"] = import_mode
    result["untranslated_gdi_ui_blocks"] = _write_untranslated_gdi_ui_report(paths)
    result["ui_label_audit"] = _write_ui_label_audit(paths)
    return result


def _copy_machine_pack_reports(paths: WorkspacePaths) -> None:
    for name in (
        "dialogue_layout_plan.json",
        "dialogue_runtime_audit.json",
        "standalone_dialogue_audit.json",
        "pack_risks.json",
        "pack_result.json",
        "ui_dat_crosswalk.json",
        "dat_ui_priority.json",
        "untranslated_gdi_ui_blocks.json",
        "ui_label_audit.json",
    ):
        _copy_report_if_exists(paths.machine / "reports" / name, paths.reports / name)


def _field_matches_runtime_gdi_filter(source_file: str, field_path: str, filters: list[dict[str, Any]]) -> bool:
    for item in filters:
        if item["source_file"] != source_file:
            continue
        if field_path in item["field_paths"]:
            return True
    return False


def _write_untranslated_gdi_ui_report(paths: WorkspacePaths) -> dict[str, Any]:
    filters = stable_gdi_text_filters()
    sidecar_lookup = {sidecar["source_file"]: sidecar for _, sidecar in _read_sidecars(paths)}
    catalog_rows: list[dict[str, Any]] = []
    for path in sort_paths(list((paths.machine / "texts" / "catalog").glob("*.jsonl"))):
        catalog_rows.extend(_load_jsonl(path))

    rows: list[dict[str, Any]] = []
    for row in catalog_rows:
        if not _field_matches_runtime_gdi_filter(row["source_file"], row["field_path"], filters):
            continue
        if row.get("translation_status") == "translated":
            continue
        sidecar = sidecar_lookup.get(row["source_file"])
        bucket, file_name = _txt_relative_parts({"source_file": row["source_file"]})
        rows.append(
            {
                "text_id": row["text_id"],
                "source_file": row["source_file"],
                "field_path": row["field_path"],
                "original_text": row["original_text"],
                "txt_file": str((paths.txt_zh / bucket / file_name).resolve()),
                "source_offset_in_file": row.get("source_offset_in_file", 0),
                "note": "This DAT-backed UI text is covered by the patched stable-menu16 GDI path. Leaving it untranslated may produce mojibake in runtime.",
                "sidecar_block_index": next(
                    (
                        block["block_index"]
                        for block in sidecar["blocks"]
                        if block.get("text_id") == row["text_id"]
                    ),
                    None,
                )
                if sidecar
                else None,
            }
        )

    payload = {
        "status": "warning" if rows else "ok",
        "covered_filter_count": len(filters),
        "untranslated_block_count": len(rows),
        "blocks": rows,
    }
    json_dump(paths.reports / "untranslated_gdi_ui_blocks.json", payload)
    json_dump(paths.machine / "reports" / "untranslated_gdi_ui_blocks.json", payload)
    return payload


def _write_ui_label_audit(paths: WorkspacePaths) -> dict[str, Any]:
    class_rows = {row["text_id"]: row for row in _load_jsonl(paths.machine / "texts" / "catalog" / "class.jsonl")}
    word_rows = {row["text_id"]: row for row in _load_jsonl(paths.machine / "texts" / "catalog" / "word.jsonl")}
    unit_db = _load_json(paths.machine / "databases" / "unit.json")

    units_by_class_id: dict[int, list[dict[str, Any]]] = {}
    for record in unit_db.get("records", []):
        class_id = int(record["fields"].get("class_id", -1))
        units_by_class_id.setdefault(class_id, []).append(
            {
                "unit_id": record["record_id"],
                "unit_name": record["fields"]["name"]["text"],
            }
        )

    def translated(row: dict[str, Any]) -> str:
        return row.get("translation") or ""

    def translation_status(row: dict[str, Any]) -> str:
        if row.get("translation_status") == "translated":
            return "translated"
        return "untranslated"

    def class_entry(text_id: str, *, runtime_function: str, draw_path: str, ui_surface_id: str, notes: str) -> dict[str, Any]:
        row = class_rows[text_id]
        class_id = int(text_id.split(":")[1])
        return {
            "label_text": translated(row) or row["original_text"],
            "original_text": row["original_text"],
            "translated_text": translated(row),
            "source_file": row["source_file"],
            "record_id": row["container_id"],
            "text_id": row["text_id"],
            "field_path": row["field_path"],
            "source_offset_in_file": row.get("source_offset_in_file", 0),
            "runtime_function": runtime_function,
            "draw_path": draw_path,
            "ui_surface_id": ui_surface_id,
            "translation_status": translation_status(row),
            "special_rendering": "class name rendered as a DAT-backed UI label",
            "numeric_binding": None,
            "notes": notes,
            "linked_units": units_by_class_id.get(class_id, []),
        }

    def word_entry(
        text_id: str,
        *,
        runtime_function: str,
        draw_path: str,
        ui_surface_id: str,
        numeric_binding: str | None,
        notes: str,
    ) -> dict[str, Any]:
        row = word_rows[text_id]
        return {
            "label_text": translated(row) or row["original_text"],
            "original_text": row["original_text"],
            "translated_text": translated(row),
            "source_file": row["source_file"],
            "record_id": row["container_id"],
            "text_id": row["text_id"],
            "field_path": row["field_path"],
            "source_offset_in_file": row.get("source_offset_in_file", 0),
            "runtime_function": runtime_function,
            "draw_path": draw_path,
            "ui_surface_id": ui_surface_id,
            "translation_status": translation_status(row),
            "special_rendering": "label paired with runtime-formatted numeric values" if numeric_binding else "plain DAT-backed UI label",
            "numeric_binding": numeric_binding,
            "notes": notes,
        }

    entries = [
        class_entry(
            "class:001:name",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.class_name",
            notes="This is the translated protagonist-facing record. UNIT.DAT uses class_id=1 for unit:097 and unit:228, so this is the record most likely expected on the main unit sheet.",
        ),
        class_entry(
            "class:008:name",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.class_name",
            notes="Near-duplicate of class:001 but currently untranslated and not referenced by UNIT.DAT in the sample database. If the user still sees a star-magical-girl label elsewhere, this duplicate is a candidate only if another system references raw class id 8.",
        ),
        class_entry(
            "class:045:name",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.class_name",
            notes="Variant record used by UNIT.DAT class_id=45 (unit:166 / リーザ). It stays untranslated, so if the user is looking at that unit the visible text will still be Japanese and may mojibake on a GDI-migrated surface.",
        ),
        word_entry(
            "word:004:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.total_attack",
            numeric_binding="paired with runtime value a2[32]",
            notes="This label previously stayed on the old path because the early sub_412448 callsite was omitted from stable-menu16. After the fix it should enter the same patched GDI chain as the neighboring stat labels.",
        ),
        word_entry(
            "word:004:label",
            runtime_function="sub_41C960",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="compact_unit_card.total_attack",
            numeric_binding="paired with runtime value v16[32]",
            notes="The same WORD.DAT label is reused on the compact unit card. This confirms that '総合攻撃値' can stay Japanese on one screen simply because it has not been translated yet, even though sibling labels on the same family of screens were translated.",
        ),
        word_entry(
            "word:008:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.attack",
            numeric_binding="paired with runtime value a2[28]",
            notes="Translated label. If this one mojibakes while word:004 stays Japanese, the cause is not block lookup mismatch; it means translated and untranslated labels are reaching the same surface through different content states.",
        ),
        word_entry(
            "word:009:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.spirit",
            numeric_binding="paired with runtime value a2[29]",
            notes="Still untranslated. Compare directly against translated word:008 and word:010 on the same surface when auditing mixed-language behavior.",
        ),
        word_entry(
            "word:009:label",
            runtime_function="sub_41C960",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="compact_unit_card.spirit",
            numeric_binding="paired with runtime value v16[29]",
            notes="The compact card uses the same label through a second UI path, so mixed behavior between screens can come from surface coverage rather than translation content alone.",
        ),
        word_entry(
            "word:010:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.agility",
            numeric_binding="paired with runtime value a2[30]",
            notes="Translated label on the main unit sheet.",
        ),
        word_entry(
            "word:011:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.endurance",
            numeric_binding="paired with runtime value a2[31]",
            notes="Still untranslated on the main unit sheet.",
        ),
        word_entry(
            "word:001:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.level",
            numeric_binding="label plus formatted numeric level from runtime state",
            notes="LV label comes from WORD.DAT, but the number is runtime-generated. Translating the label itself is feasible; the adjacent numeric field is not stored in DAT and is unaffected by text translation.",
        ),
        word_entry(
            "word:002:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.hp",
            numeric_binding="label plus current/max HP from a2[26]/a2[20]",
            notes="HP label is DAT-backed, but the two numbers are runtime-formatted values. Translation should not change the numeric source, only the label width/alignment risk.",
        ),
        word_entry(
            "word:002:label",
            runtime_function="sub_41C960",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="compact_unit_card.hp",
            numeric_binding="label plus current/max HP from v16[26]/v16[20]",
            notes="Second confirmed HP surface. Any alignment issue after translation must be validated on both the main sheet and compact card.",
        ),
        word_entry(
            "word:003:label",
            runtime_function="sub_412448",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="unit_sheet_panel.mp",
            numeric_binding="label plus current/max MP from a2[27]/a2[21]",
            notes="MP behaves the same way as HP: DAT-backed label, runtime-formatted numbers, shared layout constraints.",
        ),
        word_entry(
            "word:003:label",
            runtime_function="sub_41C960",
            draw_path="patched GDI chain under stable-menu16",
            ui_surface_id="compact_unit_card.mp",
            numeric_binding="label plus current/max MP from v16[27]/v16[21]",
            notes="Second confirmed MP surface.",
        ),
    ]

    payload = {
        "status": "ok",
        "generated_at_utc": utc_timestamp(),
        "official_runtime_profile": PROJECT_RUNTIME_PROFILE,
        "summary": {
            "class_findings": [
                "The translated star-magical-girl record is class:001:name and is referenced by UNIT.DAT class_id=1 (unit:097 and unit:228).",
                "Two near-duplicates remain: class:008:name is untranslated and currently unreferenced by UNIT.DAT; class:045:name is untranslated and is used by unit:166.",
                "The translated class:001 label previously still mojibaked because sub_412448 class-name callsites were omitted from stable-menu16. Duplicate records still matter, but the display-path gap was also real.",
            ],
            "word_findings": [
                "word:004:label (総合攻撃値) previously stayed normal Japanese because its early sub_412448 callsite was omitted from stable-menu16, not because it was outside WORD.DAT.",
                "word:008 and word:010 being translated while word:009 and word:011 remained untranslated exposed that only the later half of the stat sheet had actually been redirected.",
                "After the fix, the audited stat labels in sub_412448 and sub_41C960 should both route through the patched GDI chain under stable-menu16.",
            ],
            "surface_findings": [
                "Readable Japanese unit names on the sortie roster previously meant that sub_41E028 -> sub_4203C0 / sub_41F27C / sub_4208CC had not been added to stable-menu16 yet.",
                "Readable Japanese item or magic help text previously meant that the shared downstream help-panel draw site sub_4142D4 still sat outside stable-menu16 even though adjacent menu rows were already patched.",
                "The fixed garbage prefix/suffix still visible on item, equipment, or skill names came from cp932-only punctuation constants appended by sub_40F4C4 / sub_40F650 / sub_4177E8 / sub_417D9C, not from DAT payload corruption.",
                "Readable Japanese unit names on battlefield hover labels previously meant that sub_413F80 still sat outside stable-menu16 even though the other UNIT.DAT surfaces had already been migrated.",
                "Readable Japanese or fixed mojibake markers in the camp shop item list previously meant that the shared row renderer still had one direct glyph callsite and legacy list-marker behavior outside the Chinese UI baseline.",
                "The remaining problem12 misalignment on the camp shop screen comes from the shop-specific row formatter in sub_421220/sub_4215A4 still using 22/10/8-width fields tuned for the older UI, not just from the shared GDI bridge x-offsets.",
                "The remaining problem15 misalignment on the battle skill popup comes from sub_40F930 / sub_417548 / sub_4171E0 / sub_418308 keeping a skill-popup-local row text anchor, width padding, and right-side detail anchor that were tuned for the older UI rather than from the camp shop formatter.",
                "After this fix, sortie roster rows, battlefield hover labels, the shared item/magic help panel, and the camp shop item list should behave like the rest of the patched DAT-backed UI surfaces: translated Chinese renders through GDI, while untranslated Japanese follows the documented migrated-surface boundary behavior.",
            ],
            "numeric_findings": [
                "LV/HP/MP labels come from WORD.DAT, but the adjacent numbers are runtime values formatted from unit state fields rather than DAT text.",
                "Translating LV/HP/MP is structurally safe as label-only work, but spacing and alignment must be checked because the numbers share the same UI row.",
                "The EXP and level-up popups share sub_4051A0 as their numeric formatter, and that helper previously still emitted cp932 full-width digit glyphs even after the surrounding UNIT.DAT and WORD.DAT text had been migrated onto the Chinese GDI path.",
            ],
        },
        "entries": entries,
    }
    json_dump(paths.reports / "ui_label_audit.json", payload)
    json_dump(paths.machine / "reports" / "ui_label_audit.json", payload)
    return payload


def _write_dialogue_runtime_audit(paths: WorkspacePaths, runtime_profile: str = PROJECT_RUNTIME_PROFILE) -> dict[str, Any]:
    patch_plan = build_runtime_patch_plan(paths.root, runtime_profile)
    dialogue_rows: list[dict[str, Any]] = []
    for path in sort_paths(list((paths.machine / "texts" / "dialogue_catalog").glob("*.jsonl"))):
        dialogue_rows.extend(_load_jsonl(path))

    translated_dialogue_block_count = sum(
        1
        for row in dialogue_rows
        if row.get("translation_present") or row.get("translation_status") == "translated"
    )
    patched_callsites = [
        item
        for item in patch_plan["patched_callsites"]
        if "dialogue" in str(item.get("purpose", "")).lower()
        or "opcode 1" in str(item.get("purpose", "")).lower()
        or "opcode 201" in str(item.get("purpose", "")).lower()
    ]
    payload = {
        "status": "ok" if patch_plan["dialogue_side_buffer"]["enabled"] else "error",
        "generated_at_utc": utc_timestamp(),
        "runtime_profile": runtime_profile,
        "legacy_profile_aliases": patch_plan.get("legacy_profile_aliases", []),
        "dialogue_patch_status": "enabled" if patch_plan["dialogue_side_buffer"]["enabled"] else "disabled",
        "dialogue_side_buffer": patch_plan["dialogue_side_buffer"],
        "patched_callsite_count": len(patched_callsites),
        "patched_callsites": patched_callsites,
        "translated_dialogue_block_count": translated_dialogue_block_count,
        "notes": (
            [
                "The selected runtime profile enables the heap-backed opcode 1/201 dialogue safety chain.",
                "Translated dialogue still packs as cp936 in SMAP data; this audit confirms that the side-buffer helpers required to display those bytes safely are active.",
            ]
            if patch_plan["dialogue_side_buffer"]["enabled"]
            else [
                "The selected runtime profile does not enable the heap-backed opcode 1/201 dialogue safety chain.",
                "Translated dialogue still packs as cp936 in SMAP data, but stable-menu16 stays on the older dialogue runtime path for maximum stability.",
            ]
        ),
    }
    json_dump(paths.reports / "dialogue_runtime_audit.json", payload)
    json_dump(paths.machine / "reports" / "dialogue_runtime_audit.json", payload)
    return payload


def doctor_project(workspace_dir: Path, import_mode: str = DEFAULT_IMPORT_MODE) -> dict[str, Any]:
    paths = _workspace_paths(workspace_dir)
    manifest = _load_project_manifest(paths)
    import_result = _rebuild_translation_state(paths, import_mode)
    untranslated_report = _write_untranslated_gdi_ui_report(paths)
    ui_label_audit = _write_ui_label_audit(paths)
    dialogue_runtime_audit = _write_dialogue_runtime_audit(paths)
    simulate_result = simulate_pack(paths.machine)
    _copy_machine_pack_reports(paths)
    result = {
        "status": "ok"
        if import_result["txt_consistency"]["status"] == "ok"
        and import_result["txt_import_result"]["status"] == "ok"
        and simulate_result["status"] == "ok"
        else "error",
        "import_mode": import_mode,
        "zh_seed_mode": _manifest_zh_seed_mode(manifest),
        "txt_consistency": import_result["txt_consistency"],
        "txt_import_result": import_result["txt_import_result"],
        "untranslated_gdi_ui_blocks": untranslated_report,
        "ui_label_audit": ui_label_audit,
        "dialogue_runtime_audit": dialogue_runtime_audit,
        "simulate_pack": simulate_result,
    }
    json_dump(paths.reports / "project_doctor_result.json", result)
    return result


def build_project(game_dir: Path, workspace_dir: Path, out_dir: Path, import_mode: str = DEFAULT_IMPORT_MODE) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    paths = _workspace_paths(workspace_dir)
    manifest = _load_project_manifest(paths)
    out_dir = out_dir.resolve()
    packed_dir = out_dir / "packed_game"
    playable_dir = out_dir / "playable_game"
    report_dir = out_dir / "reports"
    _remove_path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    import_result = _rebuild_translation_state(paths, import_mode)
    untranslated_report = _write_untranslated_gdi_ui_report(paths)
    ui_label_audit = _write_ui_label_audit(paths)
    dialogue_runtime_audit = _write_dialogue_runtime_audit(paths)
    simulate_result = simulate_pack(paths.machine)
    if import_result["txt_consistency"]["status"] != "ok" or import_result["txt_import_result"]["status"] != "ok" or simulate_result["status"] != "ok":
        result = {
            "status": "error",
            "import_mode": import_mode,
            "zh_seed_mode": _manifest_zh_seed_mode(manifest),
            "txt_consistency": import_result["txt_consistency"],
            "txt_import_result": import_result["txt_import_result"],
            "untranslated_gdi_ui_blocks": untranslated_report,
            "ui_label_audit": ui_label_audit,
            "dialogue_runtime_audit": dialogue_runtime_audit,
            "simulate_pack": simulate_result,
        }
        json_dump(report_dir / "project_build_result.json", result)
        return result

    pack_result = pack_game(game_dir, paths.machine, packed_dir)
    runtime_result = patch_runtime(packed_dir, playable_dir, PROJECT_RUNTIME_PROFILE)
    bitmap_resource_audit = _write_bitmap_resource_audit(paths.machine, playable_dir, playable_dir / "reports")

    report_dir.mkdir(parents=True, exist_ok=True)
    _copy_report_if_exists(paths.reports / "txt_consistency.json", report_dir / "txt_consistency.json")
    _copy_report_if_exists(paths.reports / "txt_import_result.json", report_dir / "txt_import_result.json")
    for name in (
        "pack_plan.json",
        "dialogue_layout_plan.json",
        "standalone_dialogue_audit.json",
        "pack_risks.json",
        "pack_result.json",
        "runtime_opcode_map.json",
        "runtime_text_contract.json",
        "runtime_encoding_chain.json",
        "runtime_buffer_risks.json",
        "ui_dat_crosswalk.json",
        "dat_ui_priority.json",
        "dat_growth_blockers.json",
        "dialogue_runtime_audit.json",
        "ui_label_audit.json",
    ):
        _copy_report_if_exists(packed_dir / "reports" / name, report_dir / name)
    _copy_report_if_exists(paths.reports / "untranslated_gdi_ui_blocks.json", report_dir / "untranslated_gdi_ui_blocks.json")
    _copy_report_if_exists(paths.reports / "ui_label_audit.json", report_dir / "ui_label_audit.json")
    _copy_report_if_exists(paths.reports / "dialogue_runtime_audit.json", report_dir / "dialogue_runtime_audit.json")
    _copy_report_if_exists(playable_dir / "reports" / "runtime_patch_plan.json", report_dir / "runtime_patch_plan.json")
    _copy_report_if_exists(playable_dir / "reports" / "runtime_patch_result.json", report_dir / "runtime_patch_result.json")
    _copy_report_if_exists(playable_dir / "reports" / "runtime_resource_aliases.json", report_dir / "runtime_resource_aliases.json")
    _copy_report_if_exists(playable_dir / "reports" / "ui_patch_coverage.json", report_dir / "ui_patch_coverage.json")
    _copy_report_if_exists(playable_dir / "reports" / "bitmap_resource_audit.json", report_dir / "bitmap_resource_audit.json")

    result = {
        "status": "ok"
        if pack_result["status"] == "ok" and runtime_result["status"] == "ok" and bitmap_resource_audit["status"] == "ok"
        else "error",
        "import_mode": import_mode,
        "zh_seed_mode": _manifest_zh_seed_mode(manifest),
        "runtime_profile": PROJECT_RUNTIME_PROFILE,
        "packed_game_dir": str(packed_dir),
        "playable_game_dir": str(playable_dir),
        "txt_consistency": import_result["txt_consistency"],
        "txt_import_result": import_result["txt_import_result"],
        "untranslated_gdi_ui_blocks": untranslated_report,
        "ui_label_audit": ui_label_audit,
        "dialogue_runtime_audit": dialogue_runtime_audit,
        "simulate_pack": simulate_result,
        "pack": pack_result,
        "patch_runtime": runtime_result,
        "bitmap_resource_audit": bitmap_resource_audit,
    }
    json_dump(report_dir / "project_build_result.json", result)
    return result
