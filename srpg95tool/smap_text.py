from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import (
    SOURCE_ENCODING,
    bytes_to_hex,
    detect_linebreak_mode,
    encoding_profile,
    find_cp932_segments,
    length_profile,
    looks_like_resource_reference,
    read_null_terminated_text,
)


OPCODE_SPECS: dict[int, dict[str, Any]] = {
    1: {"command_category": "display", "display_role": "speaker", "extractor": "prefixed_null_text"},
    13: {"command_category": "resource", "resource_role": "bitmap_ref", "extractor": "generic_segments"},
    18: {"command_category": "noise", "extractor": "generic_segments"},
    36: {"command_category": "resource", "resource_role": "media_ref", "extractor": "generic_segments"},
    37: {"command_category": "resource", "resource_role": "sound_ref", "extractor": "generic_segments"},
    45: {"command_category": "display", "display_role": "system", "extractor": "generic_display_segments"},
    201: {"command_category": "display", "display_role": "dialogue", "extractor": "prefixed_null_text"},
}
for opcode_id in (2, 3, 4, 16, 22, 23, 24, 31, 42, 54):
    OPCODE_SPECS[opcode_id] = {"command_category": "noise", "extractor": "generic_segments"}

DISPLAY_OPCODE_SPECS: dict[int, dict[str, Any]] = {
    opcode_id: spec for opcode_id, spec in OPCODE_SPECS.items() if spec["command_category"] == "display"
}
RESOURCE_OPCODE_SPECS: dict[int, dict[str, Any]] = {
    opcode_id: spec for opcode_id, spec in OPCODE_SPECS.items() if spec["command_category"] == "resource"
}
NOISE_OPCODE_SET = {opcode_id for opcode_id, spec in OPCODE_SPECS.items() if spec["command_category"] == "noise"}


@dataclass(frozen=True)
class CommandTextExtraction:
    command_category: str
    classifier: str
    display_role: str | None
    display_segments: list[dict[str, Any]]
    resource_segments: list[dict[str, Any]]
    rejected_segments: list[dict[str, Any]]


def _contains_japanese_text(text: str) -> bool:
    return any(
        ("\u3040" <= ch <= "\u30ff") or ("\u4e00" <= ch <= "\u9fff") or ch == "\u3000"
        for ch in text
    )


def _is_ascii_symbol_noise(text: str) -> bool:
    if not text:
        return True
    if all(ord(ch) < 128 for ch in text):
        if len(text) == 1:
            return True
        stripped = text.strip()
        if not stripped:
            return True
        if all(not ch.isalnum() for ch in stripped):
            return True
    return False


def _is_high_confidence_display_text(text: str) -> bool:
    if not text:
        return False
    if looks_like_resource_reference(text):
        return False
    if _is_ascii_symbol_noise(text):
        return False
    if _contains_japanese_text(text):
        return True
    return len(text) >= 4


def _build_segment(
    *,
    payload: bytes,
    start: int,
    end: int,
    segment_index: int,
    opcode_id: int,
    command_length_field_value: int,
    display_role: str | None,
    terminator_policy: str,
) -> dict[str, Any]:
    text_bytes = payload[start:end]
    text = text_bytes.decode(SOURCE_ENCODING, errors="replace")
    return {
        "segment_index": segment_index,
        "opcode_id": opcode_id,
        "display_role": display_role,
        "payload_text_start": start,
        "payload_text_end": end,
        "payload_offset": start,
        "payload_end_offset": end,
        "byte_length": len(text_bytes),
        "text": text,
        "source_bytes_hex": bytes_to_hex(text_bytes),
        "prefix_bytes_hex": bytes_to_hex(payload[:start]),
        "suffix_bytes_hex": bytes_to_hex(payload[end:]),
        "terminator_policy": terminator_policy,
        "command_length_field_value": command_length_field_value,
        "linebreak_mode": detect_linebreak_mode(text),
        "encoding_profile": encoding_profile(text, text_bytes),
        "length_profile": length_profile(text_bytes, None, hard_limit=False, supports_length_growth=True),
    }


