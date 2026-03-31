"""Public CLI entrypoints for the SRPG MAKER 95 toolchain.

This module intentionally stays thin: it defines the stable command surface
and forwards execution into the unpack/pack/runtime/workflow layers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .common import TOOL_VERSION
from .pack import inspect_runtime, pack_game, simulate_pack
from .runtime_patch import SUPPORTED_PATCH_PROFILES, patch_runtime
from .unpack import inspect_export, unpack_game, verify_roundtrip
from .workflow import (
    DEFAULT_IMPORT_MODE,
    DEFAULT_ZH_SEED_MODE,
    SUPPORTED_IMPORT_MODES,
    SUPPORTED_ZH_SEED_MODES,
    build_project,
    doctor_project,
    import_project_txt,
    init_project,
)


def emit_json(payload: dict) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True, indent=2) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="srpg95tool", description="SRPG MAKER 95 unpack and verification tool")
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    unpack_parser = subparsers.add_parser("unpack", help="Unpack a SRPG95 game into structured JSON outputs")
    unpack_parser.add_argument("game_dir", type=Path)
    unpack_parser.add_argument("out_dir", type=Path)
    unpack_parser.add_argument("--reference-map", dest="reference_map", type=Path, default=None)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect an existing unpack output directory")
    inspect_parser.add_argument("out_dir", type=Path)

    verify_parser = subparsers.add_parser("verify-roundtrip", help="Verify that unpacked JSON roundtrips back to the original DAT bytes")
    verify_parser.add_argument("game_dir", type=Path)
    verify_parser.add_argument("out_dir", type=Path)

    pack_parser = subparsers.add_parser("pack", help="Pack translated SRPG95 exports back into game DAT files")
    pack_parser.add_argument("game_dir", type=Path)
    pack_parser.add_argument("unpack_dir", type=Path)
    pack_parser.add_argument("out_dir", type=Path)

    simulate_parser = subparsers.add_parser("simulate-pack", help="Simulate pack planning without writing game files")
    simulate_parser.add_argument("unpack_dir", type=Path)

    runtime_parser = subparsers.add_parser("inspect-runtime", help="Inspect SRPG95 runtime text and encoding paths")
    runtime_parser.add_argument("game_dir", type=Path)

    patch_runtime_parser = subparsers.add_parser("patch-runtime", help="Copy a game directory and apply runtime patch profiles for menu and dialogue rendering")
    patch_runtime_parser.add_argument("game_dir", type=Path)
    patch_runtime_parser.add_argument("out_dir", type=Path)
    patch_runtime_parser.add_argument("--profile", choices=SUPPORTED_PATCH_PROFILES, default="stable-menu16")

    project_parser = subparsers.add_parser("project", help="Run the integrated SRPG95 translation workflow")
    project_subparsers = project_parser.add_subparsers(dest="project_command", required=True)

    project_init_parser = project_subparsers.add_parser("init", help="Initialize a translation workspace with machine exports and TXT sources")
    project_init_parser.add_argument("game_dir", type=Path)
    project_init_parser.add_argument("workspace_dir", type=Path)
    project_init_parser.add_argument("--zh-seed", choices=SUPPORTED_ZH_SEED_MODES, default=DEFAULT_ZH_SEED_MODE)

    project_import_parser = project_subparsers.add_parser("import-txt", help="Import translated TXT files back into machine JSONL catalogs")
    project_import_parser.add_argument("workspace_dir", type=Path)
    project_import_parser.add_argument("--import-mode", choices=SUPPORTED_IMPORT_MODES, default=DEFAULT_IMPORT_MODE)

    project_doctor_parser = project_subparsers.add_parser("doctor", help="Validate TXT consistency and simulate a pack without building a game directory")
    project_doctor_parser.add_argument("workspace_dir", type=Path)
    project_doctor_parser.add_argument("--import-mode", choices=SUPPORTED_IMPORT_MODES, default=DEFAULT_IMPORT_MODE)

    project_build_parser = project_subparsers.add_parser("build", help="Import translated TXT, pack game files, and apply the stable runtime patch")
    project_build_parser.add_argument("game_dir", type=Path)
    project_build_parser.add_argument("workspace_dir", type=Path)
    project_build_parser.add_argument("out_dir", type=Path)
    project_build_parser.add_argument("--import-mode", choices=SUPPORTED_IMPORT_MODES, default=DEFAULT_IMPORT_MODE)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "unpack":
        manifest = unpack_game(args.game_dir, args.out_dir, args.reference_map)
        emit_json({"status": "ok", "command": "unpack", "manifest": manifest})
        return 0
    if args.command == "inspect":
        inspected = inspect_export(args.out_dir)
        emit_json({"status": "ok", "command": "inspect", **inspected})
        return 0
    if args.command == "verify-roundtrip":
        summary = verify_roundtrip(args.game_dir, args.out_dir)
        emit_json({"status": "ok" if summary["all_matched"] else "mismatch", "command": "verify-roundtrip", **summary})
        return 0 if summary["all_matched"] else 1
    if args.command == "pack":
        result = pack_game(args.game_dir, args.unpack_dir, args.out_dir)
        emit_json({"status": result["status"], "command": "pack", **result})
        return 0 if result["status"] == "ok" else 1
    if args.command == "simulate-pack":
        result = simulate_pack(args.unpack_dir)
        emit_json({"status": result["status"], "command": "simulate-pack", **result})
        return 0 if result["status"] == "ok" else 1
    if args.command == "inspect-runtime":
        result = inspect_runtime(args.game_dir)
        emit_json({"status": "ok", "command": "inspect-runtime", **result})
        return 0
    if args.command == "patch-runtime":
        result = patch_runtime(args.game_dir, args.out_dir, args.profile)
        emit_json({"status": result["status"], "command": "patch-runtime", **result})
        return 0 if result["status"] == "ok" else 1
    if args.command == "project":
        if args.project_command == "init":
            result = init_project(args.game_dir, args.workspace_dir, args.zh_seed)
            emit_json({"status": result["status"], "command": "project init", **result})
            return 0 if result["status"] == "ok" else 1
        if args.project_command == "import-txt":
            result = import_project_txt(args.workspace_dir, args.import_mode)
            status = "ok" if result["txt_consistency"]["status"] == "ok" and result["txt_import_result"]["status"] == "ok" else "error"
            emit_json({"status": status, "command": "project import-txt", **result})
            return 0 if status == "ok" else 1
        if args.project_command == "doctor":
            result = doctor_project(args.workspace_dir, args.import_mode)
            emit_json({"status": result["status"], "command": "project doctor", **result})
            return 0 if result["status"] == "ok" else 1
        if args.project_command == "build":
            result = build_project(args.game_dir, args.workspace_dir, args.out_dir, args.import_mode)
            emit_json({"status": result["status"], "command": "project build", **result})
            return 0 if result["status"] == "ok" else 1
        parser.error(f"Unsupported project command: {args.project_command}")
        return 2
    parser.error(f"Unsupported command: {args.command}")
    return 2
