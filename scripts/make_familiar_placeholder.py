"""使い魔の仮画像 ``assets/familiars/default.png`` を生成する。

正式画像が用意できるまでの代替画像です。外部ライブラリを追加せずに済むよう、
標準ライブラリだけで最小構成のPNGを書き出します。

    python scripts/make_familiar_placeholder.py

正式画像を配置したあとも、IDに対応する画像が無い個体はこの画像を表示します。
"""

from __future__ import annotations

import struct
import sys
import zlib

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "assets" / "familiars" / "default.png"

SIZE = 256

# docs/GAME_SPEC.md の表示色に合わせた配色
BACKGROUND = (0x2B, 0x2D, 0x31)  # 灰
PLATE = (0x3A, 0x3D, 0x44)
ACCENT = (0x5C, 0x21, 0xFF)  # 紫
ACCENT_LIGHT = (0xBE, 0xDB, 0xFF)  # 青


def _diamond_distance(x: int, y: int) -> float:
    """中心からのひし形距離（|dx| + |dy|）を返す。"""

    center = SIZE / 2
    return abs(x - center + 0.5) + abs(y - center + 0.5)


def _pixel(x: int, y: int) -> tuple[int, int, int]:
    distance = _diamond_distance(x, y)

    if distance < 52:
        return ACCENT_LIGHT
    if distance < 62:
        return ACCENT
    if distance < 70:
        return PLATE
    if distance < 78:
        return ACCENT
    if distance < 108:
        return PLATE

    return BACKGROUND


def build_png() -> bytes:
    """RGBのPNGバイト列を組み立てる。"""

    rows = bytearray()
    for y in range(SIZE):
        rows.append(0)  # フィルタ種別: None
        for x in range(SIZE):
            rows.extend(_pixel(x, y))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return (
            struct.pack(">I", len(data))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + chunk(b"IEND", b"")
    )


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(build_png())

    print(f"仮画像を生成しました: {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
