from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


DIALOGUE_LINE_LIMIT_BYTES = 79
DIALOGUE_BODY_LINES_PER_PAGE = 4

_BREAK_CHARS = set(" \t-.,!?;:)]}>,，。！？；：、）】》〉」』】")


@dataclass(frozen=True)
class DialogueBlock:
    dialogue_id: str
    source_file: str
    map_id: str
    event_id: str
    event_name_internal: str
    start_command_index: int
    end_command_index: int
    speaker_command: dict[str, Any]
    body_commands: list[dict[str, Any]]


def build_dialogue_id(map_id: str, event_name_internal: str, start_command_index: int) -> str:
    return f"smap:{map_id}:event:{event_name_internal}:dialogue:{start_command_index:06d}"


def iter_dialogue_blocks(
    *,
    map_id: str,
    source_file: str,
    event_id: str,
    event_name_internal: str,
    commands: list[dict[str, Any]],
) -> list[DialogueBlock]:
    blocks: list[DialogueBlock] = []
    index = 0
    while index < len(commands):
        command = commands[index]
        display_segments = command.get("display_segments", [])
        if command.get("command_id") != 1 or not display_segments:
            index += 1
            continue
        start = index
        body_commands: list[dict[str, Any]] = []
        index += 1
        while index < len(commands):
            chained = commands[index]
            if chained.get("command_id") != 201 or not chained.get("display_segments", []):
                break
            body_commands.append(chained)
            index += 1
        blocks.append(
            DialogueBlock(
                dialogue_id=build_dialogue_id(map_id, event_name_internal, start),
                source_file=source_file,
                map_id=map_id,
                event_id=event_id,
                event_name_internal=event_name_internal,
                start_command_index=start,
                end_command_index=index - 1,
                speaker_command=command,
                body_commands=body_commands,
            )
        )
    return blocks


def dialogue_catalog_name(source_file: str) -> str:
    return f"{Path(source_file).stem.lower()}.jsonl"


def build_dialogue_catalog_row(block: DialogueBlock) -> dict[str, Any]:
    speaker_segment = block.speaker_command["display_segments"][0]
    body_segments = [command["display_segments"][0] for command in block.body_commands]
    return {
        "dialogue_id": block.dialogue_id,
        "source_file": block.source_file,
        "map_id": block.map_id,
        "event_id": block.event_id,
        "event_name_internal": block.event_name_internal,
        "start_command_index": block.start_command_index,
        "end_command_index": block.end_command_index,
        "speaker_text_id": speaker_segment["text_id"],
        "body_text_ids": [segment["text_id"] for segment in body_segments],
        "original_speaker": speaker_segment["text"],
        "original_body_lines": [segment["text"] for segment in body_segments],
        "original_body": "\n".join(segment["text"] for segment in body_segments),
        "translation_speaker": "",
        "translation_body": "",
        "source_encoding": "cp932",
        "translation_status": "untranslated",
        "line_limit_bytes": DIALOGUE_LINE_LIMIT_BYTES,
        "body_lines_per_page": DIALOGUE_BODY_LINES_PER_PAGE,
        "layout_policy": "auto_wrap_and_paginate",
        "page_repeat_speaker": True,
    }


def encode_cp936(text: str) -> bytes:
    return text.encode("cp936")


def encoded_len_cp936(text: str) -> int:
    return len(encode_cp936(text))


def wrap_dialogue_body(
    body_text: str,
    *,
    line_limit_bytes: int = DIALOGUE_LINE_LIMIT_BYTES,
    body_lines_per_page: int = DIALOGUE_BODY_LINES_PER_PAGE,
) -> tuple[list[list[str]], dict[str, bool]]:
    flags = {
        "line_wrap_applied": False,
        "auto_pagination_applied": False,
        "manual_page_break_applied": "\f" in body_text,
        "body_token_split": False,
    }
    manual_pages = body_text.split("\f")
    pages: list[list[str]] = []
    for manual_page in manual_pages:
        wrapped_lines: list[str] = []
        for manual_line in manual_page.split("\n"):
            line_parts, token_split = _wrap_single_line(manual_line, line_limit_bytes)
            if len(line_parts) > 1:
                flags["line_wrap_applied"] = True
            if token_split:
                flags["body_token_split"] = True
            wrapped_lines.extend(line_parts)
        if not wrapped_lines:
            wrapped_lines = [""]
        for start in range(0, len(wrapped_lines), body_lines_per_page):
            page_lines = wrapped_lines[start : start + body_lines_per_page]
            if start:
                flags["auto_pagination_applied"] = True
            pages.append(page_lines)
    if not pages:
        pages = [[""]]
    return pages, flags


def _wrap_single_line(line: str, line_limit_bytes: int) -> tuple[list[str], bool]:
    if line == "":
        return [""], False
    parts: list[str] = []
    token_split = False
    remaining = line
    while remaining:
        if encoded_len_cp936(remaining) <= line_limit_bytes:
            parts.append(remaining)
            break
        cut_index, split_token = _find_wrap_index(remaining, line_limit_bytes)
        token_split = token_split or split_token
        chunk = remaining[:cut_index]
        parts.append(chunk.rstrip() if chunk.rstrip() else chunk)
        remaining = remaining[cut_index:]
        if chunk and chunk[-1].isspace():
            remaining = remaining.lstrip()
    return parts, token_split


def _find_wrap_index(text: str, line_limit_bytes: int) -> tuple[int, bool]:
    used_bytes = 0
    max_index = 0
    for index, char in enumerate(text):
        char_bytes = encode_cp936(char)
        if used_bytes + len(char_bytes) > line_limit_bytes:
            break
        used_bytes += len(char_bytes)
        max_index = index + 1
    if max_index <= 0:
        raise ValueError("Unable to fit any character into a dialogue line")
    if max_index >= len(text):
        return len(text), False
    for index in range(max_index, 0, -1):
        if text[index - 1].isspace() or text[index - 1] in _BREAK_CHARS:
            return index, False
    split_token = _looks_like_ascii_token(text[:max_index]) and _looks_like_ascii_token(text[max_index : max_index + 1])
    return max_index, split_token


def _looks_like_ascii_token(text: str) -> bool:
    if not text:
        return False
    return all(ord(char) < 128 and not char.isspace() for char in text)
