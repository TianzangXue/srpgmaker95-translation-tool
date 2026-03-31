from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SOURCE_ENCODING = "cp932"
TOOL_VERSION = "0.1.0"

_CONTROL_BYTES = set(range(0x20)) - {0x09, 0x0A, 0x0D}
_RESOURCE_SUFFIXES = (".mid", ".wav", ".bmp", ".dat", ".avi", ".dll", ".exe")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def bytes_to_hex(data: bytes) -> str:
    return data.hex()


def hex_to_bytes(value: str) -> bytes:
    return bytes.fromhex(value) if value else b""


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_null_terminated_text(raw: bytes, encoding: str = SOURCE_ENCODING) -> tuple[str, bytes, bool]:
    if b"\x00" in raw:
        used = raw.split(b"\x00", 1)[0]
        null_terminated = True
    else:
        used = raw
        null_terminated = False
    return used.decode(encoding, errors="replace"), used, null_terminated


def detect_linebreak_mode(text: str) -> str:
    has_crlf = "\r\n" in text
    has_lf = "\n" in text.replace("\r\n", "")
    has_cr = "\r" in text.replace("\r\n", "")
    if has_crlf and not has_lf and not has_cr:
        return "crlf"
    if has_lf and not has_crlf and not has_cr:
        return "lf"
    if has_cr and not has_crlf and not has_lf:
        return "cr"
    if has_crlf or has_lf or has_cr:
        return "mixed"
    return "none"


def contains_control_bytes(data: bytes) -> bool:
    return any(byte in _CONTROL_BYTES for byte in data)


def encoding_profile(text: str, text_bytes: bytes, source_encoding: str = SOURCE_ENCODING) -> dict[str, Any]:
    try:
        roundtrip_ok = text.encode(source_encoding) == text_bytes
    except UnicodeEncodeError:
        roundtrip_ok = False
    return {
        "source_encoding": source_encoding,
        "source_roundtrip_ok": roundtrip_ok,
        "contains_multiline": ("\n" in text) or ("\r" in text),
        "contains_fullwidth_space": "\u3000" in text,
        "contains_control_bytes": contains_control_bytes(text_bytes),
    }


def length_profile(
    text_bytes: bytes,
    max_bytes: int | None,
    *,
    hard_limit: bool,
    supports_length_growth: bool,
) -> dict[str, Any]:
    original_byte_length = len(text_bytes)
    if max_bytes is None:
        soft_threshold = None
        risk = "medium" if supports_length_growth else "unknown"
    else:
        soft_threshold = max(1, int(max_bytes * 0.8))
        if original_byte_length >= max_bytes:
            risk = "high"
        elif original_byte_length >= soft_threshold:
            risk = "medium"
        else:
            risk = "low"
    return {
        "original_byte_length": original_byte_length,
        "max_bytes": max_bytes,
        "hard_limit": hard_limit,
        "soft_warning_threshold": soft_threshold,
        "estimated_translation_risk": risk,
    }


def json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def jsonl_dump(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sanitize_name(value: str) -> str:
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", value.strip())
    safe = safe.strip("._")
    return safe or "unnamed"


def looks_like_resource_reference(text: str) -> bool:
    lowered = text.lower()
    return any(suffix in lowered for suffix in _RESOURCE_SUFFIXES)


def looks_like_text(text: str, data: bytes) -> bool:
    if not text:
        return False
    if any(ch == "\ufffd" for ch in text):
        return False
    if all(ch.isspace() for ch in text):
        return False
    control_chars = [ch for ch in text if ord(ch) < 0x20 and ch not in "\r\n\t"]
    if control_chars:
        return False
    if len(data) == 1 and data[0] < 0x20:
        return False
    return True


def find_cp932_segments(payload: bytes) -> list[dict[str, Any]]:
    candidates: list[tuple[int, int, bytes, str]] = []
    for start in range(len(payload)):
        if payload[start] == 0:
            continue
        end = payload.find(b"\x00", start)
        if end < 0:
            end = len(payload)
        chunk = payload[start:end]
        if not chunk:
            continue
        try:
            text = chunk.decode(SOURCE_ENCODING)
        except UnicodeDecodeError:
            continue
        if not looks_like_text(text, chunk):
            continue
        candidates.append((start, end, chunk, text))
    candidates.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    accepted: list[tuple[int, int, bytes, str]] = []
    for candidate in candidates:
        start, end, _, _ = candidate
        if any(not (end <= existing_start or start >= existing_end) for existing_start, existing_end, _, _ in accepted):
            continue
        accepted.append(candidate)
    accepted.sort(key=lambda item: item[0])
    segments: list[dict[str, Any]] = []
    for index, (start, end, chunk, text) in enumerate(accepted):
        segments.append(
            {
                "segment_index": index,
                "payload_offset": start,
                "payload_end_offset": end,
                "byte_length": len(chunk),
                "text": text,
                "source_bytes_hex": bytes_to_hex(chunk),
                "linebreak_mode": detect_linebreak_mode(text),
                "encoding_profile": encoding_profile(text, chunk),
                "length_profile": length_profile(chunk, len(chunk), hard_limit=False, supports_length_growth=True),
            }
        )
    return segments


def build_text_object(
    *,
    text_id: str,
    role: str,
    field_bytes: bytes,
    text_bytes: bytes,
    text: str,
    max_bytes: int | None,
    is_fixed_size: bool,
    null_terminated: bool,
    padding_byte: int | None,
    supports_length_growth: bool,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "text_id": text_id,
        "role": role,
        "text": text,
        "source_bytes_hex": bytes_to_hex(field_bytes),
        "actual_text_bytes_hex": bytes_to_hex(text_bytes),
        "encoding": SOURCE_ENCODING,
        "byte_length": len(text_bytes),
        "char_length": len(text),
        "max_bytes": max_bytes,
        "is_fixed_size": is_fixed_size,
        "null_terminated": null_terminated,
        "padding_byte": padding_byte,
        "linebreak_mode": detect_linebreak_mode(text),
        "ui_risk": length_profile(text_bytes, max_bytes, hard_limit=is_fixed_size, supports_length_growth=supports_length_growth)[
            "estimated_translation_risk"
        ],
        "notes": notes or [],
        "encoding_profile": encoding_profile(text, text_bytes),
        "length_profile": length_profile(
            text_bytes,
            max_bytes,
            hard_limit=is_fixed_size,
            supports_length_growth=supports_length_growth,
        ),
    }


def sort_paths(paths: list[Path]) -> list[Path]:
    return sorted(paths, key=lambda item: str(item).lower())
