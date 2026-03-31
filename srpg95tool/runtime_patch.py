"""Runtime patch definitions for the SRPG MAKER 95 localization toolchain.

This module keeps the low-level executable patch specifications in one place.
It is intentionally data-heavy: the project-level workflow and runtime
analysis layers describe *why* a surface is patched, while this file defines
the exact bytes, coverage metadata, and profile composition used to apply
those fixes.
"""

from __future__ import annotations

import shutil
import struct
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .common import SOURCE_ENCODING, json_dump, sha256_hex

MENU_GDI_FONT_HEIGHT = 12
SUPPORTED_PATCH_PROFILES = ("stable-menu16", "strong-dialogue")
RESOURCE_ALIAS_FOLDERS = ("BGM", "EFS", "BMP")
DIALOGUE_LINES = 5
DIALOGUE_LINE_BYTES = 512
DIALOGUE_TOTAL_BYTES = DIALOGUE_LINES * DIALOGUE_LINE_BYTES
DIALOGUE_ALLOC_BYTES = 0x1000
DIALOGUE_BUFFER_PTR_VA = 0x43DFF0
DIALOGUE_ACTIVE_LINES_VA = 0x43DFF4
OPCODE45_GDI_BRIDGE_VA = 0x431484
SHIFTJIS_GLYPH_DRAW_VA = 0x407744
GDI_BEGIN_DRAW_VA = 0x407888
GDI_TEXT_DRAW_VA = 0x407954
GDI_END_DRAW_VA = 0x40791C


@dataclass(frozen=True)
class BinaryPatchSpec:
    binary_name: str
    va: int
    expected_hexes: tuple[str, ...]
    patched_hex: str
    description: str

    @classmethod
    def single(cls, binary_name: str, va: int, expected_hex: str, patched_hex: str, description: str) -> "BinaryPatchSpec":
        return cls(
            binary_name=binary_name,
            va=va,
            expected_hexes=(expected_hex,),
            patched_hex=patched_hex,
            description=description,
        )


DATA_SECTION_EXECUTE_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x400344,
    expected_hex="400000c0",
    patched_hex="400000e0",
    description="Mark the .data section as executable so the dialogue side-buffer helpers placed in the dedicated .data code cave remain DEP-safe at runtime.",
)


CP936_INIT_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x42DF18,
    expected_hex="e8c332000050e805ffffff59c3",
    patched_hex="68a803000090e805ffffff59c3",
    description="Force CRT multibyte initialization to call _setmbcp(936) instead of GetACP().",
)

FONT_CHARSET_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x4078D3,
    expected_hex="c645db80",
    patched_hex="c645db86",
    description="Change LOGFONTA.lfCharSet from SHIFTJIS_CHARSET (0x80) to GB2312_CHARSET (0x86).",
)

FONT_FACENAME_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x432A8F,
    expected_hex="826c827220835383568362834e00000000",
    patched_hex="53696d53756e0000000000000000000000",
    description="Replace hardcoded Shift-JIS face name 'ＭＳ ゴシック' with ASCII face name 'SimSun'.",
)


def _call_rel_hex(callsite_va: int, target_va: int) -> str:
    rel = (target_va - (callsite_va + 5)) & 0xFFFFFFFF
    return "e8" + rel.to_bytes(4, "little").hex()


def _build_opcode45_gdi_bridge_hex() -> str:
    stub = bytearray()
    local_helper_va = 0x431500

    def emit(hex_bytes: str) -> None:
        stub.extend(bytes.fromhex(hex_bytes))

    def emit_call(target_va: int) -> None:
        callsite_va = OPCODE45_GDI_BRIDGE_VA + len(stub)
        stub.extend(bytes.fromhex(_call_rel_hex(callsite_va, target_va)))

    emit("558bec81ec20010000")
    emit("6a0cff7508")
    emit_call(GDI_BEGIN_DRAW_VA)
    emit("83c408")
    emit("8b450c8945f0")
    emit("8b45108945f4")
    emit("8b4d0c81c180010000894df8")
    emit("8b451083c0208945fc")
    emit("8b45148d95e0feffff5250")
    emit_call(local_helper_va)
    emit("83c408")
    emit("ff75188d55f05250ff7508")
    emit_call(GDI_TEXT_DRAW_VA)
    emit("83c410ff7508")
    emit_call(GDI_END_DRAW_VA)
    emit("83c404c9c3")
    if OPCODE45_GDI_BRIDGE_VA + len(stub) > local_helper_va:
        raise ValueError("opcode45 bridge grew past the reserved local sanitize helper offset")
    emit("00" * (local_helper_va - (OPCODE45_GDI_BRIDGE_VA + len(stub))))

    helper = bytearray()
    labels: dict[str, int] = {}
    short_fixups: list[tuple[int, str]] = []

    def h_emit(hex_bytes: str) -> None:
        helper.extend(bytes.fromhex(hex_bytes))

    def h_label(name: str) -> None:
        labels[name] = len(helper)

    def h_jump(opcode: str, target: str) -> None:
        helper.extend(bytes.fromhex(opcode))
        short_fixups.append((len(helper), target))
        helper.append(0)

    h_emit("55")  # push ebp
    h_emit("8bec")  # mov ebp, esp
    h_emit("56")  # push esi
    h_emit("57")  # push edi
    h_emit("53")  # push ebx
    h_emit("8b7508")  # mov esi, [ebp+8]
    h_emit("8b7d0c")  # mov edi, [ebp+0Ch]
    h_emit("89fb")  # mov ebx, edi
    h_emit("b9ff000000")  # mov ecx, 0FFh

    h_label("loop")
    h_emit("83f900")  # cmp ecx, 0
    h_jump("74", "term")
    h_emit("8a06")  # mov al, [esi]
    h_emit("84c0")  # test al, al
    h_jump("74", "term")
    h_emit("3c01")  # cmp al, 1
    h_jump("74", "skip1")
    h_emit("3c81")  # cmp al, 81h
    h_jump("75", "copy1")
    h_emit("8a5601")  # mov dl, [esi+1]
    h_emit("80fa40")  # cmp dl, 40h
    h_jump("74", "skip2")
    h_emit("80fa45")  # cmp dl, 45h
    h_jump("74", "out_dot")
    h_emit("80fa46")  # cmp dl, 46h
    h_jump("74", "out_colon")
    h_emit("80fa5e")  # cmp dl, 5Eh
    h_jump("74", "out_slash")
    h_emit("80fa7e")  # cmp dl, 7Eh
    h_jump("74", "out_x")

    h_label("copy1")
    h_emit("8807")  # mov [edi], al
    h_emit("47")  # inc edi
    h_emit("46")  # inc esi
    h_emit("49")  # dec ecx
    h_jump("eb", "loop")

    h_label("skip1")
    h_emit("46")  # inc esi
    h_jump("eb", "loop")

    h_label("skip2")
    h_emit("83c602")  # add esi, 2
    h_jump("eb", "loop")

    h_label("out_dot")
    h_emit("c6072e")  # mov byte ptr [edi], '.'
    h_emit("47")  # inc edi
    h_emit("83c602")  # add esi, 2
    h_emit("49")  # dec ecx
    h_jump("eb", "loop")

    h_label("out_colon")
    h_emit("c6073a")  # mov byte ptr [edi], ':'
    h_emit("47")  # inc edi
    h_emit("83c602")  # add esi, 2
    h_emit("49")  # dec ecx
    h_jump("eb", "loop")

    h_label("out_slash")
    h_emit("c6072f")  # mov byte ptr [edi], '/'
    h_emit("47")  # inc edi
    h_emit("83c602")  # add esi, 2
    h_emit("49")  # dec ecx
    h_jump("eb", "loop")

    h_label("out_x")
    h_emit("c60778")  # mov byte ptr [edi], 'x'
    h_emit("47")  # inc edi
    h_emit("83c602")  # add esi, 2
    h_emit("49")  # dec ecx
    h_jump("eb", "loop")

    h_label("term")
    h_emit("c60700")  # mov byte ptr [edi], 0
    h_emit("89d8")  # mov eax, ebx
    h_emit("5b")  # pop ebx
    h_emit("5f")  # pop edi
    h_emit("5e")  # pop esi
    h_emit("5d")  # pop ebp
    h_emit("c3")  # ret

    for pos, target in short_fixups:
        disp = labels[target] - (pos + 1)
        if not -128 <= disp <= 127:
            raise ValueError(f"opcode45 sanitize helper jump to {target} is out of range")
        helper[pos] = disp & 0xFF

    stub.extend(helper)
    return stub.hex()


