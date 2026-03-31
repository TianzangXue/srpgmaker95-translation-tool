"""Runtime analysis helpers and reporting views for SRPG MAKER 95.

The project uses this module to convert reverse-engineering findings into a
stable reporting vocabulary: UI surface ids, draw paths, coverage summaries,
and DAT-backed crosswalk entries. The goal is to keep code, reports, and
documentation aligned on one set of names.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import SOURCE_ENCODING, file_sha256
from .specs import FIXED_FILE_SPECS

try:
    import pefile  # type: ignore
except Exception:  # pragma: no cover
    pefile = None


INTERESTING_IMPORTS = {
    "DrawTextA",
    "TextOutA",
    "CreateFontIndirectA",
    "GetACP",
    "GetCPInfo",
    "MultiByteToWideChar",
    "WideCharToMultiByte",
    "lstrcpyA",
    "lstrlenA",
}


def _text_field_spec(source_file: str, field_name: str):
    spec = FIXED_FILE_SPECS[source_file]
    for field in spec.fields:
        if field.kind == "text" and field.name == field_name:
            return spec, field
    raise KeyError(f"Unknown text field: {source_file}:{field_name}")


def _text_field_summary(source_file: str, field_name: str) -> dict[str, Any]:
    spec, field = _text_field_spec(source_file, field_name)
    return {
        "source_file": source_file,
        "file_type": spec.file_type,
        "field_name": field.name,
        "role": field.role or field.name,
        "record_size": spec.record_size,
        "offset": field.offset,
        "slot_size_bytes": field.size,
        "hard_limit_bytes": field.size - 1,
        "text_id_pattern": f"{spec.prefix}:*:{field.role or field.name}",
    }


def _crosswalk_entry(
    *,
    ui_surface_id: str,
    runtime_function: str,
    draw_path: str,
    source_kind: str,
    source_file: str | list[str] | None,
    text_role: str | list[str],
    text_id_pattern: str | list[str] | None,
    current_slot_limit_bytes: int | list[int] | None,
    runtime_buffer_limit_bytes: int | list[int] | None,
    notes: str,
) -> dict[str, Any]:
    return {
        "ui_surface_id": ui_surface_id,
        "runtime_function": runtime_function,
        "draw_path": draw_path,
        "source_kind": source_kind,
        "source_file": source_file,
        "text_role": text_role,
        "text_id_pattern": text_id_pattern,
        "current_slot_limit_bytes": current_slot_limit_bytes,
        "runtime_buffer_limit_bytes": runtime_buffer_limit_bytes,
        "notes": notes,
    }


def build_ui_dat_crosswalk() -> dict[str, Any]:
    word_label = _text_field_summary("WORD.DAT", "name")
    map_title = _text_field_summary("MAPNAME.DAT", "name")
    unit_name = _text_field_summary("UNIT.DAT", "name")
    unit_death = _text_field_summary("UNIT.DAT", "death_message")
    class_name = _text_field_summary("CLASS.DAT", "name")
    item_name = _text_field_summary("ITEM.DAT", "name")
    item_desc = _text_field_summary("ITEM.DAT", "desc")
    magic_name = _text_field_summary("MAGIC.DAT", "name")
    magic_desc = _text_field_summary("MAGIC.DAT", "desc")

    surfaces = [
        _crosswalk_entry(
            ui_surface_id="title_main_menu",
            runtime_function="unresolved_title_menu_path",
            draw_path="unknown",
            source_kind="unknown",
            source_file=None,
            text_role="title_menu_labels",
            text_id_pattern=None,
            current_slot_limit_bytes=None,
            runtime_buffer_limit_bytes=None,
            notes="Title-screen labels are not yet tied to a DAT-backed reader. Keep them outside the first DAT expansion wave until their runtime source is proven.",
        ),
        _crosswalk_entry(
            ui_surface_id="opening_skip_op_prompt",
            runtime_function="sub_42523C -> sub_414344",
            draw_path="opcode45_menu_state_slots -> stable-menu16 GDI bridge",
            source_kind="smap_text",
            source_file="MAP/SMAP_001.DAT",
            text_role="system_choice_or_prompt",
            text_id_pattern="smap:001:event:OPENING:cmd:*:seg:*",
            current_slot_limit_bytes=80,
            runtime_buffer_limit_bytes=80,
            notes="This confirms an important non-DAT UI surface. It stays outside the DAT growth plan, but it proves UI coverage cannot be solved by DAT work alone.",
        ),
        _crosswalk_entry(
            ui_surface_id="unit_sheet_panel",
            runtime_function="sub_412448",
            draw_path="mixed DrawTextA(unit name) + patched GDI bridge(class name, LV/HP/MP/total-attack labels, and stat labels) under stable-menu16",
            source_kind="dat_text",
            source_file=[unit_name["source_file"], class_name["source_file"], word_label["source_file"]],
            text_role=[unit_name["role"], class_name["role"], word_label["role"]],
            text_id_pattern=[unit_name["text_id_pattern"], class_name["text_id_pattern"], word_label["text_id_pattern"]],
            current_slot_limit_bytes=[unit_name["hard_limit_bytes"], class_name["hard_limit_bytes"], word_label["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=256,
            notes="sub_412448 copies UNIT.DAT name into a DrawTextA path, and now routes CLASS.DAT name plus the full early WORD.DAT label/value section and later stat labels through the shared GDI bridge. This is a confirmed high-value DAT-backed UI surface.",
        ),
        _crosswalk_entry(
            ui_surface_id="unit_command_or_item_menu",
            runtime_function="sub_420564 -> sub_414344",
            draw_path="menu state slots -> stable-menu16 GDI bridge",
            source_kind="dat_text",
            source_file=[word_label["source_file"], item_name["source_file"], unit_name["source_file"]],
            text_role=["menu_labels", item_name["role"], unit_name["role"]],
            text_id_pattern=[word_label["text_id_pattern"], item_name["text_id_pattern"], unit_name["text_id_pattern"]],
            current_slot_limit_bytes=[word_label["hard_limit_bytes"], item_name["hard_limit_bytes"], unit_name["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=[80, 256],
            notes="sub_420564 populates menu rows from WORD.DAT labels, availability checks from ITEM.DAT, and unit state from UNIT.DAT before handing the rows to the opcode-45-like menu renderer.",
        ),
        _crosswalk_entry(
            ui_surface_id="battle_reward_popup",
            runtime_function="sub_414B14",
            draw_path="patched GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=[word_label["source_file"], item_name["source_file"]],
            text_role=["reward_labels", item_name["role"]],
            text_id_pattern=[word_label["text_id_pattern"], item_name["text_id_pattern"]],
            current_slot_limit_bytes=[word_label["hard_limit_bytes"], item_name["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=256,
            notes="The reward popup appends ITEM.DAT names to WORD.DAT reward labels in 256-byte per-line scratch rows before sprite-text drawing, and still relies on sub_4051A0 for the formatted numeric pieces on EXP lines.",
        ),
        _crosswalk_entry(
            ui_surface_id="post_battle_summary",
            runtime_function="sub_414D40",
            draw_path="patched GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=[unit_name["source_file"], magic_name["source_file"], word_label["source_file"]],
            text_role=["unit_name", "magic_name", "summary_labels"],
            text_id_pattern=[unit_name["text_id_pattern"], magic_name["text_id_pattern"], word_label["text_id_pattern"]],
            current_slot_limit_bytes=[unit_name["hard_limit_bytes"], magic_name["hard_limit_bytes"], word_label["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=256,
            notes="sub_414D40 builds summary rows from UNIT.DAT names, MAGIC.DAT names, and WORD.DAT labels, then uses sub_4051A0 for the formatted level-up numbers. This is the clearest confirmed MAGIC-backed UI surface so far.",
        ),
        _crosswalk_entry(
            ui_surface_id="compact_unit_card",
            runtime_function="sub_41C960",
            draw_path="mixed DrawTextA(unit name) + patched GDI bridge(labels and values) under stable-menu16",
            source_kind="dat_text",
            source_file=[unit_name["source_file"], word_label["source_file"]],
            text_role=[unit_name["role"], "unit_card_labels"],
            text_id_pattern=[unit_name["text_id_pattern"], word_label["text_id_pattern"]],
            current_slot_limit_bytes=[unit_name["hard_limit_bytes"], word_label["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=256,
            notes="sub_41C960 renders a compact unit card. It is another confirmed UNIT + WORD consumer and proves the same labels can land in mixed DrawTextA / patched GDI paths.",
        ),
        _crosswalk_entry(
            ui_surface_id="equip_slot_list_and_help",
            runtime_function="sub_4177E8 / sub_417D9C / sub_40F650 / sub_40F4C4 -> sub_4142D4",
            draw_path="menu rows plus shared two-line help panel via stable-menu16 GDI bridge",
            source_kind="dat_text",
            source_file=[word_label["source_file"], item_name["source_file"], item_desc["source_file"], magic_desc["source_file"]],
            text_role=["equip_labels", item_name["role"], item_desc["role"], magic_desc["role"]],
            text_id_pattern=[word_label["text_id_pattern"], item_name["text_id_pattern"], item_desc["text_id_pattern"], magic_desc["text_id_pattern"]],
            current_slot_limit_bytes=[word_label["hard_limit_bytes"], item_name["hard_limit_bytes"], item_desc["hard_limit_bytes"], magic_desc["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=[80, 256],
            notes="sub_4177E8, sub_417D9C, sub_417548, and sub_418108 build equipment, spell, and related item rows from WORD.DAT plus ITEM/MAGIC names. sub_40F4C4/sub_40F650/sub_40F930 populate the shared two-line help slots at a1+23453 / a1+23533, sub_4142D4 routes those help strings through the stable-menu16 GDI bridge, and the old cp932-only punctuation suffix/prefix constants on these upstream row builders are now normalized so they cannot leak as fixed mojibake markers.",
        ),
        _crosswalk_entry(
            ui_surface_id="sortie_unit_roster",
            runtime_function="sub_41E028 -> sub_4203C0 / sub_41F27C / sub_4208CC",
            draw_path="shared GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=unit_name["source_file"],
            text_role=unit_name["role"],
            text_id_pattern=unit_name["text_id_pattern"],
            current_slot_limit_bytes=unit_name["hard_limit_bytes"],
            runtime_buffer_limit_bytes=256,
            notes="The sortie roster uses sub_4203C0 for the 22-row unit list, sub_41F27C for highlighted rows/page text, and sub_4208CC for the page header/counter. These UNIT.DAT.name consumers were previously outside stable-menu16 and therefore stayed on the old readable-Japanese path.",
        ),
        _crosswalk_entry(
            ui_surface_id="battle_unit_hover_name",
            runtime_function="sub_413F80",
            draw_path="shared GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=unit_name["source_file"],
            text_role=unit_name["role"],
            text_id_pattern=unit_name["text_id_pattern"],
            current_slot_limit_bytes=unit_name["hard_limit_bytes"],
            runtime_buffer_limit_bytes=256,
            notes="The battlefield hover label above a unit head reuses UNIT.DAT.name and a second formatted line. This surface previously stayed on the old glyph path and is now migrated into stable-menu16.",
        ),
        _crosswalk_entry(
            ui_surface_id="item_magic_help_panel",
            runtime_function="sub_40F4C4 / sub_40F650 -> sub_4142D4",
            draw_path="shared GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=[item_desc["source_file"], magic_desc["source_file"]],
            text_role=[item_desc["role"], magic_desc["role"]],
            text_id_pattern=[item_desc["text_id_pattern"], magic_desc["text_id_pattern"]],
            current_slot_limit_bytes=[item_desc["hard_limit_bytes"], magic_desc["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=80,
            notes="The shared two-line help panel is the downstream consumer for item and magic descriptions. The data producers differ, but the final draw-site is sub_4142D4 and is now part of stable-menu16; its title line also no longer inherits the cp932-only punctuation suffixes that upstream producers used to append after item/equipment/skill names.",
        ),
        _crosswalk_entry(
            ui_surface_id="battle_skill_popup",
            runtime_function="sub_40F930 / sub_417548 / sub_4171E0 / sub_418308 -> sub_414344",
            draw_path="shared GDI bridge under stable-menu16 with skill-popup-local formatter and anchor tightening",
            source_kind="dat_text",
            source_file=[magic_name["source_file"], magic_desc["source_file"], word_label["source_file"]],
            text_role=[magic_name["role"], magic_desc["role"], "skill_popup_labels"],
            text_id_pattern=[magic_name["text_id_pattern"], magic_desc["text_id_pattern"], word_label["text_id_pattern"]],
            current_slot_limit_bytes=[magic_name["hard_limit_bytes"], magic_desc["hard_limit_bytes"], word_label["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=[80, 256],
            notes="The battle skill popup shown near the acting unit is not the camp shop formatter. sub_40F930 enters the skill menu state, sub_417548 formats the skill rows, sub_4171E0 sets the popup row anchor and width padding, sub_418308 lays out the selected-skill detail lines, and stable-menu16 now tightens the row formatter widths, shifts the row text anchor left, expands the row-box width padding slightly, and tightens the popup's local right-side anchor so MP/cost text stays inside the frame with the 12px Chinese UI font.",
        ),
        _crosswalk_entry(
            ui_surface_id="camp_shop_item_list",
            runtime_function="sub_4210D0 / sub_4210F4 / sub_421220 / sub_421478 / sub_4215A4 -> sub_414344",
            draw_path="shared GDI bridge under stable-menu16 with shop-specific row formatting and localized column anchors",
            source_kind="dat_text",
            source_file=[item_name["source_file"], word_label["source_file"]],
            text_role=[item_name["role"], "shop_labels"],
            text_id_pattern=[item_name["text_id_pattern"], word_label["text_id_pattern"]],
            current_slot_limit_bytes=[item_name["hard_limit_bytes"], word_label["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=[80, 256],
            notes="The camp shop item list reuses the shared menu row renderer but also has its own row formatter in sub_421220/sub_4215A4. The stable-menu16 baseline therefore needs both the remaining direct glyph callsite migration and a shop-only column-width/anchor correction.",
        ),
        _crosswalk_entry(
            ui_surface_id="item_and_class_change_preview",
            runtime_function="sub_40B188 / sub_40B8F0 / sub_40BB0C",
            draw_path="formatted effect rows, later shown by menu/status UI",
            source_kind="dat_text",
            source_file=[word_label["source_file"], item_name["source_file"], magic_name["source_file"]],
            text_role=["effect_labels", item_name["role"], magic_name["role"]],
            text_id_pattern=[word_label["text_id_pattern"], item_name["text_id_pattern"], magic_name["text_id_pattern"]],
            current_slot_limit_bytes=[word_label["hard_limit_bytes"], item_name["hard_limit_bytes"], magic_name["hard_limit_bytes"]],
            runtime_buffer_limit_bytes=40,
            notes="These helpers build short 40-byte effect rows from WORD labels plus ITEM/MAGIC data. They are important when deciding whether short DAT labels can stay fixed even if desc fields eventually expand.",
        ),
        _crosswalk_entry(
            ui_surface_id="map_name_runtime_cache",
            runtime_function="sub_4039F8 / sub_4048AC",
            draw_path="consumer unresolved; loaded into Buffer_7 and refreshed when opening SMAP",
            source_kind="dat_text",
            source_file=map_title["source_file"],
            text_role=map_title["role"],
            text_id_pattern=map_title["text_id_pattern"],
            current_slot_limit_bytes=map_title["hard_limit_bytes"],
            runtime_buffer_limit_bytes=32,
            notes="MAPNAME.DAT is confirmed to load into Buffer_7 and to be refreshed during SMAP open. The final draw-site still needs one more runtime pass, so MAPNAME stays in the DAT plan but below ITEM/WORD in implementation priority.",
        ),
        _crosswalk_entry(
            ui_surface_id="unit_death_or_battle_message",
            runtime_function="unit_death_message_consumer_unresolved",
            draw_path="unknown",
            source_kind="dat_text",
            source_file=unit_death["source_file"],
            text_role=unit_death["role"],
            text_id_pattern=unit_death["text_id_pattern"],
            current_slot_limit_bytes=unit_death["hard_limit_bytes"],
            runtime_buffer_limit_bytes=None,
            notes="UNIT.DAT death_message is a high-value long field, but its exact runtime consumer still needs one more reverse pass. This is a likely future growth target, but not the first implementation candidate.",
        ),
        _crosswalk_entry(
            ui_surface_id="magic_description_help",
            runtime_function="sub_40F650 -> sub_4142D4",
            draw_path="shared GDI bridge under stable-menu16",
            source_kind="dat_text",
            source_file=magic_desc["source_file"],
            text_role=magic_desc["role"],
            text_id_pattern=magic_desc["text_id_pattern"],
            current_slot_limit_bytes=magic_desc["hard_limit_bytes"],
            runtime_buffer_limit_bytes=80,
            notes="MAGIC.DAT desc now resolves to the shared item/magic help panel path. It mirrors ITEM.DAT desc structurally, but still remains a growth-risk field because the current help slots are fixed-size.",
        ),
    ]

    return {
        "schema_version": 1,
        "official_runtime_profile": "stable-menu16",
        "mainline_dialogue_strategy": "pack-layer native wrap and pagination for opcode 1/201",
        "surfaces": surfaces,
    }


def build_dat_ui_priority() -> dict[str, Any]:
    def field(
        source_file: str,
        field_name: str,
        *,
        ui_surfaces: list[str],
        runtime_functions: list[str],
        draw_paths: list[str],
        runtime_buffer_limit_bytes: int | list[int] | None,
        ui_value: str,
        slot_risk: str,
        growth_difficulty: str,
        recommended_strategy: str,
        notes: str,
    ) -> dict[str, Any]:
        summary = _text_field_summary(source_file, field_name)
        return {
            **summary,
            "ui_surfaces": ui_surfaces,
            "runtime_functions": runtime_functions,
            "draw_paths": draw_paths,
            "runtime_buffer_limit_bytes": runtime_buffer_limit_bytes,
            "ui_value": ui_value,
            "slot_risk": slot_risk,
            "growth_difficulty": growth_difficulty,
            "recommended_strategy": recommended_strategy,
            "notes": notes,
        }

    files = [
        {
            "source_file": "WORD.DAT",
            "tier": 1,
            "confirmed_ui_surfaces": [
                "unit_sheet_panel",
                "unit_command_or_item_menu",
                "battle_reward_popup",
                "post_battle_summary",
                "compact_unit_card",
                "equip_slot_list_and_help",
                "sortie_unit_roster",
                "item_and_class_change_preview",
            ],
            "text_fields": [
                field(
                    "WORD.DAT",
                    "name",
                    ui_surfaces=[
                        "unit_sheet_panel",
                        "unit_command_or_item_menu",
                        "battle_reward_popup",
                        "post_battle_summary",
                        "compact_unit_card",
                        "equip_slot_list_and_help",
                        "sortie_unit_roster",
                        "item_and_class_change_preview",
                    ],
                    runtime_functions=[
                        "sub_412448",
                        "sub_420564",
                        "sub_414B14",
                        "sub_414D40",
                        "sub_41C960",
                        "sub_4177E8",
                        "sub_417D9C",
                        "sub_4203C0",
                        "sub_41F27C",
                        "sub_4208CC",
                        "sub_40B188",
                        "sub_40B8F0",
                        "sub_40BB0C",
                    ],
                    draw_paths=[
                        "DrawTextA",
                        "sub_407744",
                        "stable-menu16 GDI bridge",
                    ],
                    runtime_buffer_limit_bytes=[40, 80, 256],
                    ui_value="high",
                    slot_risk="high",
                    growth_difficulty="hard",
                    recommended_strategy="compress_translation",
                    notes="WORD.DAT drives a large share of short UI labels, but the labels land in several independent runtime paths. This makes it high-value yet awkward for an early growth patch.",
                )
            ],
            "overall_ui_value": "high",
            "expansion_difficulty": "hard",
            "recommended_default": "compress_translation",
            "notes": "WORD.DAT is the biggest UI text hub. Treat it as a coverage target first, and as an expansion target only after a narrower subset proves necessary.",
        },
        {
            "source_file": "MAPNAME.DAT",
            "tier": 1,
            "confirmed_ui_surfaces": ["map_name_runtime_cache"],
            "text_fields": [
                field(
                    "MAPNAME.DAT",
                    "name",
                    ui_surfaces=["map_name_runtime_cache"],
                    runtime_functions=["sub_4039F8", "sub_4048AC"],
                    draw_paths=["consumer unresolved"],
                    runtime_buffer_limit_bytes=32,
                    ui_value="medium",
                    slot_risk="medium",
                    growth_difficulty="medium",
                    recommended_strategy="keep_fixed",
                    notes="The map-title cache is proven, but the final display function is still unresolved. Keep the field fixed until the last draw path is confirmed.",
                )
            ],
            "overall_ui_value": "medium",
            "expansion_difficulty": "medium",
            "recommended_default": "keep_fixed",
            "notes": "MAPNAME is clearly DAT-backed, but it is not the first expansion candidate because its final UI path is still one step short of proof.",
        },
        {
            "source_file": "ITEM.DAT",
            "tier": 1,
            "confirmed_ui_surfaces": [
                "unit_command_or_item_menu",
                "battle_reward_popup",
                "equip_slot_list_and_help",
                "item_magic_help_panel",
                "item_and_class_change_preview",
            ],
            "text_fields": [
                field(
                    "ITEM.DAT",
                    "name",
                    ui_surfaces=[
                        "unit_command_or_item_menu",
                        "battle_reward_popup",
                        "equip_slot_list_and_help",
                        "item_magic_help_panel",
                    ],
                    runtime_functions=["sub_420564", "sub_414B14", "sub_4177E8", "sub_417D9C", "sub_40F650", "sub_4142D4"],
                    draw_paths=["stable-menu16 GDI bridge"],
                    runtime_buffer_limit_bytes=[80, 256],
                    ui_value="high",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="compress_translation",
                    notes="Names are heavily exposed on UI surfaces, but they are still short enough that compressed translation is usually better than record growth.",
                ),
                field(
                    "ITEM.DAT",
                    "desc",
                    ui_surfaces=["equip_slot_list_and_help", "item_magic_help_panel"],
                    runtime_functions=["sub_40F4C4", "sub_40F650", "sub_4142D4"],
                    draw_paths=["shared GDI bridge under stable-menu16"],
                    runtime_buffer_limit_bytes=80,
                    ui_value="high",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="needs_pointer_rework",
                    notes="ITEM desc is the best first real expansion candidate: it is already confirmed in help panels, the field is longer than a short label, and users will want fuller Chinese text here.",
                ),
            ],
            "overall_ui_value": "high",
            "expansion_difficulty": "medium",
            "recommended_default": "needs_pointer_rework",
            "notes": "ITEM.DAT is the clearest first implementation candidate. Name fields can stay compressed, but desc should move into the first expansion prototype.",
        },
        {
            "source_file": "MAGIC.DAT",
            "tier": 1,
            "confirmed_ui_surfaces": ["post_battle_summary", "magic_description_help", "item_magic_help_panel"],
            "text_fields": [
                field(
                    "MAGIC.DAT",
                    "name",
                    ui_surfaces=["post_battle_summary"],
                    runtime_functions=["sub_414D40"],
                    draw_paths=["sub_407744"],
                    runtime_buffer_limit_bytes=256,
                    ui_value="medium",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="compress_translation",
                    notes="Magic names are already confirmed on UI, but a name expansion patch is less urgent than descriptions.",
                ),
                field(
                    "MAGIC.DAT",
                    "desc",
                    ui_surfaces=["magic_description_help", "item_magic_help_panel"],
                    runtime_functions=["sub_40F650", "sub_4142D4"],
                    draw_paths=["shared GDI bridge under stable-menu16"],
                    runtime_buffer_limit_bytes=80,
                    ui_value="medium",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="needs_pointer_rework",
                    notes="MAGIC desc now has an explicit shared help-panel consumer, which makes it a stronger second candidate after ITEM desc.",
                ),
            ],
            "overall_ui_value": "medium",
            "expansion_difficulty": "medium",
            "recommended_default": "needs_pointer_rework",
            "notes": "Use MAGIC.DAT as the second desc-expansion candidate after ITEM.DAT, because the record layout is similar and likely wants the same tooling.",
        },
        {
            "source_file": "UNIT.DAT",
            "tier": 1,
            "confirmed_ui_surfaces": [
                "unit_sheet_panel",
                "unit_command_or_item_menu",
                "post_battle_summary",
                "compact_unit_card",
                "sortie_unit_roster",
                "battle_unit_hover_name",
                "unit_death_or_battle_message",
            ],
            "text_fields": [
                field(
                    "UNIT.DAT",
                    "name",
                    ui_surfaces=[
                        "unit_sheet_panel",
                        "unit_command_or_item_menu",
                        "post_battle_summary",
                        "compact_unit_card",
                        "sortie_unit_roster",
                        "battle_unit_hover_name",
                    ],
                    runtime_functions=["sub_412448", "sub_420564", "sub_414D40", "sub_41C960", "sub_4203C0", "sub_41F27C", "sub_4208CC", "sub_413F80"],
                    draw_paths=["DrawTextA", "stable-menu16 GDI bridge"],
                    runtime_buffer_limit_bytes=256,
                    ui_value="high",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="compress_translation",
                    notes="UNIT name is everywhere, including the sortie roster and battlefield hover labels that previously stayed on the old glyph path. Chinese usually still fits if translated compactly, so coverage matters more than growth as the first fix.",
                ),
                field(
                    "UNIT.DAT",
                    "death_message",
                    ui_surfaces=["unit_death_or_battle_message"],
                    runtime_functions=["consumer unresolved"],
                    draw_paths=["unknown"],
                    runtime_buffer_limit_bytes=None,
                    ui_value="medium",
                    slot_risk="medium",
                    growth_difficulty="hard",
                    recommended_strategy="needs_runtime_patch",
                    notes="The field is long enough to matter, but the final battle/death text path is not yet pinned down. Do not start with this one.",
                ),
            ],
            "overall_ui_value": "high",
            "expansion_difficulty": "hard",
            "recommended_default": "compress_translation",
            "notes": "UNIT.DAT is a major UI source, but its growth problems are mostly about names and battle messaging. Keep it in the mapping phase; do not use it as the first expansion prototype.",
        },
        {
            "source_file": "CLASS.DAT",
            "tier": 2,
            "confirmed_ui_surfaces": ["unit_sheet_panel"],
            "text_fields": [
                field(
                    "CLASS.DAT",
                    "name",
                    ui_surfaces=["unit_sheet_panel"],
                    runtime_functions=["sub_412448"],
                    draw_paths=["sub_407744"],
                    runtime_buffer_limit_bytes=256,
                    ui_value="medium",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="compress_translation",
                    notes="Important on status UI, but usually short enough to keep fixed.",
                )
            ],
            "overall_ui_value": "medium",
            "expansion_difficulty": "medium",
            "recommended_default": "compress_translation",
            "notes": "A real UI source, but not a first-wave growth target.",
        },
        {
            "source_file": "SWNAME.DAT",
            "tier": 2,
            "confirmed_ui_surfaces": [],
            "text_fields": [
                field(
                    "SWNAME.DAT",
                    "name",
                    ui_surfaces=[],
                    runtime_functions=[],
                    draw_paths=[],
                    runtime_buffer_limit_bytes=None,
                    ui_value="low",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="keep_fixed",
                    notes="Still unconfirmed as a player-facing UI source in the current runtime pass.",
                )
            ],
            "overall_ui_value": "low",
            "expansion_difficulty": "medium",
            "recommended_default": "keep_fixed",
            "notes": "Track it, but keep it out of the first implementation wave.",
        },
        {
            "source_file": "VARNAME.DAT",
            "tier": 2,
            "confirmed_ui_surfaces": [],
            "text_fields": [
                field(
                    "VARNAME.DAT",
                    "name",
                    ui_surfaces=[],
                    runtime_functions=[],
                    draw_paths=[],
                    runtime_buffer_limit_bytes=None,
                    ui_value="low",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="keep_fixed",
                    notes="Still unconfirmed as a visible UI source in the current runtime pass.",
                )
            ],
            "overall_ui_value": "low",
            "expansion_difficulty": "medium",
            "recommended_default": "keep_fixed",
            "notes": "Keep it mapped, but do not prioritize growth work yet.",
        },
        {
            "source_file": "GEOLOGY.DAT",
            "tier": 2,
            "confirmed_ui_surfaces": [],
            "text_fields": [
                field(
                    "GEOLOGY.DAT",
                    "name",
                    ui_surfaces=[],
                    runtime_functions=[],
                    draw_paths=[],
                    runtime_buffer_limit_bytes=None,
                    ui_value="low",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="keep_fixed",
                    notes="Likely terrain/label data, but still unresolved as a concrete player-facing UI path.",
                )
            ],
            "overall_ui_value": "low",
            "expansion_difficulty": "medium",
            "recommended_default": "keep_fixed",
            "notes": "Keep it documented as a later pass item.",
        },
        {
            "source_file": "ANIME.DAT",
            "tier": 2,
            "confirmed_ui_surfaces": [],
            "text_fields": [
                field(
                    "ANIME.DAT",
                    "name",
                    ui_surfaces=[],
                    runtime_functions=[],
                    draw_paths=[],
                    runtime_buffer_limit_bytes=None,
                    ui_value="low",
                    slot_risk="high",
                    growth_difficulty="medium",
                    recommended_strategy="keep_fixed",
                    notes="Animation names are present in the fixed DAT schema, but they are not yet confirmed as a player-visible UI source worth growth work.",
                )
            ],
            "overall_ui_value": "low",
            "expansion_difficulty": "medium",
            "recommended_default": "keep_fixed",
            "notes": "Low priority for the next implementation wave.",
        },
    ]
    return {
        "schema_version": 1,
        "official_runtime_profile": "stable-menu16",
        "primary_files": ["WORD.DAT", "MAPNAME.DAT", "ITEM.DAT", "MAGIC.DAT", "UNIT.DAT"],
        "secondary_files": ["CLASS.DAT", "SWNAME.DAT", "VARNAME.DAT", "GEOLOGY.DAT", "ANIME.DAT"],
        "files": files,
        "recommended_first_growth_candidates": [
            {
                "source_file": "ITEM.DAT",
                "target_fields": ["desc"],
                "difficulty": "medium",
                "reason": "ITEM desc is already confirmed in help/detail UI and is long enough that Chinese quality suffers quickly under the fixed 70-byte slot.",
                "blockers": [
                    "SRPGEXEC.EXE loaders currently assume a fixed 640-byte record.",
                    "All desc consumers must be audited so a larger field is not re-truncated into downstream 80-byte UI buffers.",
                ],
            },
            {
                "source_file": "MAGIC.DAT",
                "target_fields": ["desc"],
                "difficulty": "medium",
                "reason": "MAGIC desc structurally mirrors ITEM desc and likely wants the same pack/runtime strategy once its UI consumer is fully nailed down.",
                "blockers": [
                    "The exact desc display path still needs one confirmed runtime consumer.",
                    "SRPGEXEC.EXE currently assumes a fixed 480-byte record.",
                ],
            },
        ],
    }


def _extract_ascii_strings(data: bytes, min_len: int = 4) -> list[str]:
    pattern = rb"[ -~]{" + str(min_len).encode("ascii") + rb",}"
    return [match.decode("ascii", errors="ignore") for match in re.findall(pattern, data)]


def _scan_imports(path: Path) -> list[dict[str, Any]]:
    if pefile is None:
        return []
    pe = pefile.PE(str(path))
    results: list[dict[str, Any]] = []
    if not hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        return results
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        dll_name = entry.dll.decode("ascii", errors="ignore")
        names = [imp.name.decode("ascii", errors="ignore") for imp in entry.imports if imp.name]
        interesting = sorted(name for name in names if name in INTERESTING_IMPORTS)
        if interesting:
            results.append({"dll": dll_name, "functions": interesting})
    return results


def _scan_binary(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    ascii_strings = _extract_ascii_strings(data)
    interesting_strings = sorted(
        value
        for value in ascii_strings
        if any(token in value.lower() for token in [".dat", ".bmp", ".mid", ".wav", "smap_", "mapc_", "drawtext", "getacp"])
    )[:120]
    return {
        "binary": path.name,
        "path": path.as_posix(),
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
        "imports": _scan_imports(path),
        "interesting_strings": interesting_strings,
    }


def build_runtime_opcode_map() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "smap_loader": {
            "function": "sub_4048AC",
            "address": "0x4048AC",
            "path_format": r"%s\MAP\SMAP_%03u.DAT",
            "event_bank_loader": {"function": "sub_40289C", "address": "0x40289C"},
        },
        "named_event_lookup": [
            {"function": "sub_40CFE0", "address": "0x40CFE0", "pattern": "EV%04d%02d"},
            {"function": "sub_40D0A4", "address": "0x40D0A4", "pattern": "OPENING"},
        ],
        "payload_buffer": {"symbol": "dword_439A30", "address": "0x439A30", "size_bytes": 0x7A120},
        "event_interpreter": {
            "function": "sub_40D18C",
            "address": "0x40D18C",
            "command_header": "opcode:u8 + length:u16 + unk:u8",
        },
        "confirmed_opcodes": [
            {
                "opcode_id": 1,
                "dispatch_address": "0x40D47F",
                "display_role": "speaker_block",
                "runtime_behavior": "Copies payload+2 to a1+24108, then scans for chained opcode 201 lines.",
            },
            {
                "opcode_id": 201,
                "dispatch_address": "0x40D532",
                "display_role": "dialogue_continuation",
                "runtime_behavior": "Consumed as a chained continuation under opcode 1, copied into 80-byte slots.",
            },
            {
                "opcode_id": 45,
                "dispatch_address": "0x40DD37",
                "display_role": "system_choice_or_prompt",
                "runtime_behavior": "Passes payload to sub_42523C, which reads a 16-bit line count then NUL-delimited strings before sub_414344 renders them.",
            },
            {
                "opcode_id": 13,
                "dispatch_address": "0x40D7A0",
                "display_role": "resource_reference",
                "runtime_behavior": "Uses payload+6 as a media path string before calling sub_4233F0.",
            },
            {
                "opcode_id": 36,
                "dispatch_address": "0x40DA68",
                "display_role": "bgm_reference",
                "runtime_behavior": "Formats %s\\BGM\\%s and calls HarmonyPlayMusic.",
            },
            {
                "opcode_id": 37,
                "dispatch_address": "0x40DA82",
                "display_role": "sound_reference",
                "runtime_behavior": "Formats %s\\EFS\\%s and calls HarmonyPlaySound.",
            },
        ],
    }


def build_runtime_text_contract() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "header_reader": {
            "function": "sub_40D18C",
            "address": "0x40D18C",
            "detail": "Reads opcode at +0, length at +1, unk byte at +3, then advances by 4.",
        },
        "opcode_contracts": [
            {
                "opcode_id": 1,
                "file_contract": "[2-byte prefix][speaker text][00]",
                "runtime_contract": "strcpy(a1 + 24108, payload + 2); draw via sub_4148A4 -> sub_407954 -> DrawTextA",
                "line_slot_size_bytes": 80,
                "strong_dialogue_mitigation": "patch-runtime --profile strong-dialogue redirects the draw truth to a heap-backed 5x512-byte side buffer while keeping the original slot only as a compatibility mirror. The formal stable-menu16 profile does not enable this experimental dialogue runtime path.",
            },
            {
                "opcode_id": 201,
                "file_contract": "[2-byte prefix][dialogue line][00]",
                "runtime_contract": "strcpy(a1 + 24108 + 80*n, payload + 2) while opcode 1 is active",
                "line_slot_size_bytes": 80,
                "strong_dialogue_mitigation": "patch-runtime --profile strong-dialogue redirects chained lines to the same heap-backed side buffer and patches sub_4148A4 to read from that buffer instead of the 80-byte draw slots. The formal stable-menu16 profile does not enable this experimental dialogue runtime path.",
            },
            {
                "opcode_id": 45,
                "file_contract": "[line_count:u16][str0][00][str1][00]...",
                "runtime_contract": "sub_42523C reads count, then strcpy(a1 + 20992 + 80*n, cursor) per line; original draw path is sub_414344 -> sub_407744 -> sub_407698",
                "line_slot_size_bytes": 80,
            },
        ],
        "menu_sprite_renderer": {
            "entry_function": "sub_407744",
            "glyph_function": "sub_407698",
            "glyph_table_symbol": "dword_43A1F4",
            "glyph_table_address": "0x43A1F4",
            "glyph_size": "12x12",
            "glyph_bytes_per_char": 24,
            "accepted_ranges": ["0x20-0xDF", "0x81-0x9F", "0xE0-0xEF", "0xF0-0xFC"],
            "resource_source": {"name": "KANJIFONT", "type": "KANJI", "loader": "sub_40A284"},
        },
        "length_ownership": [
            "commands[].length",
            "event.command_bytes_length",
            "event.declared_length",
            "event.padded_length",
            "event.chunk_chain",
        ],
        "final_draw": {
            "function": "sub_407954",
            "address": "0x407954",
            "length_source": "strlen(lpString)",
            "api": "DrawTextA",
            "api_call_address": "0x407A39",
        },
        "runtime_profiles": {
            "stable-menu16": "Formal mainline profile. It includes the opcode 45 GDI bridge, hover fixes, the 12px menu UI font, and resource aliases for BGM/EFS/BMP filename compatibility.",
            "strong-dialogue": "Experimental dialogue-safe profile. It keeps the heap-backed opcode 1/201 dialogue side buffer and patched read/write callsites separate from the formal stable-menu16 baseline.",
        },
    }


def build_runtime_encoding_chain() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "source_encoding": SOURCE_ENCODING,
        "observed_chain": [
            {"function": "__initMBCSTable", "address": "0x42DF18", "detail": "GetACP() -> _setmbcp(ACP)"},
            {
                "function": "__setmbcp",
                "address": "0x42DE28",
                "detail": "Builds CRT multibyte tables via GetCPInfo(); includes a CodePage == 932 branch.",
            },
            {"function": "sub_407954", "address": "0x407954", "detail": "Uses strlen() and DrawTextA for final draw."},
            {"function": "sub_407744", "address": "0x407744", "detail": "Original menu/system text path uses a Shift-JIS glyph renderer instead of DrawTextA."},
        ],
        "imports": [
            {"name": "GetACP", "address": "0x440214"},
            {"name": "GetCPInfo", "address": "0x440218"},
            {"name": "MultiByteToWideChar", "address": "0x440298"},
            {"name": "WideCharToMultiByte", "address": "0x4402DC"},
            {"name": "CreateFontIndirectA", "address": "0x44034C"},
            {"name": "DrawTextA", "address": "0x440430"},
        ],
        "cp936_assessment": {
            "status": "plausible_but_runtime_validation_required",
            "recommended_translation_encoding": "cp936",
            "detail": "ACP-driven ANSI text makes CP936 plausible. The verified runtime still keeps the original 80-byte per-line buffers as the formal stable-menu16 behavior, while the experimental strong-dialogue profile patches opcode 1/201 final drawing onto a heap-backed side buffer.",
        },
        "plan_a": {
            "name": "ACP/CP936 compatible path",
            "expected_benefit": "Lowest-cost path if ANSI rendering and buffers survive; opcode 45 can be bridged onto GDI without editing KANJIFONT.",
            "risks": [
                "Untranslated CP932 strings will not survive under ACP 936.",
                "Opcode 1/201 and opcode 45 still use 80-byte source slots before drawing.",
            ],
        },
        "plan_b": {
            "name": "Stronger EXE/HARMONY patch path",
            "expected_benefit": "Removes the most dangerous runtime limits and leaves room for a later Unicode-capable route. Today this remains an experimental runtime branch rather than part of the formal stable-menu16 baseline.",
            "risks": ["Higher patching cost and testing burden."],
        },
    }


def build_runtime_buffer_risks() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "buffers": [
            {
                "function": "sub_40D18C",
                "address": "0x40D47F / 0x40D532",
                "buffer_type": "state_buffer",
                "buffer_expression": "a1 + 24108 + 80*n",
                "slot_size_bytes": 80,
                "copy_function": "strcpy",
                "risk_level": "high",
                "mitigation_status": "Mitigated for final drawing by patch-runtime --profile stable-menu16; original slots remain as compatibility mirrors and may still affect unpatched callsites.",
            },
            {
                "function": "sub_42523C",
                "address": "0x42523C",
                "buffer_type": "state_buffer",
                "buffer_expression": "a1 + 20992 + 80*n",
                "slot_size_bytes": 80,
                "copy_function": "strcpy",
                "risk_level": "high",
                "mitigation_status": "Menu drawing is mitigated by the opcode 45 GDI bridge, but the original 80-byte source slots still exist.",
            },
            {
                "function": "sub_41D24C",
                "address": "0x41D24C",
                "buffer_type": "stack_buffer",
                "buffer_expression": "CHAR[1280] = 5 x 256",
                "slot_size_bytes": 256,
                "copy_function": "manual byte copy",
                "risk_level": "medium",
                "mitigation_status": "Observed as a separate runtime formatting path; not directly patched by stable-menu16 or strong-dialogue.",
            },
            {
                "function": "sub_41C960 / sub_412448",
                "address": "0x41C960 / 0x412448",
                "buffer_type": "stack_buffer",
                "buffer_expression": "Destination[256]",
                "slot_size_bytes": 256,
                "copy_function": "strcpy / sprintf / strcat",
                "risk_level": "medium",
                "mitigation_status": "Still pending deeper UI/runtime coverage.",
            },
        ],
    }


def build_dat_growth_blockers() -> dict[str, Any]:
    files = []
    for file_name, spec in FIXED_FILE_SPECS.items():
        text_fields = [field for field in spec.fields if field.kind == "text"]
        if not text_fields:
            continue
        files.append(
            {
                "source_file": file_name,
                "file_type": spec.file_type,
                "fixed_slot": True,
                "text_fields": [
                    {
                        "name": field.name,
                        "role": field.role or field.name,
                        "offset": field.offset,
                        "slot_size_bytes": field.size,
                        "hard_limit_bytes": field.size - 1,
                    }
                    for field in text_fields
                ],
            }
        )
    return {
        "schema_version": 1,
        "status": "growth_patch_not_implemented",
        "strategy": "fixed_slot_no_growth",
        "files": sorted(files, key=lambda item: item["source_file"]),
    }


def build_runtime_reports(game_dir: Path) -> dict[str, Any]:
    binaries = []
    for name in ("SRPGEXEC.EXE", "HARMONY.DLL"):
        path = game_dir / name
        if path.exists():
            binaries.append(_scan_binary(path))
    return {
        "binary_findings": binaries,
        "runtime_opcode_map": build_runtime_opcode_map(),
        "runtime_text_contract": build_runtime_text_contract(),
        "runtime_encoding_chain": build_runtime_encoding_chain(),
        "runtime_buffer_risks": build_runtime_buffer_risks(),
        "ui_dat_crosswalk": build_ui_dat_crosswalk(),
        "dat_ui_priority": build_dat_ui_priority(),
        "dat_growth_blockers": build_dat_growth_blockers(),
    }
