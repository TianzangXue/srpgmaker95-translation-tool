from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import (
    SOURCE_ENCODING,
    build_text_object,
    bytes_to_hex,
    hex_to_bytes,
    read_null_terminated_text,
    sha256_hex,
)


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str
    offset: int
    size: int
    count: int = 1
    role: str | None = None
    note: str = ""
    struct_size: int = 0
    item_fields: tuple["FieldSpec", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FixedFileSpec:
    file_type: str
    source_file: str
    record_size: int
    fields: tuple[FieldSpec, ...]
    record_count: int | None = None
    id_prefix: str | None = None

    @property
    def prefix(self) -> str:
        return self.id_prefix or self.file_type.lower()


def _int_unpack(fmt: str, payload: bytes) -> int:
    return struct.unpack(fmt, payload)[0]


def _parse_struct(item_bytes: bytes, item_fields: tuple[FieldSpec, ...]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for field_spec in item_fields:
        parsed[field_spec.name] = _parse_known_field(item_bytes, field_spec, record_id="nested", record_index=0, base_offset=0)[0]
    return parsed


def _parse_known_field(
    record_bytes: bytes,
    field_spec: FieldSpec,
    *,
    record_id: str,
    record_index: int,
    base_offset: int,
) -> tuple[Any, list[dict[str, Any]]]:
    field_slice = record_bytes[field_spec.offset : field_spec.offset + field_spec.size]
    text_entries: list[dict[str, Any]] = []
    if field_spec.kind == "text":
        text, text_bytes, null_terminated = read_null_terminated_text(field_slice, SOURCE_ENCODING)
        max_bytes = field_spec.size - 1 if null_terminated else field_spec.size
        text_key = field_spec.role or field_spec.name
        text_id = f"{record_id}:{text_key}"
        value = build_text_object(
            text_id=text_id,
            role=text_key,
            field_bytes=field_slice,
            text_bytes=text_bytes,
            text=text,
            max_bytes=max_bytes,
            is_fixed_size=True,
            null_terminated=null_terminated,
            padding_byte=0,
            supports_length_growth=False,
            notes=[field_spec.note] if field_spec.note else [],
        )
        value["offset_in_record"] = field_spec.offset
        value["source_offset_in_file"] = base_offset + field_spec.offset
        text_entries.append(
            {
                "text_id": text_id,
                "record_id": record_id,
                "record_index": record_index,
                "source_offset_in_file": base_offset + field_spec.offset,
                "field_size": field_spec.size,
                "text_object": value,
            }
        )
        return value, text_entries
    if field_spec.kind == "bytes":
        return {
            "offset_in_record": field_spec.offset,
            "size": field_spec.size,
            "hex": bytes_to_hex(field_slice),
            "note": field_spec.note,
        }, text_entries
    if field_spec.kind == "int32":
        return _int_unpack("<i", field_slice), text_entries
    if field_spec.kind == "int16":
        return _int_unpack("<h", field_slice), text_entries
    if field_spec.kind == "uint8":
        return field_slice[0], text_entries
    if field_spec.kind == "int32_array":
        return list(struct.unpack(f"<{field_spec.count}i", field_slice)), text_entries
    if field_spec.kind == "int16_array":
        return list(struct.unpack(f"<{field_spec.count}h", field_slice)), text_entries
    if field_spec.kind == "uint8_array":
        return list(field_slice), text_entries
    if field_spec.kind == "struct_array":
        items: list[dict[str, Any]] = []
        for item_index in range(field_spec.count):
            item_offset = item_index * field_spec.struct_size
            item_bytes = field_slice[item_offset : item_offset + field_spec.struct_size]
            items.append(_parse_struct(item_bytes, field_spec.item_fields))
        return items, text_entries
    raise ValueError(f"Unsupported field kind: {field_spec.kind}")


def parse_fixed_file(
    path: Path,
    spec: FixedFileSpec,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    data = path.read_bytes()
    if len(data) % spec.record_size != 0:
        raise ValueError(f"{path.name}: invalid size {len(data)} for record size {spec.record_size}")
    record_count = len(data) // spec.record_size
    if spec.record_count is not None and record_count != spec.record_count:
        raise ValueError(f"{path.name}: expected {spec.record_count} records, got {record_count}")
    text_index_rows: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    covered_ranges = [(field.offset, field.offset + field.size) for field in spec.fields]
    for record_index in range(record_count):
        offset = record_index * spec.record_size
        record_bytes = data[offset : offset + spec.record_size]
        record_id = f"{spec.prefix}:{record_index:03d}"
        fields: dict[str, Any] = {}
        for field_spec in spec.fields:
            value, field_text_entries = _parse_known_field(
                record_bytes,
                field_spec,
                record_id=record_id,
                record_index=record_index,
                base_offset=offset,
            )
            fields[field_spec.name] = value
            text_index_rows.extend(field_text_entries)
        unknown_ranges = _compute_unknown_ranges(spec.record_size, covered_ranges)
        unknown_bytes = b"".join(record_bytes[start:end] for start, end in unknown_ranges)
        records.append(
            {
                "record_id": record_id,
                "record_index": record_index,
                "offset": offset,
                "size": spec.record_size,
                "fields": fields,
                "unknown_bytes_hex": bytes_to_hex(unknown_bytes),
                "raw_record_sha256": sha256_hex(record_bytes),
                "raw_record_hex": bytes_to_hex(record_bytes),
            }
        )
    payload = {
        "file_type": spec.file_type,
        "source_file": spec.source_file,
        "record_size": spec.record_size,
        "record_count": record_count,
        "encoding": SOURCE_ENCODING,
        "records": records,
    }
    return payload, text_index_rows


def pack_fixed_file(export_data: dict[str, Any], spec: FixedFileSpec) -> bytes:
    blob = bytearray()
    for record in export_data["records"]:
        raw_hex = record.get("raw_record_hex")
        if raw_hex:
            blob.extend(hex_to_bytes(raw_hex))
            continue
        record_bytes = bytearray(spec.record_size)
        for field_spec in spec.fields:
            field_value = record["fields"][field_spec.name]
            field_slice = _pack_known_field(field_value, field_spec)
            record_bytes[field_spec.offset : field_spec.offset + field_spec.size] = field_slice
        blob.extend(record_bytes)
    return bytes(blob)


def _pack_known_field(field_value: Any, field_spec: FieldSpec) -> bytes:
    if field_spec.kind == "text":
        return hex_to_bytes(field_value["source_bytes_hex"]).ljust(field_spec.size, b"\x00")[: field_spec.size]
    if field_spec.kind == "bytes":
        return hex_to_bytes(field_value["hex"]).ljust(field_spec.size, b"\x00")[: field_spec.size]
    if field_spec.kind == "int32":
        return struct.pack("<i", int(field_value))
    if field_spec.kind == "int16":
        return struct.pack("<h", int(field_value))
    if field_spec.kind == "uint8":
        return bytes([int(field_value) & 0xFF])
    if field_spec.kind == "int32_array":
        return struct.pack(f"<{field_spec.count}i", *field_value)
    if field_spec.kind == "int16_array":
        return struct.pack(f"<{field_spec.count}h", *field_value)
    if field_spec.kind == "uint8_array":
        data = bytes(int(item) & 0xFF for item in field_value)
        return data.ljust(field_spec.size, b"\x00")[: field_spec.size]
    if field_spec.kind == "struct_array":
        buffer = bytearray()
        for item in field_value:
            item_bytes = bytearray(field_spec.struct_size)
            for child_spec in field_spec.item_fields:
                child_bytes = _pack_known_field(item[child_spec.name], child_spec)
                item_bytes[child_spec.offset : child_spec.offset + child_spec.size] = child_bytes
            buffer.extend(item_bytes)
        return bytes(buffer)
    raise ValueError(f"Unsupported field kind: {field_spec.kind}")


def _compute_unknown_ranges(record_size: int, covered_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not covered_ranges:
        return [(0, record_size)]
    merged: list[tuple[int, int]] = []
    for start, end in sorted(covered_ranges):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    unknown: list[tuple[int, int]] = []
    cursor = 0
    for start, end in merged:
        if cursor < start:
            unknown.append((cursor, start))
        cursor = end
    if cursor < record_size:
        unknown.append((cursor, record_size))
    return unknown


FRAME_FIELDS = (
    FieldSpec("x_1", "int16", 0, 2),
    FieldSpec("y_1", "int16", 2, 2),
    FieldSpec("a_1", "int16", 4, 2),
    FieldSpec("x_2", "int16", 6, 2),
    FieldSpec("y_2", "int16", 8, 2),
    FieldSpec("a_2", "int16", 10, 2),
    FieldSpec("x_3", "int16", 12, 2),
    FieldSpec("y_3", "int16", 14, 2),
    FieldSpec("a_3", "int16", 16, 2),
    FieldSpec("x_4", "int16", 18, 2),
    FieldSpec("y_4", "int16", 20, 2),
    FieldSpec("a_4", "int16", 22, 2),
)


FIXED_FILE_SPECS: dict[str, FixedFileSpec] = {
    "WORD.DAT": FixedFileSpec(
        file_type="WORD",
        source_file="WORD.DAT",
        record_size=32,
        fields=(
            FieldSpec("name", "text", 0, 20, role="label"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
        ),
    ),
    "MAPNAME.DAT": FixedFileSpec(
        file_type="MAPNAME",
        source_file="MAPNAME.DAT",
        record_size=32,
        fields=(
            FieldSpec("name", "text", 0, 20, role="title"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
        ),
        record_count=100,
        id_prefix="mapname",
    ),
    "VARNAME.DAT": FixedFileSpec(
        file_type="VARNAME",
        source_file="VARNAME.DAT",
        record_size=32,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
        ),
        id_prefix="varname",
    ),
    "SWNAME.DAT": FixedFileSpec(
        file_type="SWNAME",
        source_file="SWNAME.DAT",
        record_size=32,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
        ),
        record_count=500,
        id_prefix="swname",
    ),
    "UNIT.DAT": FixedFileSpec(
        file_type="UNIT",
        source_file="UNIT.DAT",
        record_size=480,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
            FieldSpec("face", "int32", 32, 4),
            FieldSpec("class_id", "int32", 36, 4),
            FieldSpec("level_start", "int32", 40, 4),
            FieldSpec("exp_start", "int32", 44, 4),
            FieldSpec("hp_start", "int32", 48, 4),
            FieldSpec("mp_start", "int32", 52, 4),
            FieldSpec("power_start", "int32", 56, 4),
            FieldSpec("intel_start", "int32", 60, 4),
            FieldSpec("skill_start", "int32", 64, 4),
            FieldSpec("stamina_start", "int32", 68, 4),
            FieldSpec("hp_growth", "int32", 72, 4),
            FieldSpec("mp_growth", "int32", 76, 4),
            FieldSpec("power_growth", "int32", 80, 4),
            FieldSpec("intel_growth", "int32", 84, 4),
            FieldSpec("skill_growth", "int32", 88, 4),
            FieldSpec("stamina_growth", "int32", 92, 4),
            FieldSpec("eqp_weapon", "int32", 96, 4),
            FieldSpec("eqp_armor", "int32", 100, 4),
            FieldSpec("eqp_other", "int32", 104, 4),
            FieldSpec("death_message", "text", 108, 72, role="death_message"),
            FieldSpec("unk_0", "bytes", 180, 300, note="unknown"),
        ),
    ),
    "CLASS.DAT": FixedFileSpec(
        file_type="CLASS",
        source_file="CLASS.DAT",
        record_size=640,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
            FieldSpec("mvmnt_speed", "int32", 32, 4),
            FieldSpec("mvmnt_type", "int32", 36, 4),
            FieldSpec("mvmnt_rules", "int32_array", 40, 280, count=70),
            FieldSpec("img_no", "int32", 320, 4),
            FieldSpec("anim_weapon_type_A", "int32", 324, 4),
            FieldSpec("anim_weapon_type_B", "int32", 328, 4),
            FieldSpec("anim_weapon_type_C", "int32", 332, 4),
            FieldSpec("anim_no_weapon", "int32", 336, 4),
            FieldSpec("anim_use_magic_on_enemy", "int32", 340, 4),
            FieldSpec("anim_use_magic_on_ally", "int32", 344, 4),
            FieldSpec("unk1_0", "bytes", 348, 72, note="unknown"),
            FieldSpec("stat_power", "int32", 420, 4),
            FieldSpec("stat_intel", "int32", 424, 4),
            FieldSpec("stat_skill", "int32", 428, 4),
            FieldSpec("stat_stamina", "int32", 432, 4),
            FieldSpec("magic_lvls", "int32_array", 436, 80, count=20),
            FieldSpec("magic_ids", "int32_array", 516, 80, count=20),
            FieldSpec("weak_A", "uint8", 596, 1),
            FieldSpec("weak_B", "uint8", 597, 1),
            FieldSpec("weak_C", "uint8", 598, 1),
            FieldSpec("weak_D", "uint8", 599, 1),
            FieldSpec("weak_E", "uint8", 600, 1),
            FieldSpec("weak_F", "uint8", 601, 1),
            FieldSpec("unk2_0", "bytes", 602, 2, note="unknown"),
            FieldSpec("resists_poison", "uint8", 604, 1),
            FieldSpec("resists_sleep", "uint8", 605, 1),
            FieldSpec("resists_atkdwn", "uint8", 606, 1),
            FieldSpec("resists_defdwn", "uint8", 607, 1),
            FieldSpec("unk3_0", "bytes", 608, 32, note="unknown"),
        ),
    ),
    "ITEM.DAT": FixedFileSpec(
        file_type="ITEM",
        source_file="ITEM.DAT",
        record_size=640,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
            FieldSpec("type", "int32", 32, 4),
            FieldSpec("cost", "int32", 36, 4),
            FieldSpec("desc", "text", 40, 70, role="desc"),
            FieldSpec("unk1_0", "bytes", 110, 10, note="unknown"),
            FieldSpec("class_usage", "uint8_array", 120, 150, count=150),
            FieldSpec("unk2_0", "bytes", 270, 50, note="unknown"),
            FieldSpec("wpn_range", "int32", 320, 4),
            FieldSpec("wpn_power", "int32", 324, 4),
            FieldSpec("wpn_useability", "int32", 328, 4),
            FieldSpec("wpn_critical_value", "int32", 332, 4),
            FieldSpec("wpn_animation", "int32", 336, 4),
            FieldSpec("wpn_elem_A", "uint8", 340, 1),
            FieldSpec("wpn_elem_B", "uint8", 341, 1),
            FieldSpec("wpn_elem_C", "uint8", 342, 1),
            FieldSpec("wpn_elem_D", "uint8", 343, 1),
            FieldSpec("wpn_elem_E", "uint8", 344, 1),
            FieldSpec("wpn_elem_F", "uint8", 345, 1),
            FieldSpec("zero1", "bytes", 346, 2, note="padding"),
            FieldSpec("arm_phys_def", "int32", 348, 4),
            FieldSpec("arm_magic_def", "int32", 352, 4),
            FieldSpec("arm_crit_avoid", "int32", 356, 4),
            FieldSpec("arm_weight", "int32", 360, 4),
            FieldSpec("arm_elem_A", "uint8", 364, 1),
            FieldSpec("arm_elem_B", "uint8", 365, 1),
            FieldSpec("arm_elem_C", "uint8", 366, 1),
            FieldSpec("arm_elem_D", "uint8", 367, 1),
            FieldSpec("arm_elem_E", "uint8", 368, 1),
            FieldSpec("arm_elem_F", "uint8", 369, 1),
            FieldSpec("zero2", "bytes", 370, 2, note="padding"),
            FieldSpec("oth_change_power", "int32", 372, 4),
            FieldSpec("oth_change_intel", "int32", 376, 4),
            FieldSpec("oth_change_skill", "int32", 380, 4),
            FieldSpec("oth_change_stamina", "int32", 384, 4),
            FieldSpec("oth_change_speed", "int32", 388, 4),
            FieldSpec("oth_change_growth", "int32", 392, 4),
            FieldSpec("med_hp_recovery", "int32", 396, 4),
            FieldSpec("med_mp_recovery", "int32", 400, 4),
            FieldSpec("med_phys_def_up", "int32", 404, 4),
            FieldSpec("med_magic_def_up", "int32", 408, 4),
            FieldSpec("stat_raise_maxhp", "int32", 412, 4),
            FieldSpec("stat_raise_maxmp", "int32", 416, 4),
            FieldSpec("stat_raise_powerr", "int32", 420, 4),
            FieldSpec("stat_raise_intel", "int32", 424, 4),
            FieldSpec("stat_raise_skill", "int32", 428, 4),
            FieldSpec("stat_raise_stamina", "int32", 432, 4),
            FieldSpec("cls_change_before", "int32_array", 436, 80, count=20),
            FieldSpec("cls_change_after", "int32_array", 516, 80, count=20),
            FieldSpec("med_heal_poison", "uint8", 596, 1),
            FieldSpec("med_heal_sleep", "uint8", 597, 1),
            FieldSpec("med_heal_atkdown", "uint8", 598, 1),
            FieldSpec("med_heal_defdown", "uint8", 599, 1),
            FieldSpec("unk3_0", "bytes", 600, 4, note="unknown"),
            FieldSpec("oth_safe_poison", "uint8", 604, 1),
            FieldSpec("oth_safe_sleep", "uint8", 605, 1),
            FieldSpec("oth_safe_atkdown", "uint8", 606, 1),
            FieldSpec("oth_safe_defdown", "uint8", 607, 1),
            FieldSpec("unk4_0", "bytes", 608, 4, note="unknown"),
            FieldSpec("cls_lvl_require", "int32", 612, 4),
            FieldSpec("med_range", "int32", 616, 4),
            FieldSpec("mgc_magic", "int32", 620, 4),
            FieldSpec("unk5_0", "bytes", 624, 16, note="unknown"),
        ),
    ),
    "MAGIC.DAT": FixedFileSpec(
        file_type="MAGIC",
        source_file="MAGIC.DAT",
        record_size=480,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
            FieldSpec("type", "int32", 32, 4),
            FieldSpec("mp_cost", "int32", 36, 4),
            FieldSpec("desc", "text", 40, 70, role="desc"),
            FieldSpec("unk1_0", "bytes", 110, 2, note="unknown"),
            FieldSpec("unk2_0", "bytes", 112, 8, note="unknown"),
            FieldSpec("dmg_damage", "int32", 120, 4),
            FieldSpec("dmg_accuracy", "int32", 124, 4),
            FieldSpec("unk3_0", "bytes", 128, 8, note="unknown"),
            FieldSpec("dmg_elem_A", "uint8", 136, 1),
            FieldSpec("dmg_elem_B", "uint8", 137, 1),
            FieldSpec("dmg_elem_C", "uint8", 138, 1),
            FieldSpec("dmg_elem_D", "uint8", 139, 1),
            FieldSpec("dmg_elem_E", "uint8", 140, 1),
            FieldSpec("dmg_elem_F", "uint8", 141, 1),
            FieldSpec("unk4_0", "bytes", 142, 2, note="unknown"),
            FieldSpec("animation", "int32", 144, 4),
            FieldSpec("exp_gain", "int32", 148, 4),
            FieldSpec("degrade_effect", "int32", 152, 4),
            FieldSpec("target", "int32", 156, 4),
            FieldSpec("raise_power", "int32", 160, 4),
            FieldSpec("raise_relation", "int32", 164, 4),
            FieldSpec("raise_effect", "int32", 168, 4),
            FieldSpec("unk5_0", "bytes", 172, 60, note="unknown"),
            FieldSpec("range_apply_to_entire_map", "int32", 232, 4),
            FieldSpec("area_apply_to_entire_map", "int32", 236, 4),
            FieldSpec("range_bitmap", "int32_array", 240, 84, count=21),
            FieldSpec("area_bitmap", "int32_array", 324, 84, count=21),
            FieldSpec("unk6_0", "bytes", 408, 72, note="unknown"),
        ),
    ),
    "ANIME.DAT": FixedFileSpec(
        file_type="ANIME",
        source_file="ANIME.DAT",
        record_size=2400,
        fields=(
            FieldSpec("name", "text", 0, 20, role="name"),
            FieldSpec("zero_padding_0", "bytes", 20, 12, note="padding"),
            FieldSpec("unk1_0", "bytes", 32, 4, note="unknown"),
            FieldSpec("curr_class_graphic", "int32", 36, 4),
            FieldSpec("frames", "struct_array", 40, 2160, count=90, struct_size=24, item_fields=FRAME_FIELDS),
            FieldSpec("frame_amount", "int32", 2200, 4),
            FieldSpec("frame_hit", "int32", 2204, 4),
            FieldSpec("frame_miss", "int32", 2208, 4),
            FieldSpec("unk2_0", "bytes", 2212, 28, note="unknown"),
            FieldSpec("sfx_hit", "text", 2240, 48, role="sfx_hit"),
            FieldSpec("sfx_miss", "text", 2288, 48, role="sfx_miss"),
            FieldSpec("timing_hit", "int32", 2336, 4),
            FieldSpec("timing_miss", "int32", 2340, 4),
            FieldSpec("unk3_0", "bytes", 2344, 56, note="unknown"),
        ),
    ),
    "GEOLOGY.DAT": FixedFileSpec(
        file_type="GEOLOGY",
        source_file="GEOLOGY.DAT",
        record_size=60,
        fields=(
            FieldSpec("name", "text", 0, 16, role="name"),
            FieldSpec("zero_padding_0", "bytes", 16, 16, note="padding"),
            FieldSpec("dense_effect", "int32", 32, 4),
            FieldSpec("hp_change", "int32", 36, 4),
            FieldSpec("mvmnt_change_walking", "int32", 40, 4),
            FieldSpec("mvmnt_change_flying", "int32", 44, 4),
            FieldSpec("mvmnt_change_special", "int32", 48, 4),
            FieldSpec("battle_bg", "int32", 52, 4),
            FieldSpec("unk_0", "bytes", 56, 4, note="unknown"),
        ),
    ),
}


SPECIAL_FILE_NAMES = ("GAME.DAT", "TEST.DAT", "EDITOR.DAT")
