from __future__ import annotations

import json
import shutil
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import (
    SOURCE_ENCODING,
    TOOL_VERSION,
    build_text_object,
    bytes_to_hex,
    file_sha256,
    hex_to_bytes,
    json_dump,
    jsonl_dump,
    looks_like_resource_reference,
    read_null_terminated_text,
    sanitize_name,
    sha256_hex,
    sort_paths,
    utc_timestamp,
)
from .dialogue_layout import build_dialogue_catalog_row, dialogue_catalog_name, iter_dialogue_blocks
from .engine_analysis import build_text_flow_report
from .runtime_analysis import build_runtime_reports
from .repack_analysis import build_repack_readiness_report as build_repack_readiness_report_from_exports
from .smap_text import DISPLAY_OPCODE_SPECS, NOISE_OPCODE_SET, RESOURCE_OPCODE_SPECS, extract_command_texts
from .specs import FIXED_FILE_SPECS, SPECIAL_FILE_NAMES, parse_fixed_file, pack_fixed_file

SUPPORTED_TOP_LEVEL = tuple(FIXED_FILE_SPECS) + SPECIAL_FILE_NAMES


class TextCatalogBuilder:
    def __init__(self, reference_map: dict[str, str] | None = None) -> None:
        self.reference_map = reference_map or {}
        self.display_rows: list[dict[str, Any]] = []
        self.resource_rows: list[dict[str, Any]] = []
        self.rejected_rows: list[dict[str, Any]] = []
        self.catalog_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._opcode_stats: dict[int, dict[str, Any]] = defaultdict(
            lambda: {
                "opcode_id": 0,
                "command_count": 0,
                "categories": set(),
                "classifiers": set(),
                "display_roles": set(),
                "display_segment_count": 0,
                "resource_segment_count": 0,
                "rejected_segment_count": 0,
                "sample_display_texts": [],
                "sample_resource_refs": [],
                "sample_rejected_texts": [],
            }
        )

    def _translation_info(self, original_text: str, text_category: str) -> tuple[str, str]:
        if text_category != "display_text":
            return "", "not_applicable"
        translation = self.reference_map.get(original_text, "")
        return translation, "reference" if translation else "untranslated"

    def _catalog_name(self, source_file: str) -> str:
        return f"{Path(source_file).stem.lower()}.jsonl"

    def _make_row(
        self,
        *,
        text_id: str,
        source_file: str,
        text_category: str,
        container_type: str,
        container_id: str,
        field_path: str,
        original_text: str,
        original_bytes_hex: str,
        source_offset_in_file: int,
        max_bytes: int | None,
        is_fixed_size: bool,
        supports_length_growth: bool,
        linebreak_mode: str,
        speaker: str | None,
        context_preview: str,
        ui_risk: str,
        pack_risk: str,
        opcode_id: int | None = None,
        display_role: str | None = None,
        payload_text_start: int | None = None,
        payload_text_end: int | None = None,
        prefix_bytes_hex: str | None = None,
        suffix_bytes_hex: str | None = None,
        terminator_policy: str | None = None,
        command_length_field_value: int | None = None,
        length_owner_fields: list[str] | None = None,
        repack_strategy: str | None = None,
        buffer_risk: str | None = None,
        notes: list[str] | None = None,
    ) -> dict[str, Any]:
        translation, translation_status = self._translation_info(original_text, text_category)
        return {
            "text_id": text_id,
            "text_category": text_category,
            "source_file": source_file,
            "container_type": container_type,
            "container_id": container_id,
            "field_path": field_path,
            "original_text": original_text,
            "original_bytes_hex": original_bytes_hex,
            "source_encoding": SOURCE_ENCODING,
            "source_offset_in_file": source_offset_in_file,
            "max_bytes": max_bytes,
            "is_fixed_size": is_fixed_size,
            "supports_length_growth": supports_length_growth,
            "linebreak_mode": linebreak_mode,
            "speaker": speaker,
            "context_preview": context_preview,
            "translation": translation,
            "translation_status": translation_status,
            "ui_risk": ui_risk,
            "pack_risk": pack_risk,
            "opcode_id": opcode_id,
            "display_role": display_role,
            "payload_text_start": payload_text_start,
            "payload_text_end": payload_text_end,
            "prefix_bytes_hex": prefix_bytes_hex,
            "suffix_bytes_hex": suffix_bytes_hex,
            "terminator_policy": terminator_policy,
            "command_length_field_value": command_length_field_value,
            "length_owner_fields": length_owner_fields or [],
            "repack_strategy": repack_strategy,
            "buffer_risk": buffer_risk,
            "notes": notes or [],
        }

    def note_command(self, opcode_id: int, command_category: str, classifier: str, display_role: str | None) -> None:
        stats = self._opcode_stats[opcode_id]
        stats["opcode_id"] = opcode_id
        stats["command_count"] += 1
        stats["categories"].add(command_category)
        stats["classifiers"].add(classifier)
        if display_role:
            stats["display_roles"].add(display_role)

    def _record_opcode_text(self, opcode_id: int | None, bucket: str, text: str) -> None:
        if opcode_id is None:
            return
        stats = self._opcode_stats[opcode_id]
        stats["opcode_id"] = opcode_id
        key_map = {
            "display": ("display_segment_count", "sample_display_texts"),
            "resource": ("resource_segment_count", "sample_resource_refs"),
            "rejected": ("rejected_segment_count", "sample_rejected_texts"),
        }
        count_key, sample_key = key_map[bucket]
        stats[count_key] += 1
        if text and len(stats[sample_key]) < 8 and text not in stats[sample_key]:
            stats[sample_key].append(text)

    def add_display(
        self,
        *,
        text_id: str,
        source_file: str,
        container_type: str,
        container_id: str,
        field_path: str,
        original_text: str,
        original_bytes_hex: str,
        source_offset_in_file: int,
        max_bytes: int | None,
        is_fixed_size: bool,
        supports_length_growth: bool,
        linebreak_mode: str,
        speaker: str | None,
        context_preview: str,
        ui_risk: str,
        pack_risk: str,
        opcode_id: int | None = None,
        display_role: str | None = None,
        payload_text_start: int | None = None,
        payload_text_end: int | None = None,
        prefix_bytes_hex: str | None = None,
        suffix_bytes_hex: str | None = None,
        terminator_policy: str | None = None,
        command_length_field_value: int | None = None,
        length_owner_fields: list[str] | None = None,
        repack_strategy: str | None = None,
        buffer_risk: str | None = None,
        notes: list[str] | None = None,
    ) -> None:
        if not original_text:
            return
        context_preview = f"{source_file}:{container_id}:{field_path}"
        row = self._make_row(
            text_id=text_id,
            source_file=source_file,
            text_category="display_text",
            container_type=container_type,
            container_id=container_id,
            field_path=field_path,
            original_text=original_text,
            original_bytes_hex=original_bytes_hex,
            source_offset_in_file=source_offset_in_file,
            max_bytes=max_bytes,
            is_fixed_size=is_fixed_size,
            supports_length_growth=supports_length_growth,
            linebreak_mode=linebreak_mode,
            speaker=speaker,
            context_preview=context_preview,
            ui_risk=ui_risk,
            pack_risk=pack_risk,
            opcode_id=opcode_id,
            display_role=display_role,
            payload_text_start=payload_text_start,
            payload_text_end=payload_text_end,
            prefix_bytes_hex=prefix_bytes_hex,
            suffix_bytes_hex=suffix_bytes_hex,
            terminator_policy=terminator_policy,
            command_length_field_value=command_length_field_value,
            length_owner_fields=length_owner_fields,
            repack_strategy=repack_strategy,
            buffer_risk=buffer_risk,
            notes=notes,
        )
        self.display_rows.append(row)
        self.catalog_rows[self._catalog_name(source_file)].append(row)
        self._record_opcode_text(opcode_id, "display", original_text)

    def add_resource(
        self,
        *,
        text_id: str,
        source_file: str,
        container_type: str,
        container_id: str,
        field_path: str,
        original_text: str,
        original_bytes_hex: str,
        source_offset_in_file: int,
        opcode_id: int | None = None,
        display_role: str | None = None,
        payload_text_start: int | None = None,
        payload_text_end: int | None = None,
        prefix_bytes_hex: str | None = None,
        suffix_bytes_hex: str | None = None,
        terminator_policy: str | None = None,
        command_length_field_value: int | None = None,
        notes: list[str] | None = None,
    ) -> None:
        if not original_text:
            return
        context_preview = f"{source_file}:{container_id}:{field_path}"
        row = self._make_row(
            text_id=text_id,
            source_file=source_file,
            text_category="resource_ref",
            container_type=container_type,
            container_id=container_id,
            field_path=field_path,
            original_text=original_text,
            original_bytes_hex=original_bytes_hex,
            source_offset_in_file=source_offset_in_file,
            max_bytes=None,
            is_fixed_size=False,
            supports_length_growth=False,
            linebreak_mode="none",
            speaker=None,
            context_preview=context_preview,
            ui_risk="low",
            pack_risk="low",
            opcode_id=opcode_id,
            display_role=display_role,
            payload_text_start=payload_text_start,
            payload_text_end=payload_text_end,
            prefix_bytes_hex=prefix_bytes_hex,
            suffix_bytes_hex=suffix_bytes_hex,
            terminator_policy=terminator_policy,
            command_length_field_value=command_length_field_value,
            length_owner_fields=[],
            repack_strategy="preserve_resource_reference",
            buffer_risk="low",
            notes=notes,
        )
        self.resource_rows.append(row)
        self._record_opcode_text(opcode_id, "resource", original_text)

    def add_rejected(
        self,
        *,
        text_id: str,
        source_file: str,
        container_type: str,
        container_id: str,
        field_path: str,
        original_text: str,
        original_bytes_hex: str,
        source_offset_in_file: int,
        opcode_id: int | None = None,
        display_role: str | None = None,
        payload_text_start: int | None = None,
        payload_text_end: int | None = None,
        prefix_bytes_hex: str | None = None,
        suffix_bytes_hex: str | None = None,
        terminator_policy: str | None = None,
        command_length_field_value: int | None = None,
        notes: list[str] | None = None,
    ) -> None:
        if not original_text:
            return
        context_preview = f"{source_file}:{container_id}:{field_path}"
        row = self._make_row(
            text_id=text_id,
            source_file=source_file,
            text_category="rejected_candidate",
            container_type=container_type,
            container_id=container_id,
            field_path=field_path,
            original_text=original_text,
            original_bytes_hex=original_bytes_hex,
            source_offset_in_file=source_offset_in_file,
            max_bytes=None,
            is_fixed_size=False,
            supports_length_growth=False,
            linebreak_mode="none",
            speaker=None,
            context_preview=context_preview,
            ui_risk="n/a",
            pack_risk="n/a",
            opcode_id=opcode_id,
            display_role=display_role,
            payload_text_start=payload_text_start,
            payload_text_end=payload_text_end,
            prefix_bytes_hex=prefix_bytes_hex,
            suffix_bytes_hex=suffix_bytes_hex,
            terminator_policy=terminator_policy,
            command_length_field_value=command_length_field_value,
            length_owner_fields=[],
            repack_strategy="do_not_translate_without_more_reverse_engineering",
            buffer_risk="unknown",
            notes=notes,
        )
        self.rejected_rows.append(row)
        self._record_opcode_text(opcode_id, "rejected", original_text)

    def build_catalogs(self) -> dict[str, list[dict[str, Any]]]:
        return {name: rows for name, rows in self.catalog_rows.items()}

    def build_opcode_stats(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for opcode_id, stats in self._opcode_stats.items():
            total_segment_count = stats["display_segment_count"] + stats["resource_segment_count"] + stats["rejected_segment_count"]
            rows.append(
                {
                    "opcode_id": opcode_id,
                    "command_count": stats["command_count"],
                    "categories": sorted(stats["categories"]),
                    "classifiers": sorted(stats["classifiers"]),
                    "display_roles": sorted(stats["display_roles"]),
                    "display_segment_count": stats["display_segment_count"],
                    "resource_segment_count": stats["resource_segment_count"],
                    "rejected_segment_count": stats["rejected_segment_count"],
                    "display_segment_ratio": 0.0 if total_segment_count == 0 else round(stats["display_segment_count"] / total_segment_count, 4),
                    "noise_segment_ratio": 0.0 if total_segment_count == 0 else round(stats["rejected_segment_count"] / total_segment_count, 4),
                    "sample_display_texts": list(stats["sample_display_texts"]),
                    "sample_resource_refs": list(stats["sample_resource_refs"]),
                    "sample_rejected_texts": list(stats["sample_rejected_texts"]),
                }
            )
        rows.sort(key=lambda item: (-item["command_count"], item["opcode_id"]))
        return rows

    def build_report(self) -> dict[str, Any]:
        duplicates: dict[str, list[str]] = defaultdict(list)
        for row in self.display_rows:
            duplicates[row["original_text"]].append(row["text_id"])
        duplicate_entries = {key: value for key, value in duplicates.items() if key and len(value) > 1}
        opcode_rows = self.build_opcode_stats()
        return {
            "text_entry_count": len(self.display_rows),
            "display_text_count": len(self.display_rows),
            "resource_ref_count": len(self.resource_rows),
            "rejected_segment_count": len(self.rejected_rows),
            "unique_original_text_count": len({row["original_text"] for row in self.display_rows}),
            "duplicate_original_text_count": len(duplicate_entries),
            "duplicate_original_text_samples": dict(list(duplicate_entries.items())[:50]),
            "multiline_count": sum(1 for row in self.display_rows if row["linebreak_mode"] != "none"),
            "reference_match_count": sum(1 for row in self.display_rows if row["translation_status"] == "reference"),
            "catalog_file_count": len(self.catalog_rows),
            "confirmed_display_opcodes": sorted({row["opcode_id"] for row in opcode_rows if row["display_segment_count"] > 0}),
            "confirmed_resource_opcodes": sorted({row["opcode_id"] for row in opcode_rows if row["resource_segment_count"] > 0}),
            "opcode_noise_summary": [
                {
                    "opcode_id": row["opcode_id"],
                    "command_count": row["command_count"],
                    "display_segment_count": row["display_segment_count"],
                    "resource_segment_count": row["resource_segment_count"],
                    "rejected_segment_count": row["rejected_segment_count"],
                    "noise_segment_ratio": row["noise_segment_ratio"],
                }
                for row in opcode_rows
                if row["rejected_segment_count"] > 0
            ][:20],
        }


def _estimate_pack_risk(*, max_bytes: int | None, is_fixed_size: bool, supports_length_growth: bool) -> str:
    if supports_length_growth and not is_fixed_size:
        return "medium"
    if max_bytes is None:
        return "medium"
    if is_fixed_size and max_bytes <= 20:
        return "high"
    if is_fixed_size:
        return "medium"
    return "low"


SMAP_DISPLAY_LENGTH_OWNER_FIELDS = [
    "command.length",
    "event.command_bytes_length",
    "event.declared_length",
    "event.padded_length",
    "event.chunk_chain",
]


def _collect_fixed_texts(
    source_file: str,
    container_type: str,
    text_entries: list[dict[str, Any]],
    builder: TextCatalogBuilder,
) -> None:
    for entry in text_entries:
        text_object = entry["text_object"]
        field_name = text_object["text_id"].rsplit(":", 1)[-1]
        row_kwargs = {
            "text_id": text_object["text_id"],
            "source_file": source_file,
            "container_type": container_type,
            "container_id": entry["record_id"],
            "field_path": field_name,
            "original_text": text_object["text"],
            "original_bytes_hex": text_object["actual_text_bytes_hex"],
            "source_offset_in_file": entry["source_offset_in_file"],
            "max_bytes": text_object["max_bytes"],
            "is_fixed_size": True,
            "supports_length_growth": False,
            "linebreak_mode": text_object["linebreak_mode"],
            "speaker": None,
            "context_preview": f"{source_file}:{entry['record_id']}:{field_name}",
            "ui_risk": text_object["ui_risk"],
            "pack_risk": _estimate_pack_risk(max_bytes=text_object["max_bytes"], is_fixed_size=True, supports_length_growth=False),
            "length_owner_fields": ["fixed_field.max_bytes"],
            "repack_strategy": "fixed_slot_overwrite_no_growth",
            "buffer_risk": "low" if text_object["max_bytes"] and text_object["max_bytes"] >= 20 else "medium",
            "notes": text_object["notes"],
        }
        if looks_like_resource_reference(text_object["text"]):
            builder.add_resource(**{key: row_kwargs[key] for key in ["text_id", "source_file", "container_type", "container_id", "field_path", "original_text", "original_bytes_hex", "source_offset_in_file", "notes"]})
        else:
            builder.add_display(**row_kwargs)


def parse_game_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    ints = list(struct.unpack(f"<{len(data) // 4}i", data))
    return {
        "file_type": "GAME",
        "source_file": path.name,
        "record_size": len(data),
        "record_count": 1,
        "encoding": SOURCE_ENCODING,
        "records": [
            {
                "record_id": "game:000",
                "record_index": 0,
                "offset": 0,
                "size": len(data),
                "fields": {
                    "start_map_id": ints[0],
                    "bg_type": ints[1],
                    "counter_atk_enemy": ints[2],
                    "counter_atk_ally": ints[3],
                    "animation_speed": ints[4],
                    "revive_type": ints[5],
                    "reserved_ints": ints[6:],
                },
                "unknown_bytes_hex": "",
                "raw_record_sha256": sha256_hex(data),
                "raw_record_hex": bytes_to_hex(data),
            }
            ],
        }


def _build_dialogue_catalogs(all_event_exports: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    catalogs: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event_export in all_event_exports:
        blocks = iter_dialogue_blocks(
            map_id=event_export["map_id"],
            source_file=event_export["source_file"],
            event_id=event_export["event_id"],
            event_name_internal=event_export["event_name_internal"],
            commands=event_export["commands"],
        )
        for block in blocks:
            catalogs[dialogue_catalog_name(block.source_file)].append(build_dialogue_catalog_row(block))
    return dict(catalogs)


def parse_test_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    ints = list(struct.unpack(f"<{len(data) // 4}i", data))
    return {
        "file_type": "TEST",
        "source_file": path.name,
        "record_size": len(data),
        "record_count": 1,
        "encoding": SOURCE_ENCODING,
        "records": [
            {
                "record_id": "test:000",
                "record_index": 0,
                "offset": 0,
                "size": len(data),
                "fields": {"values": ints},
                "unknown_bytes_hex": "",
                "raw_record_sha256": sha256_hex(data),
                "raw_record_hex": bytes_to_hex(data),
            }
        ],
    }


def parse_editor_file(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "file_type": "EDITOR",
        "source_file": path.name,
        "record_size": len(data),
        "record_count": 1,
        "encoding": SOURCE_ENCODING,
        "records": [
            {
                "record_id": "editor:000",
                "record_index": 0,
                "offset": 0,
                "size": len(data),
                "fields": {"value": struct.unpack("<i", data)[0]},
                "unknown_bytes_hex": "",
                "raw_record_sha256": sha256_hex(data),
                "raw_record_hex": bytes_to_hex(data),
            }
        ],
    }


def pack_special_file(export_data: dict[str, Any]) -> bytes:
    return hex_to_bytes(export_data["records"][0]["raw_record_hex"])


def parse_mapc_file(path: Path, source_file: str | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    width_assumed = 24
    height_assumed = len(data) // width_assumed
    return {
        "file_type": "MAPC",
        "mapc_id": path.stem.split("_")[-1],
        "source_file": source_file or str(path.as_posix()),
        "tile_count": len(data),
        "width_assumed": width_assumed,
        "height_assumed": height_assumed,
        "tiles": list(data),
        "tile_rows_preview": [list(data[row * width_assumed : (row + 1) * width_assumed]) for row in range(height_assumed)],
        "raw_sha256": sha256_hex(data),
    }


def pack_mapc_file(export_data: dict[str, Any]) -> bytes:
    return bytes(export_data["tiles"])


def _text_field(
    *,
    text_id: str,
    role: str,
    field_bytes: bytes,
    max_bytes: int,
    source_offset_in_file: int,
) -> dict[str, Any]:
    text, text_bytes, null_terminated = read_null_terminated_text(field_bytes, SOURCE_ENCODING)
    text_object = build_text_object(
        text_id=text_id,
        role=role,
        field_bytes=field_bytes,
        text_bytes=text_bytes,
        text=text,
        max_bytes=max_bytes - 1 if null_terminated else max_bytes,
        is_fixed_size=True,
        null_terminated=null_terminated,
        padding_byte=0,
        supports_length_growth=False,
        notes=[],
    )
    text_object["source_offset_in_file"] = source_offset_in_file
    return text_object


def _prefix_unused_counts(ev_chunks: list[int]) -> list[int]:
    counts = [0] * (len(ev_chunks) + 1)
    for index, value in enumerate(ev_chunks):
        counts[index + 1] = counts[index] + (1 if value == -1 else 0)
    return counts


def _read_chunked_event(
    data: bytes,
    ev_chunks: list[int],
    unused_counts: list[int],
    first_chunk: int,
    declared_length: int,
) -> tuple[bytes, list[dict[str, Any]]]:
    current = first_chunk
    payload = bytearray()
    chain: list[dict[str, Any]] = []
    guard = 0
    while current != -2:
        if current < 0 or current >= len(ev_chunks):
            raise ValueError(f"Invalid chunk index {current}")
        file_offset = 120800 + (current - unused_counts[current]) * 100
        payload.extend(data[file_offset : file_offset + 100])
        chain.append({"chunk_index": current, "next_chunk": ev_chunks[current], "file_offset": file_offset})
        current = ev_chunks[current]
        guard += 1
        if guard > 5000:
            raise ValueError("Chunk traversal exceeded guard limit")
    return bytes(payload[:declared_length]), chain


def _event_byte_to_file_offset(chain: list[dict[str, Any]], event_offset: int) -> int:
    chunk_index = event_offset // 100
    inner_offset = event_offset % 100
    return chain[chunk_index]["file_offset"] + inner_offset


def _parse_event_commands(
    *,
    map_id: str,
    source_file: str,
    event_name_internal: str,
    event_bytes: bytes,
    chain: list[dict[str, Any]],
    text_builder: TextCatalogBuilder,
) -> tuple[list[dict[str, Any]], int]:
    commands: list[dict[str, Any]] = []
    if len(event_bytes) < 104:
        return commands, 0
    command_bytes_length = struct.unpack_from("<I", event_bytes, 100)[0]
    cursor = 104
    end_offset = min(len(event_bytes), 104 + command_bytes_length)
    current_speaker: str | None = None
    command_index = 0
    while cursor + 4 <= end_offset:
        command_id = event_bytes[cursor]
        length = struct.unpack_from("<H", event_bytes, cursor + 1)[0]
        unk_byte = event_bytes[cursor + 3]
        payload_start = cursor + 4
        payload_end = payload_start + length
        if payload_end > len(event_bytes) or payload_end > end_offset:
            break
        payload = event_bytes[payload_start:payload_end]
        extraction = extract_command_texts(command_id, payload, length)
        text_builder.note_command(command_id, extraction.command_category, extraction.classifier, extraction.display_role)

        display_segments: list[dict[str, Any]] = []
        resource_segments: list[dict[str, Any]] = []
        rejected_segments: list[dict[str, Any]] = []
        command_container_id = f"SMAP_{map_id}:{event_name_internal}:{command_index:06d}"

        for segment in extraction.display_segments:
            text_id = f"smap:{map_id}:event:{event_name_internal}:cmd:{command_index:06d}:seg:{segment['segment_index']:02d}"
            absolute_event_offset = payload_start + segment["payload_text_start"]
            source_offset_in_file = _event_byte_to_file_offset(chain, absolute_event_offset)
            segment_export = {
                **segment,
                "text_id": text_id,
                "classifier": extraction.classifier,
                "source_offset_in_file": source_offset_in_file,
                "segment_category": "display",
            }
            display_segments.append(segment_export)
            speaker = current_speaker if extraction.display_role == "dialogue" else None
            text_builder.add_display(
                text_id=text_id,
                source_file=source_file,
                container_type="smap_command_text",
                container_id=command_container_id,
                field_path=f"commands[{command_index}].display_segments[{segment['segment_index']}]",
                original_text=segment["text"],
                original_bytes_hex=segment["source_bytes_hex"],
                source_offset_in_file=source_offset_in_file,
                max_bytes=None,
                is_fixed_size=False,
                supports_length_growth=True,
                linebreak_mode=segment["linebreak_mode"],
                speaker=speaker,
                context_preview=f"SMAP_{map_id}:{event_name_internal}:{extraction.classifier}",
                ui_risk=segment["length_profile"]["estimated_translation_risk"],
                pack_risk="high",
                opcode_id=command_id,
                display_role=segment["display_role"],
                payload_text_start=segment["payload_text_start"],
                payload_text_end=segment["payload_text_end"],
                prefix_bytes_hex=segment["prefix_bytes_hex"],
                suffix_bytes_hex=segment["suffix_bytes_hex"],
                terminator_policy=segment["terminator_policy"],
                command_length_field_value=segment["command_length_field_value"],
                length_owner_fields=list(SMAP_DISPLAY_LENGTH_OWNER_FIELDS),
                repack_strategy="rewrite_payload_and_recalculate_command_and_event_lengths",
                buffer_risk="high",
                notes=[
                    f"command_id={command_id}",
                    f"command_category={extraction.command_category}",
                    f"classifier={extraction.classifier}",
                    f"event_name_internal={event_name_internal}",
                ],
            )
        if extraction.display_role == "speaker" and display_segments:
            current_speaker = display_segments[0]["text"]

        for segment in extraction.resource_segments:
            text_id = f"smap:{map_id}:event:{event_name_internal}:cmd:{command_index:06d}:resource:{segment['segment_index']:02d}"
            absolute_event_offset = payload_start + segment["payload_text_start"]
            source_offset_in_file = _event_byte_to_file_offset(chain, absolute_event_offset)
            segment_export = {
                **segment,
                "text_id": text_id,
                "classifier": extraction.classifier,
                "source_offset_in_file": source_offset_in_file,
                "segment_category": "resource",
            }
            resource_segments.append(segment_export)
            text_builder.add_resource(
                text_id=text_id,
                source_file=source_file,
                container_type="smap_command_resource",
                container_id=command_container_id,
                field_path=f"commands[{command_index}].resource_segments[{segment['segment_index']}]",
                original_text=segment["text"],
                original_bytes_hex=segment["source_bytes_hex"],
                source_offset_in_file=source_offset_in_file,
                opcode_id=command_id,
                display_role=segment["display_role"],
                payload_text_start=segment["payload_text_start"],
                payload_text_end=segment["payload_text_end"],
                prefix_bytes_hex=segment["prefix_bytes_hex"],
                suffix_bytes_hex=segment["suffix_bytes_hex"],
                terminator_policy=segment["terminator_policy"],
                command_length_field_value=segment["command_length_field_value"],
                notes=[
                    f"command_id={command_id}",
                    f"command_category={extraction.command_category}",
                    f"classifier={extraction.classifier}",
                ],
            )

        for segment in extraction.rejected_segments:
            text_id = f"smap:{map_id}:event:{event_name_internal}:cmd:{command_index:06d}:rejected:{segment['segment_index']:02d}"
            absolute_event_offset = payload_start + segment["payload_text_start"]
            source_offset_in_file = _event_byte_to_file_offset(chain, absolute_event_offset)
            segment_export = {
                **segment,
                "text_id": text_id,
                "classifier": extraction.classifier,
                "source_offset_in_file": source_offset_in_file,
                "segment_category": "rejected",
            }
            rejected_segments.append(segment_export)
            text_builder.add_rejected(
                text_id=text_id,
                source_file=source_file,
                container_type="smap_command_candidate",
                container_id=command_container_id,
                field_path=f"commands[{command_index}].rejected_segments[{segment['segment_index']}]",
                original_text=segment["text"],
                original_bytes_hex=segment["source_bytes_hex"],
                source_offset_in_file=source_offset_in_file,
                opcode_id=command_id,
                display_role=segment["display_role"],
                payload_text_start=segment["payload_text_start"],
                payload_text_end=segment["payload_text_end"],
                prefix_bytes_hex=segment["prefix_bytes_hex"],
                suffix_bytes_hex=segment["suffix_bytes_hex"],
                terminator_policy=segment["terminator_policy"],
                command_length_field_value=segment["command_length_field_value"],
                notes=[
                    f"command_id={command_id}",
                    f"command_category={extraction.command_category}",
                    f"classifier={extraction.classifier}",
                ],
            )

        command_file_offset = _event_byte_to_file_offset(chain, cursor)
        all_segments = sorted(
            [*display_segments, *resource_segments, *rejected_segments],
            key=lambda item: (item["payload_text_start"], item["payload_text_end"], item["segment_index"]),
        )
        commands.append(
            {
                "command_id": command_id,
                "command_index": command_index,
                "offset_in_event": cursor,
                "source_offset_in_file": command_file_offset,
                "length": length,
                "command_total_length": 4 + length,
                "payload_start_in_event": payload_start,
                "payload_end_in_event": payload_end,
                "unk_byte": unk_byte,
                "payload_hex": bytes_to_hex(payload),
                "command_category": extraction.command_category,
                "classifier": extraction.classifier,
                "display_role": extraction.display_role,
                "command_length_field_value": length,
                "supports_length_growth": bool(display_segments),
                "length_owner_fields": list(SMAP_DISPLAY_LENGTH_OWNER_FIELDS) if display_segments else [],
                "repack_strategy": "rewrite_payload_and_recalculate_command_and_event_lengths" if display_segments else "preserve_payload_hex",
                "buffer_risk": "high" if display_segments else "low",
                "text_segments": all_segments,
                "display_segments": display_segments,
                "resource_segments": resource_segments,
                "rejected_segments": rejected_segments,
            }
        )
        cursor = payload_end
        command_index += 1
    return commands, command_bytes_length


def _movement_pattern_dict(obj_bytes: bytes, offset: int) -> dict[str, Any]:
    return {
        "condition": struct.unpack_from("<h", obj_bytes, offset)[0],
        "movement_type": struct.unpack_from("<h", obj_bytes, offset + 2)[0],
        "location_x": struct.unpack_from("<h", obj_bytes, offset + 4)[0],
        "location_y": struct.unpack_from("<h", obj_bytes, offset + 6)[0],
        "switch_to_apply": struct.unpack_from("<h", obj_bytes, offset + 8)[0],
    }


def _obj_event_condition_dict(obj_bytes: bytes, offset: int) -> dict[str, Any]:
    return {
        "type": struct.unpack_from("<h", obj_bytes, offset)[0],
        "condition": struct.unpack_from("<h", obj_bytes, offset + 2)[0],
        "turn": struct.unpack_from("<h", obj_bytes, offset + 4)[0],
        "unit_id": struct.unpack_from("<h", obj_bytes, offset + 6)[0],
    }


def _parse_object(obj_bytes: bytes, object_index: int, base_offset: int) -> dict[str, Any]:
    return {
        "object_index": object_index,
        "offset": base_offset,
        "raw_object_sha256": sha256_hex(obj_bytes),
        "raw_object_hex": bytes_to_hex(obj_bytes),
        "is_active": struct.unpack_from("<i", obj_bytes, 0)[0],
        "pos_x": struct.unpack_from("<i", obj_bytes, 4)[0],
        "pos_y": struct.unpack_from("<i", obj_bytes, 8)[0],
        "type": struct.unpack_from("<i", obj_bytes, 12)[0],
        "switch_condition": struct.unpack_from("<h", obj_bytes, 16)[0],
        "unk1_0_hex": bytes_to_hex(obj_bytes[18:20]),
        "unit_id": struct.unpack_from("<i", obj_bytes, 20)[0],
        "other_tile": struct.unpack_from("<i", obj_bytes, 24)[0],
        "movement_pattern_A": _movement_pattern_dict(obj_bytes, 28),
        "movement_pattern_B": _movement_pattern_dict(obj_bytes, 38),
        "movement_pattern_C": _movement_pattern_dict(obj_bytes, 48),
        "movement_pattern_D": _movement_pattern_dict(obj_bytes, 58),
        "enemy_boss": struct.unpack_from("<i", obj_bytes, 68)[0],
        "player_guest": struct.unpack_from("<i", obj_bytes, 72)[0],
        "enemy_item": struct.unpack_from("<i", obj_bytes, 76)[0],
        "enemy_gold": struct.unpack_from("<i", obj_bytes, 80)[0],
        "condition_1": _obj_event_condition_dict(obj_bytes, 84),
        "condition_2": _obj_event_condition_dict(obj_bytes, 92),
        "condition_3": _obj_event_condition_dict(obj_bytes, 100),
        "condition_4": _obj_event_condition_dict(obj_bytes, 108),
        "condition_5": _obj_event_condition_dict(obj_bytes, 116),
        "unk2_hex": bytes_to_hex(obj_bytes[124:160]),
        "neutral_healer": struct.unpack_from("<i", obj_bytes, 160)[0],
    }


def parse_smap_file(
    path: Path,
    source_file: str,
    text_builder: TextCatalogBuilder,
) -> tuple[dict[str, Any], list[tuple[Path, dict[str, Any]]]]:
    data = path.read_bytes()
    map_id = path.stem.split("_")[-1]
    offsets = {
        "name": [0, 32],
        "map_meta": [32, 48],
        "music": [48, 432],
        "camp_shop": [432, 840],
        "prev_character": [840, 1004],
        "important_characters": [1004, 1088],
        "camp_misc": [1088, 1188],
        "map_data": [1188, 4388],
        "unk4_0": [4388, 4400],
        "objects": [4400, 20800],
        "ev_chunks": [20800, 40800],
        "event_declarations": [40800, 120800],
        "event_region": [120800, len(data)],
    }
    name_field = _text_field(
        text_id=f"smap:{map_id}:map_name",
        role="map_name",
        field_bytes=data[0:20],
        max_bytes=20,
        source_offset_in_file=0,
    )
    text_builder.add_display(
        text_id=name_field["text_id"],
        source_file=source_file,
        container_type="smap",
        container_id=f"SMAP_{map_id}",
        field_path="name",
        original_text=name_field["text"],
        original_bytes_hex=name_field["actual_text_bytes_hex"],
        source_offset_in_file=0,
        max_bytes=name_field["max_bytes"],
        is_fixed_size=True,
        supports_length_growth=False,
        linebreak_mode=name_field["linebreak_mode"],
        speaker=None,
        context_preview=f"SMAP_{map_id}:name",
        ui_risk=name_field["ui_risk"],
        pack_risk=_estimate_pack_risk(max_bytes=name_field["max_bytes"], is_fixed_size=True, supports_length_growth=False),
        length_owner_fields=["fixed_field.max_bytes"],
        repack_strategy="fixed_slot_overwrite_no_growth",
        buffer_risk="low",
        notes=[],
    )
    width, height, tileset, win_condition = struct.unpack_from("<4i", data, 32)
    music_offsets = (
        ("music_camp", 48),
        ("music_player_turn", 96),
        ("music_enemy_turn", 144),
        ("music_player_battle", 192),
        ("music_enemy_battle", 240),
        ("music_boss_battle", 288),
        ("music_map_complete", 336),
        ("music_game_over", 384),
    )
    music: dict[str, Any] = {}
    for key, offset in music_offsets:
        text_object = _text_field(
            text_id=f"smap:{map_id}:{key}",
            role=key,
            field_bytes=data[offset : offset + 48],
            max_bytes=48,
            source_offset_in_file=offset,
        )
        music[key] = text_object
        if text_object["text"]:
            text_builder.add_resource(
                text_id=text_object["text_id"],
                source_file=source_file,
                container_type="smap_music",
                container_id=f"SMAP_{map_id}",
                field_path=key,
                original_text=text_object["text"],
                original_bytes_hex=text_object["actual_text_bytes_hex"],
                source_offset_in_file=offset,
                notes=[f"fixed_music_field={key}"],
            )
    camp_before_battle = struct.unpack_from("<i", data, 432)[0]
    camp_shop_items_all = list(struct.unpack_from("<100i", data, 436))
    count_shop_items = struct.unpack_from("<i", data, 836)[0]
    count_prev_character = struct.unpack_from("<i", data, 840)[0]
    prev_character_x = list(struct.unpack_from("<20i", data, 844))
    prev_character_y = list(struct.unpack_from("<20i", data, 924))
    important_characters_all = list(struct.unpack_from("<15i", data, 1004))
    unk1_0_hex = bytes_to_hex(data[1064:1084])
    count_important_characters = struct.unpack_from("<i", data, 1084)[0]
    camp_background = struct.unpack_from("<i", data, 1088)[0]
    unk_int = struct.unpack_from("<i", data, 1092)[0]
    unk2_0_hex = bytes_to_hex(data[1096:1104])
    camp_allow_party_change = struct.unpack_from("<i", data, 1104)[0]
    unk3_0_hex = bytes_to_hex(data[1108:1188])
    map_data_linear = list(struct.unpack_from("<1600h", data, 1188))
    map_rows_preview = [map_data_linear[row * 40 : row * 40 + width] for row in range(height)]
    unk4_0_hex = bytes_to_hex(data[4388:4400])
    objects: list[dict[str, Any]] = []
    for object_index in range(100):
        start = 4400 + object_index * 164
        objects.append(_parse_object(data[start : start + 164], object_index, start))
    ev_chunks = list(struct.unpack_from("<5000i", data, 20800))
    unused_counts = _prefix_unused_counts(ev_chunks)
    event_declarations: list[dict[str, Any]] = []
    event_exports: list[tuple[Path, dict[str, Any]]] = []
    for declaration_index in range(5000):
        declaration_offset = 40800 + declaration_index * 16
        internal_name_raw = data[declaration_offset : declaration_offset + 8]
        internal_name = internal_name_raw.split(b"\x00", 1)[0]
        if not internal_name:
            continue
        first_chunk, declared_length = struct.unpack_from("<ii", data, declaration_offset + 8)
        event_bytes, chain = _read_chunked_event(data, ev_chunks, unused_counts, first_chunk, declared_length)
        event_name_text, event_name_bytes, event_name_null_terminated = read_null_terminated_text(event_bytes[0:36], SOURCE_ENCODING)
        event_label = build_text_object(
            text_id=f"smap:{map_id}:event:{internal_name.decode('ascii', 'replace')}:label",
            role="event_label",
            field_bytes=event_bytes[0:36],
            text_bytes=event_name_bytes,
            text=event_name_text,
            max_bytes=35 if event_name_null_terminated else 36,
            is_fixed_size=True,
            null_terminated=event_name_null_terminated,
            padding_byte=0,
            supports_length_growth=False,
            notes=[],
        )
        commands, command_bytes_length = _parse_event_commands(
            map_id=map_id,
            source_file=source_file,
            event_name_internal=internal_name.decode("ascii", "replace"),
            event_bytes=event_bytes,
            chain=chain,
            text_builder=text_builder,
        )
        declaration = {
            "declaration_index": declaration_index,
            "offset": declaration_offset,
            "internal_name": internal_name.decode("ascii", "replace"),
            "internal_name_bytes_hex": bytes_to_hex(internal_name_raw),
            "first_chunk": first_chunk,
            "declared_length": declared_length,
            "padded_length": len(chain) * 100,
            "chunk_chain": chain,
        }
        event_declarations.append(declaration)
        event_export = {
            "file_type": "SMAP_EVENT",
            "map_id": map_id,
            "source_file": source_file,
            "event_id": f"smap:{map_id}:event:{declaration['internal_name']}",
            "event_name_internal": declaration["internal_name"],
            "declaration_index": declaration_index,
            "declaration_offset": declaration_offset,
            "first_chunk": first_chunk,
            "declared_length": declared_length,
            "padded_length": len(chain) * 100,
            "command_bytes_length": command_bytes_length,
            "chunk_chain": chain,
            "command_region_offset": 104,
            "event_label": event_label,
            "event_label_source_offset_in_file": _event_byte_to_file_offset(chain, 0),
            "event_zero_padding_hex": bytes_to_hex(event_bytes[36:100]),
            "raw_event_sha256": sha256_hex(event_bytes),
            "raw_event_bytes_hex": bytes_to_hex(event_bytes),
            "length_owner_fields": list(SMAP_DISPLAY_LENGTH_OWNER_FIELDS),
            "repack_strategy": "rewrite_payload_and_recalculate_command_and_event_lengths",
            "length_hierarchy": {
                "command_length_field_name": "commands[].length",
                "event_command_bytes_length": command_bytes_length,
                "declared_length": declared_length,
                "padded_length": len(chain) * 100,
                "chunk_chain_length": len(chain),
            },
            "commands": commands,
        }
        event_path = Path(f"maps/SMAP_{map_id}/events/{declaration_index:04d}_{sanitize_name(declaration['internal_name'])}.json")
        event_exports.append((event_path, event_export))
    smap_export = {
        "file_type": "SMAP",
        "map_id": map_id,
        "source_file": source_file,
        "size": len(data),
        "encoding": SOURCE_ENCODING,
        "offsets": offsets,
        "static_sha256": sha256_hex(data[:120800]),
        "event_region_sha256": sha256_hex(data[120800:]),
        "name": name_field,
        "zero_padding_0_hex": bytes_to_hex(data[20:32]),
        "width": width,
        "height": height,
        "tileset": tileset,
        "win_condition": win_condition,
        "music": music,
        "camp_before_battle": camp_before_battle,
        "camp_shop_items_all": camp_shop_items_all,
        "shop_items_active": camp_shop_items_all[:count_shop_items],
        "count_shop_items": count_shop_items,
        "count_prev_character": count_prev_character,
        "prev_character_start_positions": [{"x": prev_character_x[index], "y": prev_character_y[index]} for index in range(20)],
        "prev_character_active": [{"x": prev_character_x[index], "y": prev_character_y[index]} for index in range(count_prev_character)],
        "important_characters_all": important_characters_all,
        "important_characters_active": important_characters_all[:count_important_characters],
        "unk1_0_hex": unk1_0_hex,
        "count_important_characters": count_important_characters,
        "camp_background": camp_background,
        "unk_int": unk_int,
        "unk2_0_hex": unk2_0_hex,
        "camp_allow_party_change": camp_allow_party_change,
        "unk3_0_hex": unk3_0_hex,
        "map_data_linear": map_data_linear,
        "map_rows_preview": map_rows_preview,
        "unk4_0_hex": unk4_0_hex,
        "objects": objects,
        "ev_chunks": ev_chunks,
        "event_declarations": event_declarations,
        "event_declaration_slots": 5000,
    }
    return smap_export, event_exports


def _pack_text_field(text_object: dict[str, Any], field_size: int) -> bytes:
    return hex_to_bytes(text_object["source_bytes_hex"]).ljust(field_size, b"\x00")[:field_size]


def _pack_object(obj: dict[str, Any]) -> bytes:
    return hex_to_bytes(obj["raw_object_hex"])


def pack_smap_file(smap_export: dict[str, Any], event_exports: list[dict[str, Any]]) -> bytes:
    static = bytearray(120800)
    static[0:20] = _pack_text_field(smap_export["name"], 20)
    static[20:32] = hex_to_bytes(smap_export["zero_padding_0_hex"]).ljust(12, b"\x00")[:12]
    struct.pack_into("<4i", static, 32, smap_export["width"], smap_export["height"], smap_export["tileset"], smap_export["win_condition"])
    music_offsets = (
        ("music_camp", 48),
        ("music_player_turn", 96),
        ("music_enemy_turn", 144),
        ("music_player_battle", 192),
        ("music_enemy_battle", 240),
        ("music_boss_battle", 288),
        ("music_map_complete", 336),
        ("music_game_over", 384),
    )
    for key, offset in music_offsets:
        static[offset : offset + 48] = _pack_text_field(smap_export["music"][key], 48)
    struct.pack_into("<i", static, 432, smap_export["camp_before_battle"])
    struct.pack_into("<100i", static, 436, *smap_export["camp_shop_items_all"])
    struct.pack_into("<i", static, 836, smap_export["count_shop_items"])
    struct.pack_into("<i", static, 840, smap_export["count_prev_character"])
    struct.pack_into("<20i", static, 844, *[item["x"] for item in smap_export["prev_character_start_positions"]])
    struct.pack_into("<20i", static, 924, *[item["y"] for item in smap_export["prev_character_start_positions"]])
    struct.pack_into("<15i", static, 1004, *smap_export["important_characters_all"])
    static[1064:1084] = hex_to_bytes(smap_export["unk1_0_hex"]).ljust(20, b"\x00")[:20]
    struct.pack_into("<i", static, 1084, smap_export["count_important_characters"])
    struct.pack_into("<i", static, 1088, smap_export["camp_background"])
    struct.pack_into("<i", static, 1092, smap_export["unk_int"])
    static[1096:1104] = hex_to_bytes(smap_export["unk2_0_hex"]).ljust(8, b"\x00")[:8]
    struct.pack_into("<i", static, 1104, smap_export["camp_allow_party_change"])
    static[1108:1188] = hex_to_bytes(smap_export["unk3_0_hex"]).ljust(80, b"\x00")[:80]
    struct.pack_into("<1600h", static, 1188, *smap_export["map_data_linear"])
    static[4388:4400] = hex_to_bytes(smap_export["unk4_0_hex"]).ljust(12, b"\x00")[:12]
    for object_index, obj in enumerate(smap_export["objects"]):
        start = 4400 + object_index * 164
        static[start : start + 164] = _pack_object(obj)
    struct.pack_into("<5000i", static, 20800, *smap_export["ev_chunks"])
    for declaration in smap_export["event_declarations"]:
        start = 40800 + declaration["declaration_index"] * 16
        static[start : start + 8] = declaration["internal_name"].encode("ascii").ljust(8, b"\x00")[:8]
        struct.pack_into("<ii", static, start + 8, declaration["first_chunk"], declaration["declared_length"])

    event_map = {event_export["declaration_index"]: event_export for event_export in event_exports}
    chunk_payloads: dict[int, bytes] = {}
    for declaration in smap_export["event_declarations"]:
        event_export = event_map[declaration["declaration_index"]]
        event_bytes = hex_to_bytes(event_export["raw_event_bytes_hex"])
        padded = event_bytes + (b"\x00" * ((100 - (len(event_bytes) % 100)) % 100))
        payload_chunks = [padded[index : index + 100] for index in range(0, len(padded), 100)]
        chain_indices = [item["chunk_index"] for item in declaration["chunk_chain"]]
        if len(payload_chunks) != len(chain_indices):
            raise ValueError(f"Chunk count mismatch for {declaration['internal_name']}")
        for chunk_index, payload_chunk in zip(chain_indices, payload_chunks):
            chunk_payloads[chunk_index] = payload_chunk
    event_region = bytearray()
    for chunk_index, next_chunk in enumerate(smap_export["ev_chunks"]):
        if next_chunk == -1:
            continue
        event_region.extend(chunk_payloads.get(chunk_index, b"\x00" * 100))
    return bytes(static) + bytes(event_region)


def _padded_event_length(length: int) -> int:
    return ((length + 99) // 100) * 100


def _clear_readonly_and_retry(func: Any, path: str, _exc_info: Any) -> None:
    retry_path = Path(path)
    retry_path.chmod(0o666)
    func(path)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)
        return
    path.chmod(0o666)
    path.unlink()


def _build_growth_simulation(event_export: dict[str, Any], command: dict[str, Any], segment: dict[str, Any]) -> dict[str, Any]:
    original_text_bytes = hex_to_bytes(segment["source_bytes_hex"])
    growth_bytes = max(12, max(1, len(original_text_bytes) // 2))
    new_text_byte_length = len(original_text_bytes) + growth_bytes
    new_command_length = command["length"] + growth_bytes
    new_command_bytes_length = event_export["command_bytes_length"] + growth_bytes
    new_declared_length = event_export["declared_length"] + growth_bytes
    new_padded_length = _padded_event_length(new_declared_length)
    original_chunk_count = len(event_export["chunk_chain"])
    new_chunk_count = new_padded_length // 100
    return {
        "event_id": event_export["event_id"],
        "source_file": event_export["source_file"],
        "text_id": segment["text_id"],
        "opcode_id": command["command_id"],
        "display_role": command.get("display_role"),
        "command_index": command["command_index"],
        "source_offset_in_file": segment["source_offset_in_file"],
        "growth_bytes_simulated": growth_bytes,
        "original_text_byte_length": len(original_text_bytes),
        "simulated_new_text_byte_length": new_text_byte_length,
        "original_command_length": command["length"],
        "simulated_command_length": new_command_length,
        "original_command_bytes_length": event_export["command_bytes_length"],
        "simulated_command_bytes_length": new_command_bytes_length,
        "original_declared_length": event_export["declared_length"],
        "simulated_declared_length": new_declared_length,
        "original_padded_length": event_export["padded_length"],
        "simulated_padded_length": new_padded_length,
        "original_chunk_count": original_chunk_count,
        "simulated_chunk_count": new_chunk_count,
        "additional_chunks_required": max(0, new_chunk_count - original_chunk_count),
        "length_owner_fields": list(SMAP_DISPLAY_LENGTH_OWNER_FIELDS),
        "length_owner_update_order": [
            "payload.text_bytes",
            "command.length",
            "event.command_bytes_length",
            "event.declared_length",
            "event.padded_length",
            "event.chunk_chain",
        ],
        "would_truncate": False,
    }


def build_repack_readiness_report(
    *,
    all_event_exports: list[dict[str, Any]],
    opcode_stats: list[dict[str, Any]],
    text_flow_report: dict[str, Any],
) -> dict[str, Any]:
    sample_simulations: list[dict[str, Any]] = []
    per_opcode_samples: dict[int, int] = defaultdict(int)
    for event_export in all_event_exports:
        for command in event_export["commands"]:
            opcode_id = command["command_id"]
            if opcode_id not in DISPLAY_OPCODE_SPECS:
                continue
            for segment in command.get("display_segments", []):
                if per_opcode_samples[opcode_id] >= 3:
                    continue
                sample_simulations.append(_build_growth_simulation(event_export, command, segment))
                per_opcode_samples[opcode_id] += 1
                if sum(per_opcode_samples.values()) >= 9:
                    break
            if sum(per_opcode_samples.values()) >= 9:
                break
        if sum(per_opcode_samples.values()) >= 9:
            break

    opcode_lookup = {row["opcode_id"]: row for row in opcode_stats}
    display_opcode_readiness = []
    for opcode_id, spec in sorted(DISPLAY_OPCODE_SPECS.items()):
        stat = opcode_lookup.get(opcode_id, {})
        display_opcode_readiness.append(
            {
                "opcode_id": opcode_id,
                "display_role": spec["display_role"],
                "command_count": stat.get("command_count", 0),
                "display_segment_count": stat.get("display_segment_count", 0),
                "schema_ready_for_non_truncating_repack": True,
                "length_owner_fields": list(SMAP_DISPLAY_LENGTH_OWNER_FIELDS),
                "repack_strategy": "rewrite_payload_and_recalculate_command_and_event_lengths",
                "binary_risk": "high",
            }
        )

    return {
        "status": "schema_ready_binary_patch_pending",
        "supports_non_truncating_repack_at_schema_level": True,
        "binary_patch_required_for_safe_chinese_display": True,
        "confirmed_display_opcodes": [row["opcode_id"] for row in display_opcode_readiness if row["display_segment_count"] > 0],
        "resource_opcodes": sorted(RESOURCE_OPCODE_SPECS),
        "known_noise_opcodes": sorted(NOISE_OPCODE_SET),
        "display_opcode_readiness": display_opcode_readiness,
        "sample_growth_simulations": sample_simulations,
        "blocking_issues": [
            {
                "id": "ansi_render_path",
                "severity": "high",
                "detail": "SRPGEXEC.EXE/HARMONY.DLL still expose an ANSI DrawTextA/GetACP pipeline, so byte growth in SMAP is not the only blocker for Chinese text.",
            },
            {
                "id": "render_buffer_size_unknown",
                "severity": "high",
                "detail": "Stack/static buffer limits in the EXE/DLL call chain are not yet proven from disassembly; long Chinese lines may still overflow even if event repacking succeeds.",
            },
        ],
        "next_actions": [
            "Patch the packer to rebuild SMAP command bytes, event.declared_length, padded_length, and chunk_chain from edited display segments.",
            "Trace opcode 1/45/201 handlers in SRPGEXEC.EXE to prove where NUL termination and command lengths are consumed.",
            "Trace DrawTextA/CreateFontIndirectA call sites in HARMONY.DLL to confirm final buffer size and codepage assumptions.",
        ],
        "text_flow_dependencies": {
            "binary_findings_present": bool(text_flow_report.get("binary_findings")),
            "engine_inference_count": len(text_flow_report.get("engine_inferences", [])),
        },
    }


def load_reference_map(game_dir: Path, explicit_path: Path | None = None) -> tuple[dict[str, str], str | None]:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(explicit_path)
    candidates.append(Path.cwd() / "reference_map.json")
    candidates.append(game_dir.parent / "reference_map.json")
    for candidate in candidates:
        if candidate.exists():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("reference_map"), dict):
                data = data["reference_map"]
            if isinstance(data, dict):
                return {str(key): str(value) for key, value in data.items()}, str(candidate.resolve())
    return {}, None


def unpack_game(game_dir: Path, out_dir: Path, reference_map_path: Path | None = None) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    out_dir = out_dir.resolve()
    reference_map, resolved_reference_path = load_reference_map(game_dir, reference_map_path)
    builder = TextCatalogBuilder(reference_map)
    for stale_path in (out_dir / "databases", out_dir / "maps", out_dir / "texts", out_dir / "raw", out_dir / "reports"):
        try:
            _remove_path(stale_path)
        except OSError:
            pass
    for name in ("databases", "maps", "texts", "raw", "reports"):
        (out_dir / name).mkdir(parents=True, exist_ok=True)

    supported_files: list[dict[str, Any]] = []
    all_event_exports: list[dict[str, Any]] = []
    for file_name, spec in FIXED_FILE_SPECS.items():
        source_path = game_dir / file_name
        export_data, text_entries = parse_fixed_file(source_path, spec)
        json_dump(out_dir / "databases" / f"{spec.file_type.lower()}.json", export_data)
        _collect_fixed_texts(spec.source_file, spec.prefix, text_entries, builder)
        supported_files.append(
            {
                "source_file": file_name,
                "size": source_path.stat().st_size,
                "sha256": file_sha256(source_path),
                "export_path": f"databases/{spec.file_type.lower()}.json",
                "file_type": spec.file_type,
            }
        )

    special_parsers = {"GAME.DAT": parse_game_file, "TEST.DAT": parse_test_file, "EDITOR.DAT": parse_editor_file}
    for file_name, parser in special_parsers.items():
        source_path = game_dir / file_name
        export_data = parser(source_path)
        json_dump(out_dir / "databases" / f"{export_data['file_type'].lower()}.json", export_data)
        supported_files.append(
            {
                "source_file": file_name,
                "size": source_path.stat().st_size,
                "sha256": file_sha256(source_path),
                "export_path": f"databases/{export_data['file_type'].lower()}.json",
                "file_type": export_data["file_type"],
            }
        )

    for path in sort_paths(list((game_dir / "MAP").glob("MAPC_*.DAT"))):
        export_data = parse_mapc_file(path, str(path.relative_to(game_dir).as_posix()))
        json_dump(out_dir / "maps" / f"{path.stem}.json", export_data)
        supported_files.append(
            {
                "source_file": str(path.relative_to(game_dir).as_posix()),
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
                "export_path": f"maps/{path.stem}.json",
                "file_type": "MAPC",
            }
        )

    for path in sort_paths(list((game_dir / "MAP").glob("SMAP_*.DAT"))):
        relative_path = path.relative_to(game_dir)
        smap_export, event_exports = parse_smap_file(path, str(relative_path.as_posix()), builder)
        map_dir = out_dir / "maps" / path.stem
        map_dir.mkdir(parents=True, exist_ok=True)
        json_dump(map_dir / "smap.json", smap_export)
        for relative_export_path, event_export in event_exports:
            json_dump(out_dir / relative_export_path, event_export)
            all_event_exports.append(event_export)
        supported_files.append(
            {
                "source_file": str(relative_path.as_posix()),
                "size": path.stat().st_size,
                "sha256": file_sha256(path),
                "export_path": f"maps/{path.stem}/smap.json",
                "file_type": "SMAP",
                "event_export_count": len(event_exports),
            }
        )

    dialogue_catalogs = _build_dialogue_catalogs(all_event_exports)

    jsonl_dump(out_dir / "texts" / "text_index.jsonl", builder.display_rows)
    for catalog_name, rows in builder.build_catalogs().items():
        jsonl_dump(out_dir / "texts" / "catalog" / catalog_name, rows)
    for catalog_name, rows in dialogue_catalogs.items():
        jsonl_dump(out_dir / "texts" / "dialogue_catalog" / catalog_name, rows)
    jsonl_dump(out_dir / "texts" / "resource_index.jsonl", builder.resource_rows)
    jsonl_dump(out_dir / "texts" / "rejected_segments.jsonl", builder.rejected_rows)

    text_stats = builder.build_report()
    opcode_stats = builder.build_opcode_stats()
    text_flow_report = build_text_flow_report(game_dir)
    runtime_reports = build_runtime_reports(game_dir)
    repack_readiness = build_repack_readiness_report_from_exports(out_dir)

    json_dump(out_dir / "raw" / "file_hashes.json", {"files": supported_files})
    json_dump(
        out_dir / "reports" / "summary.json",
        {
            "tool_version": TOOL_VERSION,
            "game_dir": str(game_dir),
            "out_dir": str(out_dir),
            "reference_map_path": resolved_reference_path,
            "supported_file_count": len(supported_files),
            "text_entry_count": len(builder.display_rows),
            "display_text_count": text_stats["display_text_count"],
            "resource_ref_count": text_stats["resource_ref_count"],
            "rejected_segment_count": text_stats["rejected_segment_count"],
            "catalog_file_count": text_stats["catalog_file_count"],
            "dialogue_catalog_file_count": len(dialogue_catalogs),
            "dialogue_block_count": sum(len(rows) for rows in dialogue_catalogs.values()),
            "confirmed_display_opcodes": text_stats["confirmed_display_opcodes"],
            "opcode_noise_summary": text_stats["opcode_noise_summary"],
            "report_paths": {
                "opcode_stats": "reports/opcode_stats.json",
                "text_flow": "reports/text_flow.json",
                "repack_readiness": "reports/repack_readiness.json",
                "runtime_opcode_map": "reports/runtime_opcode_map.json",
                "runtime_text_contract": "reports/runtime_text_contract.json",
                "runtime_encoding_chain": "reports/runtime_encoding_chain.json",
                "runtime_buffer_risks": "reports/runtime_buffer_risks.json",
                "ui_dat_crosswalk": "reports/ui_dat_crosswalk.json",
                "dat_ui_priority": "reports/dat_ui_priority.json",
                "dat_growth_blockers": "reports/dat_growth_blockers.json",
            },
        },
    )
    json_dump(out_dir / "reports" / "text_stats.json", text_stats)
    json_dump(out_dir / "reports" / "opcode_stats.json", opcode_stats)
    json_dump(out_dir / "reports" / "text_flow.json", text_flow_report)
    json_dump(out_dir / "reports" / "repack_readiness.json", repack_readiness)
    json_dump(out_dir / "reports" / "runtime_opcode_map.json", runtime_reports["runtime_opcode_map"])
    json_dump(out_dir / "reports" / "runtime_text_contract.json", runtime_reports["runtime_text_contract"])
    json_dump(out_dir / "reports" / "runtime_encoding_chain.json", runtime_reports["runtime_encoding_chain"])
    json_dump(out_dir / "reports" / "runtime_buffer_risks.json", runtime_reports["runtime_buffer_risks"])
    json_dump(out_dir / "reports" / "ui_dat_crosswalk.json", runtime_reports["ui_dat_crosswalk"])
    json_dump(out_dir / "reports" / "dat_ui_priority.json", runtime_reports["dat_ui_priority"])
    json_dump(out_dir / "reports" / "dat_growth_blockers.json", runtime_reports["dat_growth_blockers"])
    json_dump(
        out_dir / "reports" / "coverage.json",
        {
            "supported_file_count": len(supported_files),
            "top_level_dat_count": len(SUPPORTED_TOP_LEVEL),
            "mapc_count": sum(1 for item in supported_files if item["file_type"] == "MAPC"),
            "smap_count": sum(1 for item in supported_files if item["file_type"] == "SMAP"),
            "roundtrip_ready_types": sorted({item["file_type"] for item in supported_files}),
            "display_text_count": text_stats["display_text_count"],
            "resource_ref_count": text_stats["resource_ref_count"],
            "rejected_segment_count": text_stats["rejected_segment_count"],
            "dialogue_catalog_file_count": len(dialogue_catalogs),
            "dialogue_block_count": sum(len(rows) for rows in dialogue_catalogs.values()),
            "confirmed_display_opcodes": text_stats["confirmed_display_opcodes"],
        },
    )
    manifest = {
        "tool_version": TOOL_VERSION,
        "created_at_utc": utc_timestamp(),
        "game_dir": str(game_dir),
        "out_dir": str(out_dir),
        "source_encoding": SOURCE_ENCODING,
        "reference_map_path": resolved_reference_path,
        "supported_files": supported_files,
    }
    json_dump(out_dir / "manifest.json", manifest)
    return manifest


def inspect_export(out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    text_stats = json.loads((out_dir / "reports" / "text_stats.json").read_text(encoding="utf-8"))
    return {
        "manifest": json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")),
        "summary": json.loads((out_dir / "reports" / "summary.json").read_text(encoding="utf-8")),
        "coverage": json.loads((out_dir / "reports" / "coverage.json").read_text(encoding="utf-8")),
        "text_stats": text_stats,
        "opcode_stats": json.loads((out_dir / "reports" / "opcode_stats.json").read_text(encoding="utf-8")),
        "text_flow": json.loads((out_dir / "reports" / "text_flow.json").read_text(encoding="utf-8")),
        "repack_readiness": json.loads((out_dir / "reports" / "repack_readiness.json").read_text(encoding="utf-8")),
        "runtime_opcode_map": json.loads((out_dir / "reports" / "runtime_opcode_map.json").read_text(encoding="utf-8")),
        "runtime_text_contract": json.loads((out_dir / "reports" / "runtime_text_contract.json").read_text(encoding="utf-8")),
        "runtime_encoding_chain": json.loads((out_dir / "reports" / "runtime_encoding_chain.json").read_text(encoding="utf-8")),
        "runtime_buffer_risks": json.loads((out_dir / "reports" / "runtime_buffer_risks.json").read_text(encoding="utf-8")),
        "ui_dat_crosswalk": json.loads((out_dir / "reports" / "ui_dat_crosswalk.json").read_text(encoding="utf-8")),
        "dat_ui_priority": json.loads((out_dir / "reports" / "dat_ui_priority.json").read_text(encoding="utf-8")),
        "dat_growth_blockers": json.loads((out_dir / "reports" / "dat_growth_blockers.json").read_text(encoding="utf-8")),
        "display_text_count": text_stats["display_text_count"],
        "resource_ref_count": text_stats["resource_ref_count"],
        "rejected_segment_count": text_stats["rejected_segment_count"],
        "confirmed_display_opcodes": text_stats["confirmed_display_opcodes"],
        "opcode_noise_summary": text_stats["opcode_noise_summary"],
    }


def verify_roundtrip(game_dir: Path, out_dir: Path) -> dict[str, Any]:
    game_dir = game_dir.resolve()
    out_dir = out_dir.resolve()
    results: list[dict[str, Any]] = []
    for file_name, spec in FIXED_FILE_SPECS.items():
        export_data = json.loads((out_dir / "databases" / f"{spec.file_type.lower()}.json").read_text(encoding="utf-8"))
        rebuilt = pack_fixed_file(export_data, spec)
        original = (game_dir / file_name).read_bytes()
        results.append({"source_file": file_name, "matched": rebuilt == original, "rebuilt_sha256": sha256_hex(rebuilt), "original_sha256": sha256_hex(original)})
    for file_name in SPECIAL_FILE_NAMES:
        export_data = json.loads((out_dir / "databases" / f"{Path(file_name).stem.lower()}.json").read_text(encoding="utf-8"))
        rebuilt = pack_special_file(export_data)
        original = (game_dir / file_name).read_bytes()
        results.append({"source_file": file_name, "matched": rebuilt == original, "rebuilt_sha256": sha256_hex(rebuilt), "original_sha256": sha256_hex(original)})
    for export_path in sort_paths(list((out_dir / "maps").glob("MAPC_*.json"))):
        export_data = json.loads(export_path.read_text(encoding="utf-8"))
        rebuilt = pack_mapc_file(export_data)
        original = (game_dir / export_data["source_file"]).read_bytes()
        results.append({"source_file": export_data["source_file"], "matched": rebuilt == original, "rebuilt_sha256": sha256_hex(rebuilt), "original_sha256": sha256_hex(original)})
    for smap_dir in sort_paths([path for path in (out_dir / "maps").iterdir() if path.is_dir() and path.name.startswith("SMAP_")]):
        smap_export = json.loads((smap_dir / "smap.json").read_text(encoding="utf-8"))
        event_exports = [json.loads(path.read_text(encoding="utf-8")) for path in sort_paths(list((smap_dir / "events").glob("*.json")))]
        rebuilt = pack_smap_file(smap_export, event_exports)
        original = (game_dir / smap_export["source_file"]).read_bytes()
        results.append({"source_file": smap_export["source_file"], "matched": rebuilt == original, "rebuilt_sha256": sha256_hex(rebuilt), "original_sha256": sha256_hex(original)})
    text_index_rows = [json.loads(line) for line in (out_dir / "texts" / "text_index.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = {
        "total_files_checked": len(results),
        "matched_files": sum(1 for item in results if item["matched"]),
        "reverse_lookup_ok": all(row.get("source_offset_in_file") is not None for row in text_index_rows),
        "all_matched": all(item["matched"] for item in results) and all(row.get("source_offset_in_file") is not None for row in text_index_rows),
        "results": results,
    }
    json_dump(out_dir / "reports" / "verify_roundtrip.json", summary)
    return summary
