from __future__ import annotations

import json
import shutil
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import SOURCE_ENCODING, bytes_to_hex, hex_to_bytes, json_dump, sha256_hex, sort_paths
from .dialogue_layout import (
    DIALOGUE_BODY_LINES_PER_PAGE,
    DIALOGUE_LINE_LIMIT_BYTES,
    build_dialogue_id,
    iter_dialogue_blocks,
    wrap_dialogue_body,
)
from .runtime_analysis import build_runtime_reports
from .runtime_patch import stable_gdi_text_filters
from .specs import FIXED_FILE_SPECS, SPECIAL_FILE_NAMES, pack_fixed_file
from .unpack import pack_mapc_file, pack_smap_file, pack_special_file, verify_roundtrip

TRANSLATION_ENCODING = "cp936"
DISPLAY_OPCODE_IDS = {1, 45, 201}
EVENT_CHUNK_SIZE = 100
RUNTIME_SLOT_SOFT_LIMIT = 72
RUNTIME_SLOT_HARD_LIMIT = 79


@dataclass
class PackIssue:
    severity: str
    text_id: str
    source_file: str
    detail: str
    code: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "text_id": self.text_id,
            "source_file": self.source_file,
            "detail": self.detail,
        }


def _normalize_text_for_compare(text: str) -> str:
    return "\n".join(text.replace("\r\n", "\n").replace("\r", "\n").splitlines())


def _field_matches_runtime_gdi_filter(source_file: str, field_path: str, filters: list[dict[str, Any]]) -> bool:
    for item in filters:
        if item["source_file"] != source_file:
            continue
        if field_path in item["field_paths"]:
            return True
    return False


def _row_translation_present(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if "translation_present" in row:
        return bool(row.get("translation_present"))
    return row.get("translation_status") == "translated"


def _same_text_writeback_allowed(row: dict[str, Any] | None) -> bool:
    if not row or not bool(row.get("same_as_source")):
        return False
    source_file = row.get("source_file", "")
    field_path = row.get("field_path", "")
    filters = stable_gdi_text_filters()
    if _field_matches_runtime_gdi_filter(source_file, field_path, filters):
        return True
    return source_file.startswith("MAP/SMAP_") and field_path == "name"


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


def _copy_game_tree(src: Path, dst: Path) -> None:
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("*.id0", "*.id1", "*.id2", "*.nam", "*.til", "*.i64"),
    )


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_translation_rows(unpack_dir: Path) -> tuple[dict[str, dict[str, Any]], list[PackIssue]]:
    rows: dict[str, dict[str, Any]] = {}
    issues: list[PackIssue] = []
    text_index = unpack_dir / "texts" / "text_index.jsonl"
    sources = [text_index, *sort_paths(list((unpack_dir / "texts" / "catalog").glob("*.jsonl")))]
    for source in sources:
        for row in _load_jsonl(source):
            if row.get("text_category") != "display_text":
                continue
            text_id = row["text_id"]
            translation = row.get("translation", "") or ""
            current = rows.get(text_id)
            if current is None:
                rows[text_id] = dict(row)
                continue
            old_translation = current.get("translation", "") or ""
            if translation and old_translation and translation != old_translation:
                issues.append(
                    PackIssue(
                        "error",
                        text_id,
                        row["source_file"],
                        f"Conflicting translations between indexes: {old_translation!r} vs {translation!r}",
                    )
                )
                continue
            if translation:
                rows[text_id] = dict(row)
    return rows, issues


def _load_dialogue_rows(unpack_dir: Path) -> tuple[dict[str, dict[str, Any]], list[PackIssue]]:
    rows: dict[str, dict[str, Any]] = {}
    issues: list[PackIssue] = []
    dialogue_dir = unpack_dir / "texts" / "dialogue_catalog"
    if not dialogue_dir.exists():
        return rows, issues
    for source in sort_paths(list(dialogue_dir.glob("*.jsonl"))):
        for row in _load_jsonl(source):
            dialogue_id = row["dialogue_id"]
            current = rows.get(dialogue_id)
            if current is None:
                rows[dialogue_id] = dict(row)
                continue
            current_speaker = current.get("translation_speaker", "") or ""
            current_body = current.get("translation_body", "") or ""
            next_speaker = row.get("translation_speaker", "") or ""
            next_body = row.get("translation_body", "") or ""
            if (current_speaker and next_speaker and current_speaker != next_speaker) or (current_body and next_body and current_body != next_body):
                issues.append(
                    PackIssue(
                        "error",
                        dialogue_id,
                        row["source_file"],
                        f"Conflicting dialogue translations between dialogue catalogs for {dialogue_id}",
                        code="dialogue_conflict",
                    )
                )
                continue
            if next_speaker or next_body:
                rows[dialogue_id] = dict(row)
    return rows, issues


def _encode_text(text: str, *, text_id: str, source_file: str, issues: list[PackIssue]) -> bytes | None:
    try:
        return text.encode(TRANSLATION_ENCODING)
    except UnicodeEncodeError as exc:
        issues.append(PackIssue("error", text_id, source_file, f"Cannot encode translation with {TRANSLATION_ENCODING}: {exc}"))
        return None


def _can_encode_text(text: str) -> bool:
    try:
        text.encode(TRANSLATION_ENCODING)
        return True
    except UnicodeEncodeError:
        return False


