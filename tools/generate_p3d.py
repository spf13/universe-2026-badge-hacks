#!/usr/bin/env python3
"""
generate_p3d.py — Custom P3DM 3D Mesh Compiler for GitHub Universe 2026 Badge (pico3d)

Generates a multi-part binary `.p3d` mesh (`P3DM` v1 format) containing:
  - Part 0: Extruded 3D block text (default: "SPF13")
  - Part 1: Outer beveled frame + orbiting crystal octahedra
  - Part 2: Accent bars
  - Part 3: Corner accent studs

Usage:
  python tools/generate_p3d.py --text "SPF13" --out apps/hacker/spf13_logo.p3d
"""

import argparse
import math
import struct


GLYPHS = {
    "S": [
        "1111",
        "1000",
        "1111",
        "0001",
        "1111",
    ],
    "P": [
        "1111",
        "1001",
        "1111",
        "1000",
        "1000",
    ],
    "F": [
        "1111",
        "1000",
        "1110",
        "1000",
        "1000",
    ],
    "1": [
        "0110",
        "0010",
        "0010",
        "0010",
        "0111",
    ],
    "3": [
        "1111",
        "0001",
        "0111",
        "0001",
        "1111",
    ],
}


class MeshPart:
    def __init__(self):
        self.verts = []
        self.norms = []
        self.tris = []

    def add_box(self, cx, cy, cz, sx, sy, sz):
        hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
        faces = [
            ((0, 0, 1),  [(-hx, -hy,  hz), ( hx, -hy,  hz), ( hx,  hy,  hz), (-hx,  hy,  hz)]),
            ((0, 0, -1), [( hx, -hy, -hz), (-hx, -hy, -hz), (-hx,  hy, -hz), ( hx,  hy, -hz)]),
            ((1, 0, 0),  [( hx, -hy,  hz), ( hx, -hy, -hz), ( hx,  hy, -hz), ( hx,  hy,  hz)]),
            ((-1, 0, 0), [(-hx, -hy, -hz), (-hx, -hy,  hz), (-hx,  hy,  hz), (-hx,  hy, -hz)]),
            ((0, 1, 0),  [(-hx,  hy,  hz), ( hx,  hy,  hz), ( hx,  hy, -hz), (-hx,  hy, -hz)]),
            ((0, -1, 0), [(-hx, -hy, -hz), ( hx, -hy, -hz), ( hx, -hy,  hz), (-hx, -hy,  hz)]),
        ]
        for (nx, ny, nz), corners in faces:
            base = len(self.verts)
            for vx, vy, vz in corners:
                self.verts.append((cx + vx, cy + vy, cz + vz))
                self.norms.append((nx, ny, nz))
            self.tris.append((base, base + 1, base + 2))
            self.tris.append((base, base + 2, base + 3))

    def add_octahedron(self, cx, cy, cz, r):
        pts = [
            (cx, cy + r, cz),
            (cx, cy - r, cz),
            (cx + r, cy, cz),
            (cx - r, cy, cz),
            (cx, cy, cz + r),
            (cx, cy, cz - r),
        ]
        oct_tris = [
            (0, 4, 2), (0, 2, 5), (0, 5, 3), (0, 3, 4),
            (1, 2, 4), (1, 5, 2), (1, 3, 5), (1, 4, 3),
        ]
        for i0, i1, i2 in oct_tris:
            p0, p1, p2 = pts[i0], pts[i1], pts[i2]
            ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
            vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
            nx = uy * vz - uz * vy
            ny = uz * vx - ux * vz
            nz = ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            nx, ny, nz = nx / ln, ny / ln, nz / ln
            base = len(self.verts)
            for p in (p0, p1, p2):
                self.verts.append(p)
                self.norms.append((nx, ny, nz))
            self.tris.append((base, base + 1, base + 2))


def build_p3d(text: str, out_path: str):
    parts = [MeshPart() for _ in range(4)]
    cell = 0.065
    total_cols = len(text) * 5 - 1
    start_x = -total_cols * cell * 0.5

    for idx, ch in enumerate(text.upper()):
        grid = GLYPHS.get(ch, GLYPHS["S"])
        ox = start_x + idx * 5 * cell
        for r, row in enumerate(grid):
            for c, val in enumerate(row):
                if val == "1":
                    x = ox + c * cell
                    y = (2 - r) * cell
                    parts[0].add_box(x, y, 0.0, cell * 0.92, cell * 0.92, 0.14)

    # Outer frame & orbiting crystals
    w = total_cols * cell * 0.58 + 0.12
    h = 0.26
    parts[1].add_box(0.0,  h, 0.0, w * 2, 0.035, 0.08)
    parts[1].add_box(0.0, -h, 0.0, w * 2, 0.035, 0.08)
    parts[2].add_box(-w, 0.0, 0.0, 0.035, h * 2, 0.08)
    parts[2].add_box( w, 0.0, 0.0, 0.035, h * 2, 0.08)

    for angle_deg in range(0, 360, 45):
        rad = math.radians(angle_deg)
        ox = math.cos(rad) * (w + 0.12)
        oy = math.sin(rad) * (h + 0.12)
        parts[3].add_octahedron(ox, oy, 0.0, 0.055)

    with open(out_path, "wb") as f:
        f.write(struct.pack("<4sHH", b"P3DM", 1, len(parts)))
        for p in parts:
            f.write(struct.pack("<HH", len(p.verts), len(p.tris)))
            for vx, vy, vz in p.verts:
                ix = max(-32767, min(32767, int(round(vx * 32767))))
                iy = max(-32767, min(32767, int(round(vy * 32767))))
                iz = max(-32767, min(32767, int(round(vz * 32767))))
                f.write(struct.pack("<hhh", ix, iy, iz))
            for nx, ny, nz in p.norms:
                inx = max(-127, min(127, int(round(nx * 127))))
                iny = max(-127, min(127, int(round(ny * 127))))
                inz = max(-127, min(127, int(round(nz * 127))))
                f.write(struct.pack("<bbb", inx, iny, inz))
            for i0, i1, i2 in p.tris:
                f.write(struct.pack("<HHH", i0, i1, i2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compile P3DM 3D mesh for pico3d")
    parser.add_argument("--text", default="SPF13", help="5-row block text to extrude")
    parser.add_argument("--out", default="apps/hacker/spf13_logo.p3d", help="Output .p3d path")
    args = parser.parse_args()
    build_p3d(args.text, args.out)
    print(f"Wrote {args.out}")