OPCODE45_GDI_BRIDGE_PATCHED_HEX = _build_opcode45_gdi_bridge_hex()

OPCODE45_GDI_BRIDGE_STUB = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=OPCODE45_GDI_BRIDGE_VA,
    expected_hex="00" * len(bytes.fromhex(OPCODE45_GDI_BRIDGE_PATCHED_HEX)),
    patched_hex=OPCODE45_GDI_BRIDGE_PATCHED_HEX,
    description="Inject a GDI bridge stub for opcode 45 menu rendering via sub_407888 -> sub_407954 -> sub_40791C with a UI-only font height of 12.",
)

OPCODE45_GDI_CALLSITE_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x414478,
        expected_hex="e8c732ffff",
        patched_hex="e807d00100",
        description="Redirect sub_414344 menu text call site #1 from sub_407744 to the opcode 45 GDI bridge stub.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4144C1,
        expected_hex="e87e32ffff",
        patched_hex="e8becf0100",
        description="Redirect sub_414344 menu text call site #2 from sub_407744 to the opcode 45 GDI bridge stub.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x414658,
        expected_hex="e8e730ffff",
        patched_hex="e827ce0100",
        description="Redirect sub_4144F4 highlighted menu text call site #1 from sub_407744 to the opcode 45 GDI bridge stub.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4146E2,
        expected_hex="e85d30ffff",
        patched_hex="e89dcd0100",
        description="Redirect sub_4144F4 highlighted menu helper text call site #2 from sub_407744 to the opcode 45 GDI bridge stub.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x414760,
        expected_hex="e8df2fffff",
        patched_hex="e81fcd0100",
        description="Redirect sub_4144F4 highlighted menu helper text call site #3 from sub_407744 to the opcode 45 GDI bridge stub.",
    ),
]

def _call_patch_hex(callsite_va: int, target_va: int) -> str:
    return _call_rel_hex(callsite_va, target_va)


UNIT_SHEET_GDI_CALLSITE_DEFS = [
    (0x4126ED, "e85250ffff", "sub_412448 class name draw #1"),
    (0x412709, "e83650ffff", "sub_412448 class name draw #2"),
    (0x41275A, "e8e54fffff", "sub_412448 level label draw"),
    (0x4127E9, "e8564fffff", "sub_412448 level value draw"),
    (0x41283A, "e8054fffff", "sub_412448 HP label draw"),
    (0x4128B4, "e88b4effff", "sub_412448 HP value draw"),
    (0x412904, "e83b4effff", "sub_412448 MP label draw"),
    (0x412981, "e8be4dffff", "sub_412448 MP value draw"),
    (0x4129D3, "e86c4dffff", "sub_412448 total attack label draw"),
    (0x412A21, "e81e4dffff", "sub_412448 total attack value draw"),
    (0x412A72, "e8cd4cffff", "sub_412448 status label draw #1"),
    (0x412AC0, "e87f4cffff", "sub_412448 status label draw #2"),
    (0x412B11, "e82e4cffff", "sub_412448 status label draw #3"),
    (0x412B5F, "e8e04bffff", "sub_412448 status label draw #4"),
    (0x412BB0, "e88f4bffff", "sub_412448 status label draw #5"),
    (0x412BFE, "e8414bffff", "sub_412448 status label draw #6"),
    (0x412C52, "e8ed4affff", "sub_412448 status label draw #7"),
    (0x412C9D, "e8a24affff", "sub_412448 status label draw #8"),
    (0x412CEE, "e8514affff", "sub_412448 status label draw #9"),
    (0x412D39, "e8064affff", "sub_412448 status label draw #10"),
    (0x412D8A, "e8b549ffff", "sub_412448 status label draw #11"),
    (0x412DD5, "e86a49ffff", "sub_412448 status label draw #12"),
    (0x412E26, "e81949ffff", "sub_412448 status label draw #13"),
    (0x412E71, "e8ce48ffff", "sub_412448 status label draw #14"),
    (0x412EC5, "e87a48ffff", "sub_412448 status label draw #15"),
    (0x412F24, "e81b48ffff", "sub_412448 status label draw #16"),
    (0x412F73, "e8cc47ffff", "sub_412448 status label draw #17"),
    (0x412FD2, "e86d47ffff", "sub_412448 status label draw #18"),
    (0x413021, "e81e47ffff", "sub_412448 status label draw #19"),
    (0x413080, "e8bf46ffff", "sub_412448 status label draw #20"),
]

COMPACT_UNIT_CARD_GDI_CALLSITE_DEFS = [
    (0x41CB2B, "e814acfeff", "sub_41C960 compact unit card label draw #1"),
    (0x41CBA6, "e899abfeff", "sub_41C960 compact unit card label draw #2"),
    (0x41CCCA, "e875aafeff", "sub_41C960 compact unit card label draw #3"),
    (0x41CD45, "e8faa9feff", "sub_41C960 compact unit card label draw #4"),
    (0x41CE7C, "e8c3a8feff", "sub_41C960 compact unit card label draw #5"),
    (0x41CEC8, "e877a8feff", "sub_41C960 compact unit card label draw #6"),
    (0x41CF19, "e826a8feff", "sub_41C960 compact unit card label draw #7"),
    (0x41CF65, "e8daa7feff", "sub_41C960 compact unit card label draw #8"),
    (0x41CFB9, "e886a7feff", "sub_41C960 compact unit card label draw #9"),
    (0x41D005, "e83aa7feff", "sub_41C960 compact unit card label draw #10"),
    (0x41D056, "e8e9a6feff", "sub_41C960 compact unit card label draw #11"),
    (0x41D0A2, "e89da6feff", "sub_41C960 compact unit card label draw #12"),
    (0x41D0F6, "e849a6feff", "sub_41C960 compact unit card label draw #13"),
    (0x41D13F, "e800a6feff", "sub_41C960 compact unit card label draw #14"),
    (0x41D190, "e8afa5feff", "sub_41C960 compact unit card label draw #15"),
    (0x41D205, "e83aa5feff", "sub_41C960 compact unit card label draw #16"),
]