def _check_runtime_slot_risk(
    *,
    encoded: bytes,
    text_id: str,
    source_file: str,
    opcode_id: int | None,
    display_role: str | None,
    issues: list[PackIssue],
) -> None:
    if opcode_id not in DISPLAY_OPCODE_IDS:
        return
    label = f"opcode {opcode_id}"
    if display_role:
        label += f" ({display_role})"
    if len(encoded) > RUNTIME_SLOT_HARD_LIMIT:
        issues.append(
            PackIssue(
                "warning",
                text_id,
                source_file,
                f"Runtime slot overflow risk: {label} translation is {len(encoded)} bytes, but SRPGEXEC currently copies it into an 80-byte buffer with strcpy.",
            )
        )
        return
    if len(encoded) >= RUNTIME_SLOT_SOFT_LIMIT:
        issues.append(
            PackIssue(
                "warning",
                text_id,
                source_file,
                f"Runtime slot near limit: {label} translation is {len(encoded)} bytes; the verified runtime buffer only has 80 bytes including the terminator.",
            )
        )


def _patch_fixed_text_object(
    text_object: dict[str, Any],
    translation_row: dict[str, Any] | None,
    *,
    field_size: int,
    issues: list[PackIssue],
    allow_growth: bool,
) -> None:
    translation_present = _row_translation_present(translation_row)
    translation = "" if not translation_row else (translation_row.get("translation", "") or "")
    if not translation_present:
        return
    same_as_source = _normalize_text_for_compare(translation) == _normalize_text_for_compare(text_object.get("text", ""))
    if same_as_source and not (_same_text_writeback_allowed(translation_row) and _can_encode_text(translation)):
        return
    encoded = _encode_text(translation, text_id=text_object["text_id"], source_file=translation_row["source_file"], issues=issues)
    if encoded is None:
        return
    suffix = b"\x00" if text_object.get("null_terminated") else b""
    packed = encoded + suffix
    max_bytes = text_object.get("max_bytes")
    if not allow_growth and max_bytes is not None and len(encoded) > max_bytes:
        issues.append(
            PackIssue(
                "error",
                text_object["text_id"],
                translation_row["source_file"],
                f"Fixed slot overflow: {len(encoded)} bytes > max {max_bytes}",
            )
        )
        return
    if len(packed) > field_size:
        issues.append(
            PackIssue(
                "error",
                text_object["text_id"],
                translation_row["source_file"],
                f"Packed field overflow: {len(packed)} bytes > slot {field_size}",
            )
        )
        return
    field_bytes = packed.ljust(field_size, b"\x00")
    text_object["text"] = translation
    text_object["encoding"] = TRANSLATION_ENCODING
    text_object["actual_text_bytes_hex"] = encoded.hex()
    text_object["source_bytes_hex"] = field_bytes.hex()
    text_object["byte_length"] = len(encoded)
    text_object["char_length"] = len(translation)


def _patch_fixed_dat_export(export_data: dict[str, Any], translation_rows: dict[str, dict[str, Any]], issues: list[PackIssue]) -> None:
    spec = FIXED_FILE_SPECS[export_data["source_file"]]
    field_specs = {field.name: field for field in spec.fields if field.kind == "text"}
    for record in export_data["records"]:
        raw = bytearray(hex_to_bytes(record["raw_record_hex"]))
        changed = False
        for field_name, field_spec in field_specs.items():
            text_object = record["fields"][field_name]
            translation_row = translation_rows.get(text_object["text_id"])
            before = text_object["source_bytes_hex"]
            _patch_fixed_text_object(text_object, translation_row, field_size=field_spec.size, issues=issues, allow_growth=False)
            after = text_object["source_bytes_hex"]
            if after != before:
                raw[field_spec.offset : field_spec.offset + field_spec.size] = hex_to_bytes(after)
                changed = True
        if changed:
            record["raw_record_hex"] = raw.hex()
            record["raw_record_sha256"] = sha256_hex(bytes(raw))


def _replace_segment_bytes(
    payload: bytes,
    segments: list[dict[str, Any]],
    translation_rows: dict[str, dict[str, Any]],
    issues: list[PackIssue],
    source_file: str,
) -> bytes:
    mutable = bytearray(payload)
    # Objective-condition prompts use the legacy 0x0100 subtype even though
    # they are otherwise ordinary opcode 1/201 display commands. Normalize the
    # entire command onto 0x0200 whenever we touch it so mixed translated /
    # untranslated condition groups do not keep half of their lines on the old
    # Japanese-only render path.
    for segment in segments:
        if (
            segment.get("prefix_bytes_hex") == "0100"
            and segment.get("payload_text_start", 0) >= 2
        ):
            mutable[segment["payload_text_start"] - 2 : segment["payload_text_start"]] = b"\x02\x00"
    for segment in sorted(segments, key=lambda item: item["payload_text_start"], reverse=True):
        translation_row = translation_rows.get(segment["text_id"])
        if not _row_translation_present(translation_row):
            continue
        translation = "" if not translation_row else (translation_row.get("translation", "") or "")
        encoded = _encode_text(translation, text_id=segment["text_id"], source_file=source_file, issues=issues)
        if encoded is None:
            continue
        _check_runtime_slot_risk(
            encoded=encoded,
            text_id=segment["text_id"],
            source_file=source_file,
            opcode_id=segment.get("opcode_id"),
            display_role=segment.get("display_role"),
            issues=issues,
        )
        mutable[segment["payload_text_start"] : segment["payload_text_end"]] = encoded
    return bytes(mutable)


