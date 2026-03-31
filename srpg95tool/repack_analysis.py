from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .common import sort_paths

EVENT_CHUNK_SIZE = 100
EVENT_COMMAND_REGION_OFFSET = 104
DISPLAY_OPCODE_IDS = (1, 45, 201)
DISPLAY_LENGTH_OWNER_FIELDS = [
    "command.length",
    "event.command_bytes_length",
    "event.declared_length",
    "event.padded_length",
    "event.chunk_chain",
]
DISPLAY_REPACK_STRATEGY = "rewrite_payload_recalculate_command_event_declaration_reallocate_chunks"
DISPLAY_BUFFER_RISK_BASELINE = "high"


def padded_length_for(declared_length: int) -> int:
    if declared_length <= 0:
        return 0
    return ((declared_length + EVENT_CHUNK_SIZE - 1) // EVENT_CHUNK_SIZE) * EVENT_CHUNK_SIZE


def _choose_growth_bytes(*, declared_length: int, original_byte_length: int) -> int:
    slack_before_next_chunk = (EVENT_CHUNK_SIZE - (declared_length % EVENT_CHUNK_SIZE)) % EVENT_CHUNK_SIZE
    base_growth = max(8, min(24, max(8, original_byte_length // 2)))
    if slack_before_next_chunk and base_growth <= slack_before_next_chunk:
        return slack_before_next_chunk + 8
    return base_growth


def plan_chunk_chain_growth(
    *,
    ev_chunks: list[int],
    current_chain: list[dict[str, Any]],
    required_chunk_count: int,
) -> dict[str, Any]:
    current_chain_indices = [item["chunk_index"] for item in current_chain]
    free_chunk_indices = [chunk_index for chunk_index, next_chunk in enumerate(ev_chunks) if next_chunk == -1]
    additional_chunk_count = max(0, required_chunk_count - len(current_chain_indices))
    plan_possible = additional_chunk_count <= len(free_chunk_indices)

    planned_chain_indices = current_chain_indices[:required_chunk_count]
    if additional_chunk_count and plan_possible:
        planned_chain_indices.extend(free_chunk_indices[:additional_chunk_count])

    planned_chain = []
    if plan_possible:
        for index, chunk_index in enumerate(planned_chain_indices):
            planned_chain.append(
                {
                    "chunk_index": chunk_index,
                    "next_chunk": planned_chain_indices[index + 1] if index + 1 < len(planned_chain_indices) else -2,
                }
            )

    return {
        "required_chunk_count": required_chunk_count,
        "current_chunk_count": len(current_chain_indices),
        "additional_chunk_count": additional_chunk_count,
        "free_chunk_count_before": len(free_chunk_indices),
        "plan_possible": plan_possible,
        "planned_chunk_chain": planned_chain,
    }


def simulate_display_growth(
    *,
    smap_export: dict[str, Any],
    event_export: dict[str, Any],
    command: dict[str, Any],
    segment: dict[str, Any],
) -> dict[str, Any]:
    growth_bytes = _choose_growth_bytes(
        declared_length=event_export["declared_length"],
        original_byte_length=segment["byte_length"],
    )
    original_command_length = command["length"]
    original_command_total_length = command.get("command_total_length", 4 + original_command_length)
    new_command_length = original_command_length + growth_bytes
    new_command_total_length = original_command_total_length + growth_bytes

    original_command_bytes_length = event_export["command_bytes_length"]
    new_command_bytes_length = original_command_bytes_length + growth_bytes
    original_declared_length = event_export["declared_length"]
    new_declared_length = original_declared_length + growth_bytes
    original_padded_length = event_export["padded_length"]
    new_padded_length = padded_length_for(new_declared_length)

    chunk_plan = plan_chunk_chain_growth(
        ev_chunks=smap_export["ev_chunks"],
        current_chain=event_export["chunk_chain"],
        required_chunk_count=(new_padded_length // EVENT_CHUNK_SIZE),
    )
    no_truncation = chunk_plan["plan_possible"]
    self_consistent = (
        new_command_total_length == 4 + new_command_length
        and new_command_bytes_length == original_command_bytes_length + growth_bytes
        and new_declared_length == original_declared_length + growth_bytes
        and new_padded_length == padded_length_for(new_declared_length)
        and (not no_truncation or len(chunk_plan["planned_chunk_chain"]) == chunk_plan["required_chunk_count"])
    )

    return {
        "source_file": event_export["source_file"],
        "event_id": event_export["event_id"],
        "event_name_internal": event_export["event_name_internal"],
        "command_index": command["command_index"],
        "opcode_id": command["command_id"],
        "display_role": segment.get("display_role"),
        "text_id": segment.get("text_id"),
        "original_text_preview": segment.get("text", "")[:80],
        "growth_bytes": growth_bytes,
        "original_command_length": original_command_length,
        "simulated_command_length": new_command_length,
        "original_command_total_length": original_command_total_length,
        "simulated_command_total_length": new_command_total_length,
        "original_command_bytes_length": original_command_bytes_length,
        "simulated_command_bytes_length": new_command_bytes_length,
        "original_declared_length": original_declared_length,
        "simulated_declared_length": new_declared_length,
        "original_padded_length": original_padded_length,
        "simulated_padded_length": new_padded_length,
        "original_chunk_count": len(event_export["chunk_chain"]),
        "simulated_chunk_count": chunk_plan["required_chunk_count"],
        "chunk_plan": chunk_plan,
        "length_owner_fields": list(DISPLAY_LENGTH_OWNER_FIELDS),
        "repack_strategy": DISPLAY_REPACK_STRATEGY,
        "buffer_risk": DISPLAY_BUFFER_RISK_BASELINE,
        "no_truncation": no_truncation,
        "self_consistent": self_consistent,
        "notes": [
            "Simulation uses cp932-safe byte growth only; Chinese rendering still depends on EXE/HARMONY text-path patching.",
            "No silent truncation is allowed; failure means chunk reallocation capacity must be addressed first.",
        ],
    }


def build_repack_readiness_report(out_dir: Path) -> dict[str, Any]:
    out_dir = out_dir.resolve()
    sample_limit_per_opcode = 3
    sample_count_by_opcode = {opcode_id: 0 for opcode_id in DISPLAY_OPCODE_IDS}
    smap_chunk_summaries: list[dict[str, Any]] = []
    sample_growth_simulations: list[dict[str, Any]] = []

    smap_dirs = sort_paths([path for path in (out_dir / "maps").iterdir() if path.is_dir() and path.name.startswith("SMAP_")])
    for smap_dir in smap_dirs:
        smap_export = json.loads((smap_dir / "smap.json").read_text(encoding="utf-8"))
        free_chunk_count = sum(1 for next_chunk in smap_export["ev_chunks"] if next_chunk == -1)
        smap_chunk_summaries.append(
            {
                "map_id": smap_export["map_id"],
                "source_file": smap_export["source_file"],
                "free_chunk_count": free_chunk_count,
                "used_chunk_slot_count": len(smap_export["ev_chunks"]) - free_chunk_count,
                "event_count": len(smap_export["event_declarations"]),
            }
        )

        if all(count >= sample_limit_per_opcode for count in sample_count_by_opcode.values()):
            continue

        event_paths = sort_paths(list((smap_dir / "events").glob("*.json")))
        for event_path in event_paths:
            event_export = json.loads(event_path.read_text(encoding="utf-8"))
            for command in event_export["commands"]:
                opcode_id = command["command_id"]
                if opcode_id not in DISPLAY_OPCODE_IDS:
                    continue
                if sample_count_by_opcode[opcode_id] >= sample_limit_per_opcode:
                    continue
                display_segments = command.get("display_segments", [])
                if not display_segments:
                    continue
                sample_growth_simulations.append(
                    simulate_display_growth(
                        smap_export=smap_export,
                        event_export=event_export,
                        command=command,
                        segment=display_segments[0],
                    )
                )
                sample_count_by_opcode[opcode_id] += 1
                if all(count >= sample_limit_per_opcode for count in sample_count_by_opcode.values()):
                    break
            if all(count >= sample_limit_per_opcode for count in sample_count_by_opcode.values()):
                break

    return {
        "schema_version": 1,
        "event_chunk_size": EVENT_CHUNK_SIZE,
        "event_command_region_offset": EVENT_COMMAND_REGION_OFFSET,
        "display_length_owner_fields": list(DISPLAY_LENGTH_OWNER_FIELDS),
        "display_repack_strategy": DISPLAY_REPACK_STRATEGY,
        "display_buffer_risk_baseline": DISPLAY_BUFFER_RISK_BASELINE,
        "opcode_sample_coverage": sample_count_by_opcode,
        "sample_count": len(sample_growth_simulations),
        "all_samples_self_consistent": all(item["self_consistent"] for item in sample_growth_simulations),
        "all_samples_no_truncation": all(item["no_truncation"] for item in sample_growth_simulations),
        "smap_chunk_pool": smap_chunk_summaries,
        "sample_growth_simulations": sample_growth_simulations,
        "assumptions": [
            "Variable-length growth is modeled at the byte level, not as a finalized Chinese string patch.",
            "Command payload edits for opcodes 1, 45, and 201 must propagate through command.length, event.command_bytes_length, event.declared_length, padded_length, and chunk_chain.",
            "Passing the storage-layer simulation does not remove EXE/HARMONY ANSI-path risks.",
        ],
    }