OTHER_DAT_UI_GDI_CALLSITE_DEFS = [
    (0x414CD6, "e8692affff", "sub_414B14 battle reward popup line draw"),
    (0x4153F6, "e84923ffff", "sub_414D40 post-battle summary line draw"),
]

SORTIE_UNIT_ROSTER_GDI_CALLSITE_DEFS = [
    (0x41F3E7, _call_patch_hex(0x41F3E7, SHIFTJIS_GLYPH_DRAW_VA), "sub_41F27C sortie roster highlighted unit name draw"),
    (0x41F46D, _call_patch_hex(0x41F46D, SHIFTJIS_GLYPH_DRAW_VA), "sub_41F27C sortie roster highlighted unit status marker draw"),
    (0x41F4E1, _call_patch_hex(0x41F4E1, SHIFTJIS_GLYPH_DRAW_VA), "sub_41F27C sortie roster page label draw"),
    (0x41F55F, _call_patch_hex(0x41F55F, SHIFTJIS_GLYPH_DRAW_VA), "sub_41F27C sortie roster page counter draw"),
    (0x42045C, _call_patch_hex(0x42045C, SHIFTJIS_GLYPH_DRAW_VA), "sub_4203C0 sortie roster unit row draw"),
    (0x4204B0, _call_patch_hex(0x4204B0, SHIFTJIS_GLYPH_DRAW_VA), "sub_4203C0 sortie roster unit status marker draw"),
    (0x420947, _call_patch_hex(0x420947, SHIFTJIS_GLYPH_DRAW_VA), "sub_4208CC sortie roster summary label draw"),
    (0x4209CB, _call_patch_hex(0x4209CB, SHIFTJIS_GLYPH_DRAW_VA), "sub_4208CC sortie roster summary counter draw"),
]

ITEM_MAGIC_HELP_GDI_CALLSITE_DEFS = [
    (0x414319, _call_patch_hex(0x414319, SHIFTJIS_GLYPH_DRAW_VA), "sub_4142D4 item/magic help panel title draw"),
    (0x414337, _call_patch_hex(0x414337, SHIFTJIS_GLYPH_DRAW_VA), "sub_4142D4 item/magic help panel body draw"),
]