def _needs_condition_prompt_segment_rewrite(
    command: dict[str, Any],
    translation_rows: dict[str, dict[str, Any]],
) -> bool:
    if command.get("command_id") not in {1, 201}:
        return False
    for segment in command.get("display_segments", []):
        if segment.get("prefix_bytes_hex") != "0100":
            continue
        translation_row = translation_rows.get(segment["text_id"])
        if _row_translation_present(translation_row):
            return True
    return False


def _translated_display_segments(
    command: dict[str, Any],
    translation_rows: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    translated_segments: list[dict[str, Any]] = []
    for segment in command.get("display_segments", []):
        translation_row = translation_rows.get(segment["text_id"])
        if _row_translation_present(translation_row):
            translated_segments.append(segment)
    return translated_segments


def _is_standalone_dialogue_display_command(
    command: dict[str, Any],
    translation_rows: dict[str, dict[str, Any]],
) -> bool:
    if command.get("command_id") not in {1, 201}:
        return False
    translated_segments = _translated_display_segments(command, translation_rows)
    if not translated_segments:
        return False
    return any(segment.get("display_role") in {"speaker", "dialogue"} for segment in translated_segments)


def _rewrite_standalone_dialogue_display_command(
    *,
    command: dict[str, Any],
    translation_rows: dict[str, dict[str, Any]],
    issues: list[PackIssue],
    source_file: str,
    event_id: str,
    event_name_internal: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = hex_to_bytes(command["payload_hex"])
    rebuilt_payload = _replace_segment_bytes(
        payload,
        command.get("display_segments", []),
        translation_rows,
        issues,
        source_file,
    )
    translated_segments = _translated_display_segments(command, translation_rows)
    translated_rows = [translation_rows.get(segment["text_id"], {}) for segment in translated_segments]
    translated_texts = [
        (row.get("translation", "") or segment.get("text", ""))
        for segment, row in zip(translated_segments, translated_rows, strict=False)
    ]
    speaker = next(
        (
            row.get("speaker")
            for row in translated_rows
            if isinstance(row, dict) and (row.get("speaker") or "")
        ),
        None,
    )
    updated_command = {
        **command,
        "payload_hex": rebuilt_payload.hex(),
        "length": len(rebuilt_payload),
        "command_total_length": 4 + len(rebuilt_payload),
        "command_length_field_value": len(rebuilt_payload),
        "_dialogue_payload_rebuilt": True,
    }
    report = {
        "status": "rewritten",
        "source_file": source_file,
        "event_id": event_id,
        "event_name_internal": event_name_internal,
        "command_index": command.get("command_index"),
        "command_id": command.get("command_id"),
        "text_id": translated_segments[0]["text_id"] if translated_segments else None,
        "text_ids": [segment["text_id"] for segment in translated_segments],
        "speaker": speaker,
        "translation": "\n".join(translated_texts),
        "payload_changed": rebuilt_payload != payload,
    }
    return updated_command, report


def _dialogue_block_uses_legacy_subtype(block: Any) -> bool:
    segments: list[dict[str, Any]] = []
    speaker_segment = _dialogue_segment(block.speaker_command)
    if speaker_segment is not None:
        segments.append(speaker_segment)
    for command in block.body_commands:
        segment = _dialogue_segment(command)
        if segment is not None:
            segments.append(segment)
    return any(segment.get("prefix_bytes_hex") == "0100" for segment in segments)


def _dialogue_segment(command: dict[str, Any]) -> dict[str, Any] | None:
    segments = command.get("display_segments", [])
    return segments[0] if segments else None


def _dialogue_block_has_authoritative_same_source(
    *,
    dialogue_row: dict[str, Any] | None,
    speaker_translation_row: dict[str, Any] | None,
    body_segments: list[dict[str, Any]],
    translation_rows: dict[str, dict[str, Any]],
) -> bool:
    if _row_translation_present(dialogue_row) and bool(dialogue_row.get("same_as_source")):
        return True
    if _row_translation_present(speaker_translation_row) and bool(speaker_translation_row.get("same_as_source")):
        return True
    for segment in body_segments:
        row = translation_rows.get(segment["text_id"])
        if _row_translation_present(row) and bool(row.get("same_as_source")):
            return True
    return False


def _resolve_dialogue_block_translation(
    *,
    block: Any,
    dialogue_rows: dict[str, dict[str, Any]],
    translation_rows: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    speaker_segment = _dialogue_segment(block.speaker_command)
    if speaker_segment is None:
        return None
    body_segments = [_dialogue_segment(command) for command in block.body_commands]
    body_segments = [segment for segment in body_segments if segment is not None]
    dialogue_row = dialogue_rows.get(block.dialogue_id)

    row_speaker = ""
    row_body = ""
    if dialogue_row is not None:
        row_speaker = dialogue_row.get("translation_speaker", "") or ""
        row_body = dialogue_row.get("translation_body", "") or ""

    command_speaker = ""
    speaker_translation_row = translation_rows.get(speaker_segment["text_id"])
    if speaker_translation_row is not None:
        command_speaker = speaker_translation_row.get("translation", "") or ""

    command_body_lines: list[str] = []
    body_has_command_translation = False
    for segment in body_segments:
        row = translation_rows.get(segment["text_id"])
        translated = "" if row is None else (row.get("translation", "") or "")
        command_body_lines.append(translated or segment["text"])
        body_has_command_translation = body_has_command_translation or bool(translated)

    speaker_text = row_speaker or command_speaker or speaker_segment["text"]
    body_text = row_body if row_body else "\n".join(command_body_lines)
    original_body_text = "\n".join(segment["text"] for segment in body_segments)

    has_block_translation = bool(row_speaker or row_body)
    has_command_translation = bool(command_speaker) or body_has_command_translation
    if not has_block_translation and not has_command_translation:
        return None
    if (
        _normalize_text_for_compare(speaker_text) == _normalize_text_for_compare(speaker_segment["text"])
        and _normalize_text_for_compare(body_text) == _normalize_text_for_compare(original_body_text)
    ):
        # Authoritative dialogue blocks may legitimately stay text-identical to
        # the source when a translation uses the same visible characters as the
        # original (for example kanji names, scene headings, or lines such as
        # "玲奈：\n……"). These still need a full rebuild so the event payload
        # is regenerated in CP936 instead of leaving original CP932 bytes in
        # place. Legacy 0x0100 prompt blocks also require rebuild even when the
        # text is source-identical so the subtype can be normalized to 0x0200.
        if not (
            _dialogue_block_has_authoritative_same_source(
                dialogue_row=dialogue_row,
                speaker_translation_row=speaker_translation_row,
                body_segments=body_segments,
                translation_rows=translation_rows,
            )
            or (_row_translation_present(dialogue_row) and _dialogue_block_uses_legacy_subtype(block))
        ):
            return None

    return {
        "dialogue_id": block.dialogue_id,
        "source_file": block.source_file,
        "speaker_text": speaker_text,
        "body_text": body_text,
        "translation_source": "dialogue_catalog" if has_block_translation else "command_catalog",
        "speaker_segment": speaker_segment,
        "body_segments": body_segments,
    }


def _build_dialogue_command_from_template(
    *,
    template_command: dict[str, Any],
    text: str,
    issues: list[PackIssue],
    source_file: str,
    text_id: str,
) -> dict[str, Any] | None:
    segment = _dialogue_segment(template_command)
    if segment is None:
        return None
    encoded = _encode_text(text, text_id=text_id, source_file=source_file, issues=issues)
    if encoded is None:
        return None
    if len(encoded) > DIALOGUE_LINE_LIMIT_BYTES:
        issues.append(
            PackIssue(
                "error",
                text_id,
                source_file,
                f"Dialogue line exceeds {DIALOGUE_LINE_LIMIT_BYTES} bytes after layout: {len(encoded)} bytes",
                code="dialogue_line_overflow",
            )
        )
        return None
    prefix = hex_to_bytes(segment["prefix_bytes_hex"])
    # Some objective-condition prompts are stored as opcode 1/201 with a 0x0100
    # subtype prefix. In the current runtime this subtype still falls onto the
    # legacy Japanese-oriented render path and mojibakes translated CP936 text.
    # Rewriting translated lines onto the normal 0x0200 dialogue subtype keeps
    # them in the ordinary dialogue presentation while entering the verified
    # CP936-capable path.
    if prefix == b"\x01\x00":
        prefix = b"\x02\x00"
    suffix = hex_to_bytes(segment["suffix_bytes_hex"])
    payload = prefix + encoded + suffix
    return {
        **template_command,
        "payload_hex": payload.hex(),
        "length": len(payload),
        "command_total_length": 4 + len(payload),
        "command_length_field_value": len(payload),
        "_dialogue_payload_rebuilt": True,
    }


def _rewrite_dialogue_block(
    *,
    block: Any,
    translation: dict[str, Any],
    issues: list[PackIssue],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    speaker_text = translation["speaker_text"]
    body_text = translation["body_text"]
    speaker_bytes = _encode_text(
        speaker_text,
        text_id=translation["speaker_segment"]["text_id"],
        source_file=translation["source_file"],
        issues=issues,
    )
    if speaker_bytes is None:
        return [block.speaker_command, *block.body_commands], {
            "dialogue_id": block.dialogue_id,
            "source_file": block.source_file,
            "event_id": block.event_id,
            "event_name_internal": block.event_name_internal,
            "status": "error",
            "translation_source": translation["translation_source"],
        }
    if len(speaker_bytes) > DIALOGUE_LINE_LIMIT_BYTES:
        issues.append(
            PackIssue(
                "error",
                translation["speaker_segment"]["text_id"],
                translation["source_file"],
                f"Speaker line exceeds {DIALOGUE_LINE_LIMIT_BYTES} bytes: {len(speaker_bytes)} bytes",
                code="speaker_overflow",
            )
        )
        return [block.speaker_command, *block.body_commands], {
            "dialogue_id": block.dialogue_id,
            "source_file": block.source_file,
            "event_id": block.event_id,
            "event_name_internal": block.event_name_internal,
            "status": "error",
            "translation_source": translation["translation_source"],
        }

    pages, layout_flags = wrap_dialogue_body(
        body_text,
        line_limit_bytes=DIALOGUE_LINE_LIMIT_BYTES,
        body_lines_per_page=DIALOGUE_BODY_LINES_PER_PAGE,
    )
    rewritten_commands: list[dict[str, Any]] = []
    body_templates = block.body_commands or []
    speaker_text_id = translation["speaker_segment"]["text_id"]
    body_text_ids = [segment["text_id"] for segment in translation["body_segments"]]
    original_body_line_count = len(block.body_commands)
    new_body_line_count = sum(len(page) for page in pages)

    speaker_template = block.speaker_command
    first_body_template = body_templates[-1] if body_templates else None
    for page_index, page_lines in enumerate(pages):
        speaker_command = _build_dialogue_command_from_template(
            template_command=speaker_template,
            text=speaker_text,
            issues=issues,
            source_file=translation["source_file"],
            text_id=speaker_text_id,
        )
        if speaker_command is None:
            continue
        rewritten_commands.append(speaker_command)
        for line_index, line_text in enumerate(page_lines):
            if first_body_template is None:
                continue
            template_index = min(line_index, len(body_templates) - 1)
            template_command = body_templates[template_index]
            template_text_id = body_text_ids[min(template_index, len(body_text_ids) - 1)] if body_text_ids else block.dialogue_id
            body_command = _build_dialogue_command_from_template(
                template_command=template_command,
                text=line_text,
                issues=issues,
                source_file=translation["source_file"],
                text_id=template_text_id,
            )
            if body_command is not None:
                rewritten_commands.append(body_command)
        if page_index < len(pages) - 1:
            layout_flags["auto_pagination_applied"] = True

    report = {
        "dialogue_id": block.dialogue_id,
        "source_file": block.source_file,
        "event_id": block.event_id,
        "event_name_internal": block.event_name_internal,
        "start_command_index": block.start_command_index,
        "end_command_index": block.end_command_index,
        "translation_source": translation["translation_source"],
        "status": "rewritten",
        "original_body_line_count": original_body_line_count,
        "new_body_line_count": new_body_line_count,
        "new_page_count": len(pages),
        "speaker_repeated": len(pages) > 1,
        **layout_flags,
    }
    for flag_name, enabled in layout_flags.items():
        if not enabled:
            continue
        issues.append(
            PackIssue(
                "warning",
                block.dialogue_id,
                block.source_file,
                f"Dialogue layout applied: {flag_name}",
                code=flag_name,
            )
        )
    return rewritten_commands, report


def _reindex_commands(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reindexed: list[dict[str, Any]] = []
    for command_index, command in enumerate(commands):
        updated = dict(command)
        updated["command_index"] = command_index
        reindexed.append(updated)
    return reindexed


def _apply_dialogue_layout_to_event(
    event_export: dict[str, Any],
    dialogue_rows: dict[str, dict[str, Any]],
    translation_rows: dict[str, dict[str, Any]],
    issues: list[PackIssue],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    commands = event_export["commands"]
    blocks = iter_dialogue_blocks(
        map_id=event_export["map_id"],
        source_file=event_export["source_file"],
        event_id=event_export["event_id"],
        event_name_internal=event_export["event_name_internal"],
        commands=commands,
    )
    layout_reports: list[dict[str, Any]] = []
    standalone_reports: list[dict[str, Any]] = []
    block_by_start = {block.start_command_index: block for block in blocks}
    new_commands: list[dict[str, Any]] = []
    index = 0
    while index < len(commands):
        block = block_by_start.get(index)
        if block is None:
            command = commands[index]
            if _is_standalone_dialogue_display_command(command, translation_rows):
                rewritten_command, report = _rewrite_standalone_dialogue_display_command(
                    command=command,
                    translation_rows=translation_rows,
                    issues=issues,
                    source_file=event_export["source_file"],
                    event_id=event_export["event_id"],
                    event_name_internal=event_export["event_name_internal"],
                )
                new_commands.append(rewritten_command)
                standalone_reports.append(report)
            else:
                new_commands.append(command)
            index += 1
            continue
        translation = _resolve_dialogue_block_translation(block=block, dialogue_rows=dialogue_rows, translation_rows=translation_rows)
        if translation is None:
            new_commands.extend(commands[index : block.end_command_index + 1])
            index = block.end_command_index + 1
            continue
        rewritten_commands, report = _rewrite_dialogue_block(block=block, translation=translation, issues=issues)
        new_commands.extend(rewritten_commands)
        layout_reports.append(report)
        index = block.end_command_index + 1

    event_export["commands"] = _reindex_commands(new_commands)
    return layout_reports, standalone_reports


def _extract_command_region_trailer(event_export: dict[str, Any]) -> bytes:
    raw_event = hex_to_bytes(event_export["raw_event_bytes_hex"])
    command_region_offset = event_export.get("command_region_offset", 104)
    command_region_end = min(len(raw_event), command_region_offset + event_export["command_bytes_length"])
    parsed_command_bytes = sum(4 + command["length"] for command in event_export["commands"])
    parsed_end = min(command_region_end, command_region_offset + parsed_command_bytes)
    return raw_event[parsed_end:command_region_end]


def _build_event_bytes(event_export: dict[str, Any], translation_rows: dict[str, dict[str, Any]], issues: list[PackIssue]) -> tuple[bytes, dict[str, Any]]:
    event_bytes = bytearray()
    event_bytes.extend(hex_to_bytes(event_export["event_label"]["source_bytes_hex"]).ljust(36, b"\x00")[:36])
    event_bytes.extend(hex_to_bytes(event_export["event_zero_padding_hex"]).ljust(64, b"\x00")[:64])
    command_trailer = _extract_command_region_trailer(event_export)
    command_bytes = bytearray()
    changed_commands = 0
    for command in event_export["commands"]:
        payload = hex_to_bytes(command["payload_hex"])
        # Dialogue opcodes 1/201 are handled earlier by the dialogue layout stage.
        if command["command_id"] == 45 or (
            not command.get("_dialogue_payload_rebuilt")
            and _needs_condition_prompt_segment_rewrite(command, translation_rows)
        ):
            rebuilt_payload = _replace_segment_bytes(payload, command.get("display_segments", []), translation_rows, issues, event_export["source_file"])
            if rebuilt_payload != payload:
                changed_commands += 1
            payload = rebuilt_payload
        command_bytes.append(command["command_id"] & 0xFF)
        command_bytes.extend(struct.pack("<H", len(payload)))
        command_bytes.append(command["unk_byte"] & 0xFF)
        command_bytes.extend(payload)
        command["length"] = len(payload)
        command["command_total_length"] = 4 + len(payload)
        command["payload_hex"] = payload.hex()
        command["command_length_field_value"] = len(payload)
    command_region = bytes(command_bytes) + command_trailer
    event_bytes.extend(struct.pack("<I", len(command_region)))
    event_bytes.extend(command_region)
    updated = {
        "changed_commands": changed_commands,
        "old_declared_length": event_export["declared_length"],
        "new_declared_length": len(event_bytes),
        "old_chunk_count": len(event_export["chunk_chain"]),
        "new_chunk_count": (len(event_bytes) + EVENT_CHUNK_SIZE - 1) // EVENT_CHUNK_SIZE,
    }
    event_export["command_bytes_length"] = len(command_region)
    event_export["declared_length"] = len(event_bytes)
    event_export["padded_length"] = updated["new_chunk_count"] * EVENT_CHUNK_SIZE
    event_export["command_trailer_hex"] = command_trailer.hex()
    event_export["raw_event_bytes_hex"] = event_bytes.hex()
    event_export["raw_event_sha256"] = sha256_hex(bytes(event_bytes))
    return bytes(event_bytes), updated


def _reallocate_chunk_chains(smap_export: dict[str, Any], event_exports: list[dict[str, Any]]) -> None:
    original_by_decl = {item["declaration_index"]: [node["chunk_index"] for node in item["chunk_chain"]] for item in smap_export["event_declarations"]}
    allocated = {chunk for chain in original_by_decl.values() for chunk in chain}
    free_chunks = [index for index in range(len(smap_export["ev_chunks"])) if index not in allocated]
    new_ev_chunks = [-1] * len(smap_export["ev_chunks"])
    for declaration in smap_export["event_declarations"]:
        event_export = next(item for item in event_exports if item["declaration_index"] == declaration["declaration_index"])
        required = event_export["padded_length"] // EVENT_CHUNK_SIZE
        original = original_by_decl.get(declaration["declaration_index"], [])
        assigned = original[:required]
        if required > len(assigned):
            need = required - len(assigned)
            if need > len(free_chunks):
                raise ValueError(f"Not enough free event chunks for {declaration['internal_name']}")
            assigned.extend(free_chunks[:need])
            del free_chunks[:need]
        for freed in original[required:]:
            free_chunks.append(freed)
        chain = []
        for index, chunk_index in enumerate(assigned):
            next_chunk = assigned[index + 1] if index + 1 < len(assigned) else -2
            new_ev_chunks[chunk_index] = next_chunk
            chain.append({"chunk_index": chunk_index, "next_chunk": next_chunk, "file_offset": None})
        declaration["first_chunk"] = assigned[0] if assigned else -2
        declaration["declared_length"] = event_export["declared_length"]
        declaration["padded_length"] = event_export["padded_length"]
        declaration["chunk_chain"] = chain
        event_export["first_chunk"] = declaration["first_chunk"]
        event_export["chunk_chain"] = chain
    smap_export["ev_chunks"] = new_ev_chunks


def _build_smap_pack_result(
    smap_export: dict[str, Any],
    event_updates: list[dict[str, Any]],
    dialogue_layout_reports: list[dict[str, Any]],
    standalone_dialogue_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    changed = [item for item in event_updates if item["changed_commands"] > 0 or item["old_declared_length"] != item["new_declared_length"]]
    rewritten = [item for item in dialogue_layout_reports if item.get("status") == "rewritten"]
    standalone_rewritten = [item for item in standalone_dialogue_reports if item.get("status") == "rewritten"]
    return {
        "source_file": smap_export["source_file"],
        "map_id": smap_export["map_id"],
        "event_count": len(event_updates),
        "changed_event_count": len(changed),
        "dialogue_blocks_rewritten": len(rewritten),
        "dialogue_pages_added": sum(max(0, item.get("new_page_count", 1) - 1) for item in rewritten),
        "translated_standalone_dialogue_segment_count": len(standalone_dialogue_reports),
        "rewritten_standalone_dialogue_segment_count": len(standalone_rewritten),
        "events": event_updates,
        "dialogue_layout": dialogue_layout_reports,
        "standalone_dialogue": standalone_dialogue_reports,
    }


def _apply_smap_translations(
    smap_export: dict[str, Any],
    event_exports: list[dict[str, Any]],
    dialogue_rows: dict[str, dict[str, Any]],
    translation_rows: dict[str, dict[str, Any]],
    issues: list[PackIssue],
) -> dict[str, Any]:
    _patch_fixed_text_object(smap_export["name"], translation_rows.get(smap_export["name"]["text_id"]), field_size=20, issues=issues, allow_growth=False)
    event_updates = []
    dialogue_layout_reports: list[dict[str, Any]] = []
    standalone_dialogue_reports: list[dict[str, Any]] = []
    for event_export in event_exports:
        event_dialogue_reports, event_standalone_reports = _apply_dialogue_layout_to_event(
            event_export,
            dialogue_rows,
            translation_rows,
            issues,
        )
        dialogue_layout_reports.extend(event_dialogue_reports)
        standalone_dialogue_reports.extend(event_standalone_reports)
        _, update = _build_event_bytes(event_export, translation_rows, issues)
        update["event_id"] = event_export["event_id"]
        update["event_name_internal"] = event_export["event_name_internal"]
        update["crosses_chunk_boundary"] = update["old_chunk_count"] != update["new_chunk_count"]
        update["dialogue_blocks_rewritten"] = sum(1 for item in event_dialogue_reports if item.get("status") == "rewritten")
        update["dialogue_pages_added"] = sum(max(0, item.get("new_page_count", 1) - 1) for item in event_dialogue_reports if item.get("status") == "rewritten")
        update["translated_standalone_dialogue_segments"] = len(event_standalone_reports)
        update["rewritten_standalone_dialogue_segments"] = sum(1 for item in event_standalone_reports if item.get("status") == "rewritten")
        event_updates.append(update)
    _reallocate_chunk_chains(smap_export, event_exports)
    return _build_smap_pack_result(smap_export, event_updates, dialogue_layout_reports, standalone_dialogue_reports)


def _write_reports(
    report_dir: Path,
    *,
    pack_plan: list[dict[str, Any]],
    dialogue_layout_plan: list[dict[str, Any]],
    standalone_dialogue_audit: list[dict[str, Any]],
    issues: list[PackIssue],
    result: dict[str, Any],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_dump(report_dir / "pack_plan.json", {"events": pack_plan})
    json_dump(report_dir / "dialogue_layout_plan.json", {"dialogue_blocks": dialogue_layout_plan})
    json_dump(
        report_dir / "standalone_dialogue_audit.json",
        {
            "status": "ok"
            if len(standalone_dialogue_audit)
            == sum(1 for item in standalone_dialogue_audit if item.get("status") == "rewritten")
            else "warning",
            "translated_standalone_dialogue_segment_count": len(standalone_dialogue_audit),
            "rewritten_standalone_dialogue_segment_count": sum(
                1 for item in standalone_dialogue_audit if item.get("status") == "rewritten"
            ),
            "items": standalone_dialogue_audit,
        },
    )
    json_dump(report_dir / "pack_risks.json", {"issues": [issue.to_dict() for issue in issues]})
    json_dump(report_dir / "pack_result.json", result)


def simulate_pack(unpack_dir: Path) -> dict[str, Any]:
    unpack_dir = unpack_dir.resolve()
    translation_rows, issues = _load_translation_rows(unpack_dir)
    dialogue_rows, dialogue_issues = _load_dialogue_rows(unpack_dir)
    issues.extend(dialogue_issues)
    pack_plan: list[dict[str, Any]] = []
    dialogue_layout_plan: list[dict[str, Any]] = []
    standalone_dialogue_audit: list[dict[str, Any]] = []
    for smap_dir in sort_paths([path for path in (unpack_dir / "maps").iterdir() if path.is_dir() and path.name.startswith("SMAP_")]):
        smap_export = _load_json(smap_dir / "smap.json")
        event_exports = [_load_json(path) for path in sort_paths(list((smap_dir / "events").glob("*.json")))]
        plan = _apply_smap_translations(smap_export, event_exports, dialogue_rows, translation_rows, issues)
        pack_plan.append(plan)
        dialogue_layout_plan.extend(plan.get("dialogue_layout", []))
        standalone_dialogue_audit.extend(plan.get("standalone_dialogue", []))
    result = {
        "status": "ok" if not any(issue.severity == "error" for issue in issues) else "error",
        "target_encoding": TRANSLATION_ENCODING,
        "smap_count": len(pack_plan),
        "changed_event_count": sum(item["changed_event_count"] for item in pack_plan),
        "dialogue_blocks_rewritten": sum(item.get("dialogue_blocks_rewritten", 0) for item in pack_plan),
        "dialogue_pages_added": sum(item.get("dialogue_pages_added", 0) for item in pack_plan),
        "translated_standalone_dialogue_segment_count": len(standalone_dialogue_audit),
        "rewritten_standalone_dialogue_segment_count": sum(
            1 for item in standalone_dialogue_audit if item.get("status") == "rewritten"
        ),
        "error_count": sum(1 for issue in issues if issue.severity == "error"),
        "warning_count": sum(1 for issue in issues if issue.severity != "error"),
    }
    _write_reports(
        unpack_dir / "reports",
        pack_plan=pack_plan,
        dialogue_layout_plan=dialogue_layout_plan,
        standalone_dialogue_audit=standalone_dialogue_audit,
        issues=issues,
        result=result,
    )
    return result


def pack_game(game_dir: Path, unpack_dir: Path, out_dir: Path) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    unpack_dir = unpack_dir.resolve()
    out_dir = out_dir.resolve()
    translation_rows, issues = _load_translation_rows(unpack_dir)
    dialogue_rows, dialogue_issues = _load_dialogue_rows(unpack_dir)
    issues.extend(dialogue_issues)
    _remove_path(out_dir)
    _copy_game_tree(game_dir, out_dir)
    pack_plan: list[dict[str, Any]] = []
    dialogue_layout_plan: list[dict[str, Any]] = []
    standalone_dialogue_audit: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []

    for file_name in FIXED_FILE_SPECS:
        export_data = _load_json(unpack_dir / "databases" / f"{Path(file_name).stem.lower()}.json")
        _patch_fixed_dat_export(export_data, translation_rows, issues)
        packed = pack_fixed_file(export_data, FIXED_FILE_SPECS[file_name])
        (out_dir / file_name).write_bytes(packed)
        file_results.append({"source_file": file_name, "packed_sha256": sha256_hex(packed), "type": FIXED_FILE_SPECS[file_name].file_type})

    for file_name in SPECIAL_FILE_NAMES:
        export_data = _load_json(unpack_dir / "databases" / f"{Path(file_name).stem.lower()}.json")
        packed = pack_special_file(export_data)
        (out_dir / file_name).write_bytes(packed)
        file_results.append({"source_file": file_name, "packed_sha256": sha256_hex(packed), "type": export_data["file_type"]})

    for export_path in sort_paths(list((unpack_dir / "maps").glob("MAPC_*.json"))):
        export_data = _load_json(export_path)
        packed = pack_mapc_file(export_data)
        (out_dir / export_data["source_file"]).write_bytes(packed)
        file_results.append({"source_file": export_data["source_file"], "packed_sha256": sha256_hex(packed), "type": "MAPC"})

    for smap_dir in sort_paths([path for path in (unpack_dir / "maps").iterdir() if path.is_dir() and path.name.startswith("SMAP_")]):
        smap_export = _load_json(smap_dir / "smap.json")
        event_exports = [_load_json(path) for path in sort_paths(list((smap_dir / "events").glob("*.json")))]
        plan = _apply_smap_translations(smap_export, event_exports, dialogue_rows, translation_rows, issues)
        pack_plan.append(plan)
        dialogue_layout_plan.extend(plan.get("dialogue_layout", []))
        standalone_dialogue_audit.extend(plan.get("standalone_dialogue", []))
        packed = pack_smap_file(smap_export, event_exports)
        (out_dir / smap_export["source_file"]).write_bytes(packed)
        file_results.append({"source_file": smap_export["source_file"], "packed_sha256": sha256_hex(packed), "type": "SMAP"})

    runtime_reports = build_runtime_reports(game_dir)
    report_dir = out_dir / "reports"
    result = {
        "status": "ok" if not any(issue.severity == "error" for issue in issues) else "error",
        "target_encoding": TRANSLATION_ENCODING,
        "file_results": file_results,
        "dialogue_blocks_rewritten": sum(item.get("dialogue_blocks_rewritten", 0) for item in pack_plan),
        "dialogue_pages_added": sum(item.get("dialogue_pages_added", 0) for item in pack_plan),
        "translated_standalone_dialogue_segment_count": len(standalone_dialogue_audit),
        "rewritten_standalone_dialogue_segment_count": sum(
            1 for item in standalone_dialogue_audit if item.get("status") == "rewritten"
        ),
        "error_count": sum(1 for issue in issues if issue.severity == "error"),
        "warning_count": sum(1 for issue in issues if issue.severity != "error"),
        "runtime_patch_status": runtime_reports["runtime_encoding_chain"]["cp936_assessment"]["status"],
    }
    _write_reports(
        report_dir,
        pack_plan=pack_plan,
        dialogue_layout_plan=dialogue_layout_plan,
        standalone_dialogue_audit=standalone_dialogue_audit,
        issues=issues,
        result=result,
    )
    json_dump(report_dir / "runtime_opcode_map.json", runtime_reports["runtime_opcode_map"])
    json_dump(report_dir / "runtime_text_contract.json", runtime_reports["runtime_text_contract"])
    json_dump(report_dir / "runtime_encoding_chain.json", runtime_reports["runtime_encoding_chain"])
    json_dump(report_dir / "runtime_buffer_risks.json", runtime_reports["runtime_buffer_risks"])
    json_dump(report_dir / "ui_dat_crosswalk.json", runtime_reports["ui_dat_crosswalk"])
    json_dump(report_dir / "dat_ui_priority.json", runtime_reports["dat_ui_priority"])
    json_dump(report_dir / "dat_growth_blockers.json", runtime_reports["dat_growth_blockers"])
    result["verify_roundtrip"] = verify_roundtrip(game_dir, unpack_dir)
    return result


def inspect_runtime(game_dir: Path) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    reports = build_runtime_reports(game_dir)
    return {
        "status": "ok",
        "game_dir": str(game_dir),
        **reports,
    }
