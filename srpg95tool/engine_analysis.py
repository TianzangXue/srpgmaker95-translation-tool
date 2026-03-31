from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import SOURCE_ENCODING, file_sha256

try:
    import pefile  # type: ignore
except Exception:  # pragma: no cover - optional dependency
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
    "wsprintfA",
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
        funcs = [imp.name.decode("ascii", errors="ignore") for imp in entry.imports if imp.name]
        interesting = sorted(func for func in funcs if func in INTERESTING_IMPORTS)
        if interesting:
            results.append({"dll": dll_name, "functions": interesting})
    return results


def _scan_binary(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    ascii_strings = _extract_ascii_strings(data)
    interesting_strings = sorted(
        {
            value
            for value in ascii_strings
            if any(
                token in value.lower()
                for token in [
                    "drawtext",
                    "textout",
                    "createfont",
                    "getacp",
                    "getcpinfo",
                    "multibytetowidechar",
                    "widechartomultibyte",
                    "lstrcpy",
                    "lstrlen",
                    ".dat",
                    ".bmp",
                    ".mid",
                    ".wav",
                    "smap_",
                    "mapc_",
                ]
            )
        }
    )
    return {
        "binary": path.name,
        "path": path.as_posix(),
        "size": path.stat().st_size,
        "sha256": file_sha256(path),
        "imports": _scan_imports(path),
        "interesting_strings": interesting_strings[:120],
    }


def build_text_flow_report(game_dir: Path) -> dict[str, Any]:
    binaries = []
    for name in ("SRPGEXEC.EXE", "HARMONY.DLL"):
        path = game_dir / name
        if path.exists():
            binaries.append(_scan_binary(path))

    return {
        "schema_version": 1,
        "source_encoding_assumption": SOURCE_ENCODING,
        "event_storage": {
            "smap_path_pattern": r"%s\MAP\SMAP_%03u.DAT",
            "mapc_path_pattern": r"%s\MAP\MAPC_%03u.DAT",
            "smap_static_region_size": 120800,
            "event_chunk_size": 100,
            "chunk_table_offset": 20800,
            "event_declaration_offset": 40800,
            "event_region_offset": 120800,
        },
        "confirmed_display_opcodes": [
            {
                "opcode_id": 1,
                "display_role": "speaker",
                "payload_layout": "[2-byte prefix][cp932 speaker text][00]",
                "terminator_policy": "payload text is NUL-terminated after a 2-byte prefix",
                "length_growth_owner_fields": ["command.length", "event.command_bytes_length", "event.declared_length", "event.padded_length", "event.chunk_chain"],
            },
            {
                "opcode_id": 45,
                "display_role": "system",
                "payload_layout": "heuristic multi-segment payload; one command may carry multiple null-delimited cp932 strings",
                "terminator_policy": "payload contains one or more null-delimited cp932 strings; command header length still bounds the payload",
                "length_growth_owner_fields": ["command.length", "event.command_bytes_length", "event.declared_length", "event.padded_length", "event.chunk_chain"],
            },
            {
                "opcode_id": 201,
                "display_role": "dialogue",
                "payload_layout": "[2-byte prefix][cp932 dialogue text][00]",
                "terminator_policy": "payload text is NUL-terminated after a 2-byte prefix",
                "length_growth_owner_fields": ["command.length", "event.command_bytes_length", "event.declared_length", "event.padded_length", "event.chunk_chain"],
            },
        ],
        "binary_findings": binaries,
        "runtime_flow": [
            {
                "stage": "filesystem_open",
                "confidence": "high",
                "detail": "SRPGEXEC.EXE formats MAP/SMAP_%03u.DAT and MAP/MAPC_%03u.DAT paths and opens them directly from disk.",
                "evidence": [r"%s\MAP\SMAP_%03u.DAT", r"%s\MAP\MAPC_%03u.DAT"],
            },
            {
                "stage": "chunk_reassembly",
                "confidence": "high",
                "detail": "SMAP event bytes are reconstructed from 100-byte chunk chains rooted by declaration.first_chunk and bounded by declaration.declared_length.",
                "evidence": ["SMAP static region layout", "event chunk size 100", "chunk table + declaration table + event region offsets"],
            },
            {
                "stage": "command_parse",
                "confidence": "high",
                "detail": "Each command starts with opcode(1 byte) + length(2 bytes) + unk(1 byte), and payload bytes are bounded by the command-local length field.",
                "evidence": ["command header schema in unpack exports", "payload_hex roundtrip behavior"],
            },
            {
                "stage": "opcode_text_decode",
                "confidence": "high",
                "detail": "Display text currently lands in opcodes 1, 45, and 201, with payload-local NUL termination rules matching the extracted segments.",
                "evidence": ["confirmed display opcodes", "segment terminator_policy", "sample payload layouts"],
            },
            {
                "stage": "ansi_render_chain",
                "confidence": "medium",
                "detail": "Final text rendering flows through ANSI-oriented helpers and rendering APIs visible in SRPGEXEC.EXE and HARMONY.DLL imports.",
                "evidence": ["GetACP", "GetCPInfo", "MultiByteToWideChar", "WideCharToMultiByte", "CreateFontIndirectA", "DrawTextA"],
            },
        ],
        "display_contract": {
            "display_interface_candidates": ["DrawTextA", "TextOutA"],
            "font_path_candidates": ["CreateFontIndirectA"],
            "storage_termination_model": {
                "opcode_1": "NUL-terminated string after 2-byte prefix",
                "opcode_45": "command-length-bounded payload with one or more null-delimited strings",
                "opcode_201": "NUL-terminated string after 2-byte prefix",
            },
            "final_render_argument_model": {
                "confidence": "low",
                "status": "not yet fully proven from disassembly",
                "current_inference": "Runtime likely reaches ANSI APIs with a classic NUL-terminated string or a wrapper that computes length just before DrawTextA/TextOutA. Explicit final length passing is still unconfirmed.",
            },
        },
        "engine_inferences": [
            {
                "id": "srpgexec_loads_smap_dat",
                "confidence": "high",
                "finding": "SRPGEXEC.EXE directly opens MAPC/SMAP DAT files by formatted filesystem paths.",
                "evidence": [r"%s\MAP\SMAP_%03u.DAT", r"%s\MAP\MAPC_%03u.DAT"],
            },
            {
                "id": "ansi_codepage_path_present",
                "confidence": "high",
                "finding": "The engine uses ACP-aware ANSI conversion helpers before rendering text.",
                "evidence": ["GetACP", "GetCPInfo", "MultiByteToWideChar", "WideCharToMultiByte", "lstrcpyA", "lstrlenA"],
            },
            {
                "id": "ansi_text_rendering_present",
                "confidence": "high",
                "finding": "Both SRPGEXEC.EXE and HARMONY.DLL import ANSI font and text rendering APIs.",
                "evidence": ["CreateFontIndirectA", "DrawTextA"],
            },
            {
                "id": "length_growth_not_only_packer_problem",
                "confidence": "high",
                "finding": "Variable-length repacking is structurally possible in SMAP, but Chinese display safety also depends on the EXE/DLL ANSI text path.",
                "evidence": ["opcode 1/45/201 length ownership", "ANSI import chain in SRPGEXEC.EXE/HARMONY.DLL"],
            },
        ],
        "display_pipeline": [
            "SRPGEXEC.EXE opens MAP/SMAP_%03u.DAT and reconstructs chunked event payloads.",
            "Display text is stored inside SMAP event command payloads, primarily in opcodes 1, 45, and 201.",
            "Command-local text growth changes command.length, which in turn changes event.command_bytes_length and event.declared_length.",
            "Event growth may increase padded_length and requires chunk-chain reallocation in 100-byte units.",
            "The runtime text path remains ANSI-oriented and ends in DrawTextA/CreateFontIndirectA imports visible in SRPGEXEC.EXE and HARMONY.DLL.",
        ],
        "growth_risk_points": [
            {
                "layer": "event_storage",
                "risk": "low",
                "detail": "Chunked event storage can grow as long as declaration length and chunk allocation are updated consistently.",
            },
            {
                "layer": "text_encoding",
                "risk": "high",
                "detail": "Chinese text may not survive the current cp932/ACP path without a locale strategy or binary patch.",
            },
            {
                "layer": "render_buffers",
                "risk": "high",
                "detail": "Actual stack/static buffer sizes are still not proven from disassembly; future EXE/HARMONY patch work must verify them before Chinese rollout.",
            },
        ],
        "verification_gaps": [
            "The exact function that interprets opcodes 1/45/201 inside SRPGEXEC.EXE is not yet symbol-resolved.",
            "The final DrawTextA/TextOutA caller contract is inferred from imports, not proven by traced arguments.",
            "No fixed stack/static buffer size has been confirmed yet for the last text staging buffer.",
        ],
        "next_patch_targets": [
            {
                "binary": "SRPGEXEC.EXE",
                "target": "SMAP event interpreter callers for opcodes 1/45/201",
                "reason": "Confirm command parsing and where expanded command lengths are consumed.",
            },
            {
                "binary": "SRPGEXEC.EXE",
                "target": "GetACP/GetCPInfo/MultiByteToWideChar call chain",
                "reason": "Decide whether the final translation route can stay ANSI or needs a stronger patch.",
            },
            {
                "binary": "HARMONY.DLL",
                "target": "DrawTextA/CreateFontIndirectA call chain",
                "reason": "Confirm final rendering width assumptions and any fixed local buffers before Chinese patching.",
            },
        ],
    }