BATTLE_UNIT_HOVER_GDI_CALLSITE_DEFS = [
    (0x413FF2, _call_patch_hex(0x413FF2, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover name outline draw #1"),
    (0x41400F, _call_patch_hex(0x41400F, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover name outline draw #2"),
    (0x41402E, _call_patch_hex(0x41402E, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover name outline draw #3"),
    (0x41404B, _call_patch_hex(0x41404B, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover name outline draw #4"),
    (0x414067, _call_patch_hex(0x414067, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover name main draw"),
    (0x414115, _call_patch_hex(0x414115, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover sublabel outline draw #1"),
    (0x414132, _call_patch_hex(0x414132, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover sublabel outline draw #2"),
    (0x414151, _call_patch_hex(0x414151, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover sublabel outline draw #3"),
    (0x41416E, _call_patch_hex(0x41416E, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover sublabel outline draw #4"),
    (0x41418A, _call_patch_hex(0x41418A, SHIFTJIS_GLYPH_DRAW_VA), "sub_413F80 battle hover sublabel main draw"),
]

CAMP_SHOP_ITEM_LIST_GDI_CALLSITE_DEFS = [
    (0x4210E3, _call_patch_hex(0x4210E3, SHIFTJIS_GLYPH_DRAW_VA), "sub_4210D0 camp shop header/status draw"),
]

DAT_UI_GDI_CALLSITE_DEFS = [
    *UNIT_SHEET_GDI_CALLSITE_DEFS,
    *COMPACT_UNIT_CARD_GDI_CALLSITE_DEFS,
    *OTHER_DAT_UI_GDI_CALLSITE_DEFS,
    *SORTIE_UNIT_ROSTER_GDI_CALLSITE_DEFS,
    *ITEM_MAGIC_HELP_GDI_CALLSITE_DEFS,
    *BATTLE_UNIT_HOVER_GDI_CALLSITE_DEFS,
    *CAMP_SHOP_ITEM_LIST_GDI_CALLSITE_DEFS,
]

ASCII_SLASH_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4332D4,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace full-width HP separator with ASCII slash for unit-sheet formatted values.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4332E2,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace full-width MP separator with ASCII slash for unit-sheet formatted values.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4335A5,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace full-width HP separator with ASCII slash for compact-unit-card formatted values.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4335B1,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace full-width MP separator with ASCII slash for compact-unit-card formatted values.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433347,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace a remaining full-width slash literal with ASCII slash on migrated UI surfaces #1.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433376,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace a remaining full-width slash literal with ASCII slash on migrated UI surfaces #2.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43360E,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace a remaining full-width slash literal with ASCII slash on migrated UI surfaces #3.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433826,
        expected_hex="815e00",
        patched_hex="2f0000",
        description="Replace a remaining full-width slash literal with ASCII slash on migrated UI surfaces #4.",
    ),
]

CP932_PUNCTUATION_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4331D4,
        expected_hex="814600",
        patched_hex="000000",
        description="Remove the legacy cp932 title suffix appended by sub_40F308 before the shared item/magic help panel title draw.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4331D9,
        expected_hex="814600",
        patched_hex="000000",
        description="Remove the legacy cp932 title suffix appended by sub_40F4C4 before the shared item/magic help panel title draw.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4331E0,
        expected_hex="814600",
        patched_hex="000000",
        description="Remove the legacy cp932 title suffix appended by sub_40F650 before the shared item/magic help panel title draw.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4331E9,
        expected_hex="814600",
        patched_hex="000000",
        description="Remove the legacy cp932 title suffix appended by sub_40F930 before the alternate shared item/equipment/skill help-panel title draw.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43337D,
        expected_hex="252d3873814600",
        patched_hex="252d38733a0000",
        description="Replace the cp932 full-width colon in the first equipment/skill row label formatter with an ASCII colon.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433384,
        expected_hex="252d3873814600",
        patched_hex="252d38733a0000",
        description="Replace the cp932 full-width colon in the second equipment/skill row label formatter with an ASCII colon.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43338B,
        expected_hex="252d3873814600",
        patched_hex="252d38733a0000",
        description="Replace the cp932 full-width colon in the third equipment/skill row label formatter with an ASCII colon.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433398,
        expected_hex="817e00",
        patched_hex="780000",
        description="Replace the cp932 multiplication sign separator used by sub_417D9C with an ASCII 'x'.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4333A5,
        expected_hex="817e00",
        patched_hex="780000",
        description="Replace the cp932 multiplication sign separator used by sub_418108 with an ASCII 'x'.",
    ),
]

DIGIT_GLYPH_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329A1,
        expected_hex="817c00",
        patched_hex="a3ad00",
        description="Replace the cp932 full-width minus glyph used by sub_4051A0 with a cp936 full-width minus for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329A4,
        expected_hex="824f00",
        patched_hex="a3b000",
        description="Replace the cp932 full-width digit 0 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329A7,
        expected_hex="825000",
        patched_hex="a3b100",
        description="Replace the cp932 full-width digit 1 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329AA,
        expected_hex="825100",
        patched_hex="a3b200",
        description="Replace the cp932 full-width digit 2 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329AD,
        expected_hex="825200",
        patched_hex="a3b300",
        description="Replace the cp932 full-width digit 3 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329B0,
        expected_hex="825300",
        patched_hex="a3b400",
        description="Replace the cp932 full-width digit 4 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329B3,
        expected_hex="825400",
        patched_hex="a3b500",
        description="Replace the cp932 full-width digit 5 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329B6,
        expected_hex="825500",
        patched_hex="a3b600",
        description="Replace the cp932 full-width digit 6 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329B9,
        expected_hex="825600",
        patched_hex="a3b700",
        description="Replace the cp932 full-width digit 7 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329BC,
        expected_hex="825700",
        patched_hex="a3b800",
        description="Replace the cp932 full-width digit 8 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4329BF,
        expected_hex="825800",
        patched_hex="a3b900",
        description="Replace the cp932 full-width digit 9 used by sub_4051A0 with a cp936 full-width digit for migrated EXP and level-up UI surfaces.",
    ),
]

SHOP_LIST_FORMAT_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433849,
        expected_hex="2020252d32327300",
        patched_hex="2020252d32307300",
        description="Shrink the camp shop header name column format from 22 to 20 characters for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43385B,
        expected_hex="252d32327300",
        patched_hex="252d32307300",
        description="Shrink the camp shop item-row name column format from 22 to 20 characters for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43386A,
        expected_hex="2020252d32327300",
        patched_hex="2020252d32307300",
        description="Shrink the alternate camp shop header name column format from 22 to 20 characters for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43387C,
        expected_hex="252d32327300",
        patched_hex="252d32307300",
        description="Shrink the alternate camp shop item-row name column format from 22 to 20 characters for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433855,
        expected_hex="25387300",
        patched_hex="25367300",
        description="Shrink the camp shop trailing header column formatter from width 8 to width 6 so the owned column stays inside the frame.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433876,
        expected_hex="25387300",
        patched_hex="25367300",
        description="Shrink the alternate camp shop trailing header column formatter from width 8 to width 6 so the owned column stays inside the frame.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433861,
        expected_hex="2531306400",
        patched_hex="2538640000",
        description="Shrink the camp shop price field from width 10 to width 8 for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43388D,
        expected_hex="2531306400",
        patched_hex="2538640000",
        description="Shrink the alternate camp shop price field from width 10 to width 8 for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433892,
        expected_hex="2531306400",
        patched_hex="2538640000",
        description="Shrink the alternate camp shop discounted-price field from width 10 to width 8 for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433866,
        expected_hex="25386400",
        patched_hex="25366400",
        description="Shrink the camp shop owned-count field from width 8 to width 6 for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433897,
        expected_hex="25386400",
        patched_hex="25366400",
        description="Shrink the alternate camp shop owned-count field from width 8 to width 6 for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433882,
        expected_hex="2020202020202020817c00",
        patched_hex="202020202020817c000000",
        description="Shrink the blank camp shop price placeholder from 8 spaces to 6 spaces while preserving the trailing separator used by the alternate shop list rows.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x421278,
        expected_hex="bf08000000",
        patched_hex="bf06000000",
        description="Reduce the camp shop middle-header centering width from 8 to 6 characters for the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4215FC,
        expected_hex="bf08000000",
        patched_hex="bf06000000",
        description="Reduce the alternate camp shop middle-header centering width from 8 to 6 characters for the 12px Chinese UI font.",
    ),
]

SHOP_LIST_X_ANCHOR_PATCHES = [
    BinaryPatchSpec(
        binary_name="SRPGEXEC.EXE",
        va=0x420C29,
        expected_hexes=("c783e851000050000000", "c783e851000044000000", "c783e851000040000000"),
        patched_hex="c783e85100003c000000",
        description="Shift an early camp shop column anchor left to match the tightened 12px Chinese shop columns.",
    ),
    BinaryPatchSpec(
        binary_name="SRPGEXEC.EXE",
        va=0x420F9E,
        expected_hexes=("c783e851000050000000", "c783e851000044000000", "c783e851000040000000"),
        patched_hex="c783e85100003c000000",
        description="Shift the alternate camp shop column anchor left to match the tightened 12px Chinese shop columns.",
    ),
    BinaryPatchSpec(
        binary_name="SRPGEXEC.EXE",
        va=0x4211A0,
        expected_hexes=("c783e851000050000000", "c783e851000044000000", "c783e851000040000000"),
        patched_hex="c783e85100003c000000",
        description="Shift the camp shop item-list x-anchor left to keep the tightened 12px Chinese columns inside the panel.",
    ),
    BinaryPatchSpec(
        binary_name="SRPGEXEC.EXE",
        va=0x421524,
        expected_hexes=("c783e851000050000000", "c783e851000044000000", "c783e851000040000000"),
        patched_hex="c783e85100003c000000",
        description="Shift the alternate camp shop item-list x-anchor left to keep the tightened 12px Chinese columns aligned inside the panel.",
    ),
]

BATTLE_SKILL_POPUP_LAYOUT_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4172BB,
        expected_hex="83c20c",
        patched_hex="83c206",
        description="Shift the battle skill popup row text anchor left from +12 to +6 so right-side MP/cost text has more room inside the frame with the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x417485,
        expected_hex="83c002",
        patched_hex="83c004",
        description="Increase the battle skill popup row-box width padding from +2 to +4 so translated skill rows keep their MP/cost text inside the frame with the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x43336C,
        expected_hex="252d32327300",
        patched_hex="252d31387300",
        description="Shrink the battle skill popup row name field from 22 to 18 characters so MP/cost text stays inside the frame with the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x433372,
        expected_hex="25346400",
        patched_hex="25326400",
        description="Shrink the battle skill popup current-MP field from width 4 to width 2 so MP/cost text stays inside the frame with the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4183A2,
        expected_hex="83c136",
        patched_hex="83c12a",
        description="Shift the battle skill popup right-side info anchor left from +54 to +42 so MP/cost text stays inside the frame with the 12px Chinese UI font.",
    ),
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=0x4183B5,
        expected_hex="c783f05100000c000000",
        patched_hex="c783f05100000a000000",
        description="Tighten the battle skill popup content width from 12 to 10 cells so MP/cost text stays inside the frame with the 12px Chinese UI font.",
    ),
]

DAT_UI_GDI_CALLSITE_PATCHES = [
    BinaryPatchSpec.single(
        binary_name="SRPGEXEC.EXE",
        va=va,
        expected_hex=expected_hex,
        patched_hex=_call_patch_hex(va, OPCODE45_GDI_BRIDGE_VA),
        description=f"Redirect {description} from sub_407744 to the shared GDI bridge.",
    )
    for va, expected_hex, description in DAT_UI_GDI_CALLSITE_DEFS
]

STABLE_UI_PATCH_COVERAGE = {
    "profile": "stable-menu16",
    "ui_surfaces": [
        {
            "ui_surface_id": "opening_skip_op_prompt",
            "runtime_functions": ["sub_42523C", "sub_414344", "sub_4144F4"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["MAP/SMAP_001.DAT"],
            "text_fields": ["smap prompt segments"],
            "notes": "Opcode 45 menu text and hover redraw are routed to the GDI bridge.",
        },
        {
            "ui_surface_id": "unit_sheet_panel",
            "runtime_functions": ["sub_412448"],
            "patched_draw_path": "mixed DrawTextA + shared GDI bridge",
            "source_files": ["UNIT.DAT", "CLASS.DAT", "WORD.DAT"],
            "text_fields": ["UNIT.name", "CLASS.name", "WORD.label"],
            "notes": "Status-sheet class names plus LV/HP/MP/total-attack/stat labels and their formatted value strings are redirected from sub_407744 to the shared GDI bridge.",
        },
        {
            "ui_surface_id": "compact_unit_card",
            "runtime_functions": ["sub_41C960"],
            "patched_draw_path": "mixed DrawTextA + shared GDI bridge",
            "source_files": ["UNIT.DAT", "WORD.DAT"],
            "text_fields": ["UNIT.name", "WORD.label"],
            "notes": "Compact unit-card labels are redirected from sub_407744 to the shared GDI bridge.",
        },
        {
            "ui_surface_id": "battle_reward_popup",
            "runtime_functions": ["sub_414B14"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["WORD.DAT", "ITEM.DAT"],
            "text_fields": ["WORD.label", "ITEM.name"],
            "notes": "Reward popup rows now bypass the Shift-JIS glyph renderer, and the shared numeric formatter sub_4051A0 no longer appends cp932-only full-width digits on migrated EXP lines.",
        },
        {
            "ui_surface_id": "post_battle_summary",
            "runtime_functions": ["sub_414D40"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["WORD.DAT", "UNIT.DAT", "MAGIC.DAT"],
            "text_fields": ["WORD.label", "UNIT.name", "MAGIC.name"],
            "notes": "Summary popup rows now bypass the Shift-JIS glyph renderer, and the shared numeric formatter sub_4051A0 no longer appends cp932-only full-width digits on migrated level-up lines.",
        },
        {
            "ui_surface_id": "unit_command_or_item_menu",
            "runtime_functions": ["sub_420564", "sub_414344"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["WORD.DAT", "ITEM.DAT", "UNIT.DAT"],
            "text_fields": ["WORD.label", "ITEM.name", "UNIT.name"],
            "notes": "Menu rows already inherit the opcode 45 GDI bridge.",
        },
        {
            "ui_surface_id": "equip_slot_list_and_help",
            "runtime_functions": ["sub_4177E8", "sub_417D9C", "sub_40F4C4", "sub_40F650"],
            "patched_draw_path": "menu rows plus shared help-panel downstream path via shared GDI bridge",
            "source_files": ["WORD.DAT", "ITEM.DAT", "MAGIC.DAT"],
            "text_fields": ["WORD.label", "ITEM.name", "ITEM.desc", "MAGIC.desc"],
            "notes": "List rows continue to use the existing menu bridge, the shared help-panel draw site at sub_4142D4 bypasses the Shift-JIS glyph renderer, and the upstream cp932-only punctuation formatters/separators used by sub_4177E8 / sub_417D9C / sub_417548 / sub_418108 are normalized to ASCII-safe characters before they can leak as mojibake on migrated UI surfaces.",
        },
        {
            "ui_surface_id": "sortie_unit_roster",
            "runtime_functions": ["sub_41E028", "sub_4203C0", "sub_41F27C", "sub_4208CC"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["UNIT.DAT"],
            "text_fields": ["UNIT.name"],
            "notes": "The sortie roster previously kept UNIT.DAT names on the old glyph path. Highlight rows, list rows, and summary/header text now share the patched GDI bridge under stable-menu16.",
        },
        {
            "ui_surface_id": "item_magic_help_panel",
            "runtime_functions": ["sub_40F4C4", "sub_40F650", "sub_4142D4", "sub_4210F4", "sub_421478"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["ITEM.DAT", "MAGIC.DAT"],
            "text_fields": ["ITEM.desc", "MAGIC.desc"],
            "notes": "The shared two-line help panel used by item and magic menus is routed through the same GDI bridge as the rest of stable-menu16, and the cp932-only title suffix constants previously appended by sub_40F308 / sub_40F4C4 / sub_40F650 / sub_40F930 are stripped so hovered item/equipment/skill names no longer pick up a fixed mojibake tail.",
        },
        {
            "ui_surface_id": "battle_unit_hover_name",
            "runtime_functions": ["sub_413F80"],
            "patched_draw_path": "shared GDI bridge",
            "source_files": ["UNIT.DAT"],
            "text_fields": ["UNIT.name"],
            "notes": "Battlefield hover labels above units now bypass the old Shift-JIS glyph renderer and share the same GDI bridge used by the other UNIT.DAT-backed UI surfaces.",
        },
        {
            "ui_surface_id": "camp_shop_item_list",
            "runtime_functions": ["sub_4210D0", "sub_4210F4", "sub_421220", "sub_421478", "sub_4215A4", "sub_414344"],
            "patched_draw_path": "shared GDI bridge plus shop-specific row formatting and localized column anchors",
            "source_files": ["ITEM.DAT", "WORD.DAT"],
            "text_fields": ["ITEM.name", "WORD.label"],
            "notes": "Camp shop rows now use the shared GDI bridge for their remaining direct glyph callsite, trim leaked legacy prefix/suffix markers in the shared row buffer, normalize slash variants, tighten the shop-specific row formatter widths, and shift the list columns left to keep the price/owned anchors inside the panel with the 12px Chinese UI font.",
        },
        {
            "ui_surface_id": "battle_skill_popup",
            "runtime_functions": ["sub_40F930", "sub_417548", "sub_4171E0", "sub_418308", "sub_414344"],
            "patched_draw_path": "shared GDI bridge plus skill-popup-local formatter tightening and anchor tightening",
            "source_files": ["MAGIC.DAT", "WORD.DAT"],
            "text_fields": ["MAGIC.name", "MAGIC.desc", "WORD.label"],
            "notes": "The battle skill popup near the acting unit does not reuse the camp shop formatter. sub_417548 formats the skill rows, while sub_4171E0/sub_418308 keep their own local popup anchors; stable-menu16 now tightens the row formatter widths, shifts the popup row text anchor left, expands the row-box padding slightly, and tightens the skill-detail local anchor so MP/cost text stays inside the frame with the 12px Chinese UI font.",
        },
    ],
}

STRONG_DIALOGUE_PATCH_ENTRY = {
    "ui_surface_id": "dialogue_opcode_1_201",
    "runtime_functions": ["sub_40D18C", "sub_4148A4"],
    "patched_draw_path": "heap-backed side buffer plus patched dialogue draw helper",
    "source_files": ["MAP/SMAP_*.DAT"],
    "text_fields": ["opcode 1 speaker blocks", "opcode 201 continuation lines"],
    "notes": "Experimental strong-dialogue keeps the heap-backed opcode 1/201 side-buffer chain for research builds. The formal stable-menu16 profile does not enable this runtime path.",
}

STABLE_GDI_TEXT_FILTERS = [
    {"source_file": "WORD.DAT", "field_paths": ["label"]},
    {"source_file": "CLASS.DAT", "field_paths": ["name"]},
    {"source_file": "UNIT.DAT", "field_paths": ["name"]},
    {"source_file": "ITEM.DAT", "field_paths": ["name", "desc"]},
    {"source_file": "MAGIC.DAT", "field_paths": ["name", "desc"]},
]


def stable_gdi_text_filters() -> list[dict[str, Any]]:
    return [dict(item) for item in STABLE_GDI_TEXT_FILTERS]

OPCODE1_201_COPY_HELPER_VA = 0x434820
OPCODE1_201_DRAW_HELPER_VA = 0x434900
OPCODE1_201_COPY_HELPER_BYTES = (
    "5589e5608b5d088b750c8b5510a1f0df430085c075236a40680030000068001000006a00ff15d0024400"
    "a3f0df4300c705f4df43000000000083fa05735c833df0df4300007453a1f0df4300fc85d2751789c731"
    "c0b980020000f3abc705f4df430001000000eb138b0df4df430039ca7c098d4a01890df4df4300a1f0df4300"
    "89d1c1e10901c889c7b9ff010000acaa84c074064975f7c6070083fa05732389d0c1e0048d04808d"
    "bc032c5e00008b750cb94f000000acaa84c074064975f7c607006189ec5dc3"
)
OPCODE1_201_DRAW_HELPER_BYTES = (
    "5589e583ec04608b5d088b550c8b35f0df430085f6740e83fa05730989d0c1e00901c6eb1289d0c1e004"
    "8d04808db4032c5e00000fb64514508b4d1051568b435450e86563fdff83c4108945fc618b45fc89ec5dc3"
)
STRONG_DIALOGUE_STUB_BYTES = OPCODE45_GDI_BRIDGE_PATCHED_HEX
STRONG_DIALOGUE_STUB = BinaryPatchSpec(
    binary_name="SRPGEXEC.EXE",
    va=0x431484,
    expected_hexes=(
        "00" * len(bytes.fromhex(STRONG_DIALOGUE_STUB_BYTES)),
        OPCODE45_GDI_BRIDGE_PATCHED_HEX,
    ),
    patched_hex=STRONG_DIALOGUE_STUB_BYTES,
    description="Reuse the opcode 45 shared GDI bridge stub at its original code cave while the opcode 1/201 dialogue helpers live in a larger dedicated cave.",
)
OPCODE1_201_COPY_HELPER_STUB = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=OPCODE1_201_COPY_HELPER_VA,
    expected_hex="00" * len(bytes.fromhex(OPCODE1_201_COPY_HELPER_BYTES)),
    patched_hex=OPCODE1_201_COPY_HELPER_BYTES,
    description="Install the opcode 1/201 side-buffer copy helper in a dedicated high-capacity code cave.",
)
OPCODE1_201_DRAW_HELPER_STUB = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=OPCODE1_201_DRAW_HELPER_VA,
    expected_hex="00" * len(bytes.fromhex(OPCODE1_201_DRAW_HELPER_BYTES)),
    patched_hex=OPCODE1_201_DRAW_HELPER_BYTES,
    description="Install the opcode 1/201 side-buffer draw helper in a dedicated high-capacity code cave.",
)
OPCODE1_COPY_CALLSITE_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x40D4E7,
    expected_hex="8d8b2c5e0000565783c0028bf18bf833c083c9fff2aef7d12bf98bd187f7c1e9028bc7f3a58bca83e103f3a45f5e",
    patched_hex="83c0026a005053" + _call_rel_hex(0x40D4EE, OPCODE1_201_COPY_HELPER_VA) + "83c40c" + ("90" * 31),
    description="Redirect opcode 1 speaker-line copies into the strong-dialogue side-buffer helper while leaving a short compatibility write path in place.",
)
OPCODE201_COPY_CALLSITE_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x40D546,
    expected_hex="8b45f88b93c4010000c1e004560315309a4300578d048083c20203c38bfa052c5e00008bf033c083c9fff2aef7d12bf98bd187f7c1e9028bc7f3a58bca83e103f3a45f5e",
    patched_hex="8b45f88b93c401000081c2309a430083c202505253" + _call_rel_hex(0x40D55B, OPCODE1_201_COPY_HELPER_VA) + "83c40c" + ("90" * 39),
    description="Redirect chained opcode 201 line copies into the strong-dialogue side-buffer helper.",
)
OPCODE1_201_DRAW_CALLSITE_PATCH = BinaryPatchSpec.single(
    binary_name="SRPGEXEC.EXE",
    va=0x414A84,
    expected_hex="8bc68b15dc9a4300c1e004528d4df0518d048003c3052c5e0000508b535452e8ac2effff",
    patched_hex="89f0ff35dc9a43008d4df0515053" + _call_rel_hex(0x414A92, OPCODE1_201_DRAW_HELPER_VA) + ("90" * 17),
    description="Redirect sub_4148A4 dialogue line reads to the strong-dialogue side-buffer draw helper instead of the original 80-byte runtime slots.",
)


def _remove_path(path: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_tree(src: Path, dst: Path) -> None:
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("*.id0", "*.id1", "*.id2", "*.nam", "*.til", "*.i64"))


def _load_existing_alias_names(root: Path) -> set[tuple[str, str]]:
    report_path = root / "reports" / "runtime_resource_aliases.json"
    if not report_path.exists():
        return set()
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    aliases = payload.get("aliases", [])
    return {
        (str(item.get("folder", "")), str(item.get("alias_name", "")))
        for item in aliases
        if item.get("folder") and item.get("alias_name")
    }


def _create_resource_aliases(root: Path) -> list[dict[str, Any]]:
    aliases: list[dict[str, Any]] = []
    known_aliases = _load_existing_alias_names(root)
    for folder_name in RESOURCE_ALIAS_FOLDERS:
        folder = root / folder_name
        if not folder.is_dir():
            continue
        for source_path in sorted(folder.iterdir(), key=lambda item: item.name):
            if not source_path.is_file():
                continue
            if (folder_name, source_path.name) in known_aliases:
                aliases.append(
                    {
                        "folder": folder_name,
                        "source_name": source_path.name,
                        "status": "skip_existing_alias_source",
                    }
                )
                continue
            try:
                cp932_bytes = source_path.name.encode(SOURCE_ENCODING)
            except UnicodeEncodeError:
                continue
            if all(byte < 0x80 for byte in cp932_bytes):
                continue
            try:
                alias_name = cp932_bytes.decode("cp936")
            except UnicodeDecodeError:
                aliases.append(
                    {
                        "folder": folder_name,
                        "source_name": source_path.name,
                        "status": "decode_failed",
                    }
                )
                continue
            if alias_name == source_path.name:
                continue
            alias_path = folder / alias_name
            if alias_path.exists():
                aliases.append(
                    {
                        "folder": folder_name,
                        "source_name": source_path.name,
                        "alias_name": alias_name,
                        "status": "already_exists",
                    }
                )
                continue
            shutil.copy2(source_path, alias_path)
            aliases.append(
                {
                    "folder": folder_name,
                    "source_name": source_path.name,
                    "alias_name": alias_name,
                    "status": "created",
                }
            )
    return aliases


def _summarize_resource_aliases(aliases: list[dict[str, Any]]) -> dict[str, Any]:
    folder_counts: dict[str, dict[str, int]] = {folder: {"created": 0, "already_exists": 0, "decode_failed": 0} for folder in RESOURCE_ALIAS_FOLDERS}
    for alias in aliases:
        folder = str(alias.get("folder", ""))
        status = str(alias.get("status", ""))
        if folder not in folder_counts:
            folder_counts[folder] = {"created": 0, "already_exists": 0, "decode_failed": 0}
        if status in folder_counts[folder]:
            folder_counts[folder][status] += 1

    bitmap_alias_examples = [
        {
            "source_name": str(item.get("source_name", "")),
            "alias_name": str(item.get("alias_name", "")),
            "status": str(item.get("status", "")),
        }
        for item in aliases
        if item.get("folder") == "BMP" and item.get("alias_name")
    ][:10]

    return {
        "alias_folders": list(RESOURCE_ALIAS_FOLDERS),
        "created_count": sum(1 for item in aliases if item.get("status") == "created"),
        "bitmap_alias_count": sum(1 for item in aliases if item.get("folder") == "BMP" and item.get("status") == "created"),
        "folder_counts": folder_counts,
        "bitmap_alias_examples": bitmap_alias_examples,
    }


def _get_file_offset_from_va(data: bytes, va: int) -> int:
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise ValueError("Invalid PE signature")
    file_header_offset = e_lfanew + 4
    number_of_sections = struct.unpack_from("<H", data, file_header_offset + 2)[0]
    size_of_optional_header = struct.unpack_from("<H", data, file_header_offset + 16)[0]
    optional_header_offset = file_header_offset + 20
    magic = struct.unpack_from("<H", data, optional_header_offset)[0]
    if magic == 0x10B:
        image_base = struct.unpack_from("<I", data, optional_header_offset + 28)[0]
    elif magic == 0x20B:
        image_base = struct.unpack_from("<Q", data, optional_header_offset + 24)[0]
    else:
        raise ValueError(f"Unsupported PE optional header magic: 0x{magic:X}")
    rva = va - image_base
    size_of_headers = struct.unpack_from("<I", data, optional_header_offset + 60)[0]
    if 0 <= rva < size_of_headers:
        return rva
    section_offset = optional_header_offset + size_of_optional_header
    for index in range(number_of_sections):
        entry = section_offset + index * 40
        virtual_size, virtual_address, size_of_raw_data, pointer_to_raw_data = struct.unpack_from("<IIII", data, entry + 8)
        span = max(virtual_size, size_of_raw_data)
        if virtual_address <= rva < virtual_address + span:
            return pointer_to_raw_data + (rva - virtual_address)
    raise ValueError(f"VA 0x{va:X} does not map to any PE section")


def _apply_binary_patch(path: Path, spec: BinaryPatchSpec) -> dict[str, Any]:
    data = bytearray(path.read_bytes())
    expected_variants = [bytes.fromhex(item) for item in spec.expected_hexes]
    patched = bytes.fromhex(spec.patched_hex)
    file_offset = _get_file_offset_from_va(data, spec.va)
    current = bytes(data[file_offset : file_offset + len(patched)])
    result = {
        "binary": spec.binary_name,
        "va": f"0x{spec.va:X}",
        "file_offset": f"0x{file_offset:X}",
        "description": spec.description,
        "expected_hexes": list(spec.expected_hexes),
        "patched_hex": spec.patched_hex,
        "current_hex": current.hex(),
        "status": "pending",
    }
    if current == patched:
        result["status"] = "already_patched"
        result["sha256"] = sha256_hex(bytes(data))
        return result
    if current not in expected_variants:
        result["status"] = "mismatch"
        return result
    data[file_offset : file_offset + len(patched)] = patched
    path.write_bytes(data)
    result["status"] = "patched"
    result["sha256"] = sha256_hex(bytes(data))
    return result


def _stable_patch_specs() -> list[BinaryPatchSpec]:
    return [
        FONT_CHARSET_PATCH,
        FONT_FACENAME_PATCH,
        OPCODE45_GDI_BRIDGE_STUB,
        *OPCODE45_GDI_CALLSITE_PATCHES,
        *DAT_UI_GDI_CALLSITE_PATCHES,
        *ASCII_SLASH_PATCHES,
        *CP932_PUNCTUATION_PATCHES,
        *DIGIT_GLYPH_PATCHES,
        *SHOP_LIST_FORMAT_PATCHES,
        *SHOP_LIST_X_ANCHOR_PATCHES,
        *BATTLE_SKILL_POPUP_LAYOUT_PATCHES,
    ]


def _strong_dialogue_patch_specs() -> list[BinaryPatchSpec]:
    return [
        DATA_SECTION_EXECUTE_PATCH,
        *_stable_patch_specs(),
        STRONG_DIALOGUE_STUB,
        OPCODE1_201_COPY_HELPER_STUB,
        OPCODE1_201_DRAW_HELPER_STUB,
        OPCODE1_COPY_CALLSITE_PATCH,
        OPCODE201_COPY_CALLSITE_PATCH,
        OPCODE1_201_DRAW_CALLSITE_PATCH,
    ]


def _patch_specs_for_profile(profile: str) -> list[BinaryPatchSpec]:
    if profile == "stable-menu16":
        return _stable_patch_specs()
    if profile == "strong-dialogue":
        return _strong_dialogue_patch_specs()
    raise ValueError(f"Unsupported patch profile: {profile}")


def _dialogue_patch_enabled(profile: str) -> bool:
    return profile == "strong-dialogue"


def _legacy_profile_aliases(profile: str) -> list[str]:
    if profile in SUPPORTED_PATCH_PROFILES:
        return []
    raise ValueError(f"Unsupported patch profile: {profile}")


def _patched_callsites(profile: str) -> list[dict[str, Any]]:
    patched = [
        {"function": "sub_414344", "address": "0x414478", "purpose": "opcode 45 menu draw #1"},
        {"function": "sub_414344", "address": "0x4144C1", "purpose": "opcode 45 menu draw #2"},
        {"function": "sub_4144F4", "address": "0x414658", "purpose": "opcode 45 hover redraw #1"},
        {"function": "sub_4144F4", "address": "0x4146E2", "purpose": "opcode 45 hover redraw #2"},
        {"function": "sub_4144F4", "address": "0x414760", "purpose": "opcode 45 hover redraw #3"},
        {"function": "sub_412448", "address": "0x4126ED+", "purpose": "unit sheet panel class/LV/HP/MP/total-attack/stat labels and value strings via shared GDI bridge"},
        {"function": "sub_41C960", "address": "0x41CB2B+", "purpose": "compact unit card labels via shared GDI bridge"},
        {"function": "sub_414B14", "address": "0x414CD6", "purpose": "battle reward popup rows via shared GDI bridge"},
        {"function": "sub_414D40", "address": "0x4153F6", "purpose": "post-battle summary rows via shared GDI bridge"},
        {"function": "sub_4203C0", "address": "0x42045C+", "purpose": "sortie unit roster rows via shared GDI bridge"},
        {"function": "sub_41F27C", "address": "0x41F3E7+", "purpose": "sortie unit roster highlight and page text via shared GDI bridge"},
        {"function": "sub_4208CC", "address": "0x420947+", "purpose": "sortie unit roster summary header and counter via shared GDI bridge"},
        {"function": "sub_4142D4", "address": "0x414319+", "purpose": "item/magic help panel title and body via shared GDI bridge"},
        {"function": "sub_413F80", "address": "0x413FF2+", "purpose": "battle hover unit name and sublabel via shared GDI bridge"},
        {"function": "sub_4210D0", "address": "0x4210E3", "purpose": "camp shop header/status text via shared GDI bridge"},
        {"function": "sub_4210F4 / sub_421478", "address": "0x4211A0 / 0x421524", "purpose": "camp shop item-list x-anchor adjustment for the 12px Chinese UI font"},
        {"function": "sub_4171E0", "address": "0x4172BB / 0x417485", "purpose": "battle skill popup row text anchor and row-box width padding adjustment for the 12px Chinese UI font"},
        {"function": "sub_418308", "address": "0x4183A2 / 0x4183B5", "purpose": "battle skill popup right-side info anchor and width adjustment for the 12px Chinese UI font"},
    ]
    if _dialogue_patch_enabled(profile):
        patched.extend(
            [
                {"function": "sub_40D18C", "address": "0x40D4E7", "purpose": "opcode 1 side-buffer write helper"},
                {"function": "sub_40D18C", "address": "0x40D546", "purpose": "opcode 201 side-buffer write helper"},
                {"function": "sub_4148A4", "address": "0x414A84", "purpose": "opcode 1/201 side-buffer draw helper"},
            ]
        )
    return patched


def _ui_patch_coverage(profile: str) -> dict[str, Any]:
    coverage = {
        "profile": profile,
        "ui_surfaces": list(STABLE_UI_PATCH_COVERAGE["ui_surfaces"]),
    }
    if _dialogue_patch_enabled(profile):
        coverage["ui_surfaces"] = [STRONG_DIALOGUE_PATCH_ENTRY, *coverage["ui_surfaces"]]
    return coverage


def build_runtime_patch_plan(game_dir: Path, profile: str = "stable-menu16") -> dict[str, Any]:
    if profile not in SUPPORTED_PATCH_PROFILES:
        raise ValueError(f"Unsupported patch profile: {profile}")
    patch_specs = _patch_specs_for_profile(profile)
    return {
        "schema_version": 3,
        "profile": profile,
        "legacy_profile_aliases": _legacy_profile_aliases(profile),
        "source_dir": str(game_dir),
        "target_codepage": "system_acp_preserved",
        "encoding_route": {
            "source_files": "cp932",
            "export_storage": "utf-8",
            "repack_target": "cp936",
        },
        "menu_gdi_font_height": MENU_GDI_FONT_HEIGHT,
        "ui_patch_coverage": _ui_patch_coverage(profile),
        "dialogue_side_buffer": {
            "enabled": _dialogue_patch_enabled(profile),
            "lines": DIALOGUE_LINES,
            "bytes_per_line": DIALOGUE_LINE_BYTES,
            "total_bytes": DIALOGUE_TOTAL_BYTES,
            "allocation_bytes": DIALOGUE_ALLOC_BYTES,
            "buffer_ptr_va": f"0x{DIALOGUE_BUFFER_PTR_VA:X}",
            "active_lines_va": f"0x{DIALOGUE_ACTIVE_LINES_VA:X}",
            "copy_helper_va": f"0x{OPCODE1_201_COPY_HELPER_VA:X}",
            "draw_helper_va": f"0x{OPCODE1_201_DRAW_HELPER_VA:X}",
        },
        "patched_callsites": _patched_callsites(profile),
        "patches": [
            {
                "binary": spec.binary_name,
                "va": f"0x{spec.va:X}",
                "expected_hexes": list(spec.expected_hexes),
                "patched_hex": spec.patched_hex,
                "description": spec.description,
            }
            for spec in patch_specs
        ],
        "plan_b_pending": {
            "status": not _dialogue_patch_enabled(profile),
            "detail": (
                "Opcode 45 menu text and first-tier DAT-backed UI surfaces belong to the formal stable-menu16 baseline. "
                "Global CRT codepage forcing is intentionally disabled to preserve original resource path compatibility. "
                "The heap-backed opcode 1/201 dialogue side-buffer path remains experimental and is only enabled by the strong-dialogue profile."
            ),
        },
        "harmony_status": {
            "binary": "HARMONY.DLL",
            "status": "inspected_no_binary_patch_applied",
            "detail": "Current runtime patch updates SRPGEXEC.EXE only: font settings, opcode 45 menu/static/highlight GDI bridging, stable UI fixes, optional strong-dialogue opcode 1/201 side-buffer patches, and resource alias copies under BGM/EFS/BMP for cp932->cp936 filename compatibility. HARMONY.DLL patching remains pending.",
        },
    }


def patch_runtime(game_dir: Path, out_dir: Path, profile: str = "stable-menu16") -> dict[str, Any]:
    if profile not in SUPPORTED_PATCH_PROFILES:
        raise ValueError(f"Unsupported patch profile: {profile}")
    game_dir = game_dir.resolve()
    out_dir = out_dir.resolve()
    _remove_path(out_dir)
    _copy_tree(game_dir, out_dir)
    patch_plan = build_runtime_patch_plan(game_dir, profile)
    resource_aliases = _create_resource_aliases(out_dir)
    resource_alias_summary = _summarize_resource_aliases(resource_aliases)
    patch_results: list[dict[str, Any]] = []
    for spec in _patch_specs_for_profile(profile):
        binary_path = out_dir / spec.binary_name
        patch_results.append(_apply_binary_patch(binary_path, spec))
    report_dir = out_dir / "reports"
    ok_statuses = {"patched", "already_patched"}
    result = {
        "status": "ok" if all(item["status"] in ok_statuses for item in patch_results) else "error",
        "profile": profile,
        "target_codepage": "system_acp_preserved",
        "patched_dir": str(out_dir),
        "patched_binaries": patch_results,
        "patched_callsites": patch_plan["patched_callsites"],
        "ui_patch_coverage": patch_plan["ui_patch_coverage"],
        "dialogue_patch_status": "enabled" if _dialogue_patch_enabled(profile) else "disabled",
        "dialogue_side_buffer": patch_plan["dialogue_side_buffer"],
        "resource_aliases": resource_aliases,
        "resource_alias_summary": resource_alias_summary,
        "plan_b_pending": patch_plan["plan_b_pending"],
    }
    json_dump(report_dir / "runtime_patch_plan.json", patch_plan)
    json_dump(report_dir / "ui_patch_coverage.json", patch_plan["ui_patch_coverage"])
    json_dump(report_dir / "runtime_resource_aliases.json", {"aliases": resource_aliases, "summary": resource_alias_summary})
    json_dump(report_dir / "runtime_patch_result.json", result)
    return result