def _extract_prefixed_null_text(
    *,
    command_id: int,
    payload: bytes,
    command_length_field_value: int,
    display_role: str,
) -> list[dict[str, Any]]:
    if len(payload) <= 2:
        return []
    text, text_bytes, null_terminated = read_null_terminated_text(payload[2:], SOURCE_ENCODING)
    if not text_bytes:
        return []
    start = 2
    end = start + len(text_bytes)
    terminator_policy = "prefix_2_null_terminated" if null_terminated else "prefix_2_unterminated"
    return [
        _build_segment(
            payload=payload,
            start=start,
            end=end,
            segment_index=0,
            opcode_id=command_id,
            command_length_field_value=command_length_field_value,
            display_role=display_role,
            terminator_policy=terminator_policy,
        )
    ]


def _extract_generic_segments(
    *,
    command_id: int,
    payload: bytes,
    command_length_field_value: int,
    display_role: str | None,
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for candidate in find_cp932_segments(payload):
        start = candidate["payload_offset"]
        end = candidate["payload_end_offset"]
        terminator_policy = "null_delimited_scan" if end < len(payload) and payload[end] == 0 else "unterminated_scan"
        segments.append(
            _build_segment(
                payload=payload,
                start=start,
                end=end,
                segment_index=len(segments),
                opcode_id=command_id,
                command_length_field_value=command_length_field_value,
                display_role=display_role,
                terminator_policy=terminator_policy,
            )
        )
    return segments


def extract_command_texts(command_id: int, payload: bytes, command_length_field_value: int) -> CommandTextExtraction:
    spec = OPCODE_SPECS.get(command_id)
    if spec and spec["command_category"] == "display":
        if spec["extractor"] == "prefixed_null_text":
            display_segments = _extract_prefixed_null_text(
                command_id=command_id,
                payload=payload,
                command_length_field_value=command_length_field_value,
                display_role=spec["display_role"],
            )
        else:
            display_segments = [
                segment
                for segment in _extract_generic_segments(
                    command_id=command_id,
                    payload=payload,
                    command_length_field_value=command_length_field_value,
                    display_role=spec["display_role"],
                )
                if _is_high_confidence_display_text(segment["text"])
            ]
        rejected_segments = []
        if command_id == 45:
            rejected_segments = [
                segment
                for segment in _extract_generic_segments(
                    command_id=command_id,
                    payload=payload,
                    command_length_field_value=command_length_field_value,
                    display_role=spec["display_role"],
                )
                if segment["text"] and segment not in display_segments
            ]
        classifier = f"{spec['display_role']}_like"
        return CommandTextExtraction(
            command_category="display",
            classifier=classifier,
            display_role=spec["display_role"],
            display_segments=display_segments,
            resource_segments=[],
            rejected_segments=rejected_segments,
        )

    generic_segments = _extract_generic_segments(
        command_id=command_id,
        payload=payload,
        command_length_field_value=command_length_field_value,
        display_role=None,
    )

    if spec and spec["command_category"] == "resource":
        resource_segments = [segment for segment in generic_segments if looks_like_resource_reference(segment["text"])]
        rejected_segments = [segment for segment in generic_segments if segment not in resource_segments]
        return CommandTextExtraction(
            command_category="resource",
            classifier="resource_ref_like" if resource_segments else "resource_control_like",
            display_role=None,
            display_segments=[],
            resource_segments=resource_segments,
            rejected_segments=rejected_segments,
        )

    if spec and spec["command_category"] == "noise":
        return CommandTextExtraction(
            command_category="noise",
            classifier="noise_like" if generic_segments else "noise_control",
            display_role=None,
            display_segments=[],
            resource_segments=[],
            rejected_segments=generic_segments,
        )

    if generic_segments:
        return CommandTextExtraction(
            command_category="unknown",
            classifier="candidate_text_like",
            display_role=None,
            display_segments=[],
            resource_segments=[],
            rejected_segments=generic_segments,
        )
    return CommandTextExtraction(
        command_category="unknown",
        classifier="unknown",
        display_role=None,
        display_segments=[],
        resource_segments=[],
        rejected_segments=[],
    )
