# spf13 labs - 60 FPS Viper IMU Neon Sand Physics & 3D Custom Mesh Showcase
# Runs on RP2350 (ARM Thumb-2 @micropython.viper + pico3d dual-core rasterizer)

import array
import builtins
import struct

try:
    import micropython
except ImportError:
    class _mp:
        @staticmethod
        def viper(fn):
            return fn
    micropython = _mp()


@micropython.viper
def step_and_render_sand_16(grid_buf, fb_buf, pal_buf, flags: int):
    grid = ptr8(grid_buf)
    fb = ptr16(fb_buf)
    pal = ptr16(pal_buf)

    # flags encoding (all unsigned positive ints):
    #   bits 0..1: gx_mode (0 => -1, 1 => 0, 2 => +1)
    #   bits 2..3: gy_mode (0 => -1, 1 => 0, 2 => +1)
    #   bit  4:    parity  (0 or 1)
    gx: int = (flags & 3) - 1
    gy: int = ((flags >> 2) & 3) - 1
    parity: int = (flags >> 4) & 1

    # 1. Cellular Automata Gravity & Fluid Step (160x100 grid)
    y: int = 98 if gy >= 0 else 1
    while y >= 1 and y < 99:
        row_off: int = y * 160
        x: int = 158 if gx >= 0 else 1
        while x >= 1 and x < 159:
            idx: int = row_off + x
            c: int = int(grid[idx])
            if c >= 1 and c <= 5:
                moved: int = 0
                ny: int = y + gy
                nx: int = x + gx
                if ny >= 1 and ny < 99 and nx >= 1 and nx < 159:
                    n_idx: int = ny * 160 + nx
                    if int(grid[n_idx]) == 0:
                        grid[n_idx] = c
                        grid[idx] = 0
                        moved = 1

                if moved == 0:
                    d1: int = 1
                    d2: int = -1
                    if ((x + y + parity) & 1) == 0:
                        d1 = -1
                        d2 = 1

                    if gy != 0:
                        ny1: int = y + gy
                        if ny1 >= 1 and ny1 < 99:
                            nx1: int = x + d1
                            if nx1 >= 1 and nx1 < 159 and int(grid[ny1 * 160 + nx1]) == 0:
                                grid[ny1 * 160 + nx1] = c
                                grid[idx] = 0
                                moved = 1
                            else:
                                nx2: int = x + d2
                                if nx2 >= 1 and nx2 < 159 and int(grid[ny1 * 160 + nx2]) == 0:
                                    grid[ny1 * 160 + nx2] = c
                                    grid[idx] = 0
                                    moved = 1
                    elif gx != 0:
                        nx1b: int = x + gx
                        if nx1b >= 1 and nx1b < 159:
                            ny1b: int = y + d1
                            if ny1b >= 1 and ny1b < 99 and int(grid[ny1b * 160 + nx1b]) == 0:
                                grid[ny1b * 160 + nx1b] = c
                                grid[idx] = 0
                                moved = 1
                            else:
                                ny2b: int = y + d2
                                if ny2b >= 1 and ny2b < 99 and int(grid[ny2b * 160 + nx1b]) == 0:
                                    grid[ny2b * 160 + nx1b] = c
                                    grid[idx] = 0
                                    moved = 1

                # Fluid lateral dispersion for Cerulean (2) and Violet (4) particles
                if moved == 0 and (c == 2 or c == 4):
                    sx: int = (x + 1) if ((x + parity) & 1) else (x - 1)
                    if sx >= 1 and sx < 159 and int(grid[row_off + sx]) == 0:
                        grid[row_off + sx] = c
                        grid[idx] = 0

            if gx >= 0:
                x = x - 1
            else:
                x = x + 1

        if gy >= 0:
            y = y - 1
        else:
            y = y + 1

    # 2. Direct 2x2 Pixel Blit into screen.raw (y = 20..219, x = 0..319)
    for ry in range(100):
        g_row: int = ry * 160
        fb_row0: int = (20 + (ry << 1)) * 320
        fb_row1: int = fb_row0 + 320
        for rx in range(160):
            col16: int = int(pal[int(grid[g_row + rx])])
            px: int = rx << 1
            fb[fb_row0 + px] = col16
            fb[fb_row0 + px + 1] = col16
            fb[fb_row1 + px] = col16
            fb[fb_row1 + px + 1] = col16


GLYPHS_5X7 = {
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
}

SCENE_NAMES = (
    "SPF13 Collider",
    "Hourglass",
    "Plinko Pegs",
    "Open Basin",
)


class SandToy:
    def __init__(self, screen_obj, color_mod):
        self.screen = screen_obj
        self.color = color_mod
        self.grid = bytearray(160 * 100)
        self.scene_idx = 0
        self.parity = 0
        self.particle_count = 0
        self.pal = self._build_palette()
        self.load_scene(0)

    def _build_palette(self):
        rgbs = [
            (14, 16, 26),     # 0: Empty dark bg
            (249, 193, 110),  # 1: spf13 Gold sand
            (0, 180, 230),    # 2: spf13 Cerulean fluid
            (210, 45, 190),   # 3: spf13 Magenta neon
            (186, 110, 249),  # 4: spf13 Violet plasma
            (46, 210, 115),   # 5: GitHub Neon Green
            (255, 255, 255),  # 6
            (255, 255, 255),  # 7
            (255, 255, 255),  # 8
            (248, 250, 244),  # 9: Cream solid obstacle wall
        ]
        try:
            img_cls = getattr(builtins, "image")
            lut = img_cls(16, 1, bytearray(32), img_cls.RGB565)
            for i, (r, g, b) in enumerate(rgbs):
                lut.pen = self.color.rgb(r, g, b)
                lut.put(i, 0)
            return array.array("H", struct.unpack("<16H", lut.raw))
        except Exception:
            arr = array.array("H", [0] * 16)
            for i, (r, g, b) in enumerate(rgbs):
                arr[i] = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            return arr

    def _clear_borders(self):
        g = self.grid
        for i in range(16000):
            g[i] = 0
        for x in range(160):
            g[x] = 9
            g[99 * 160 + x] = 9
        for y in range(100):
            g[y * 160] = 9
            g[y * 160 + 159] = 9

    def load_scene(self, idx):
        self.scene_idx = idx % len(SCENE_NAMES)
        self._clear_borders()
        g = self.grid

        if self.scene_idx == 0:
            word = "SPF13"
            scale = 3
            start_x = 36
            start_y = 38
            for ch_i, ch in enumerate(word):
                rows = GLYPHS_5X7[ch]
                for r_i, row in enumerate(rows):
                    for c_i, bit in enumerate(row):
                        if bit == "1":
                            bx = start_x + (ch_i * 6 + c_i) * scale
                            by = start_y + r_i * scale
                            for dy in range(scale):
                                for dx in range(scale):
                                    g[(by + dy) * 160 + (bx + dx)] = 9
            for i in range(22):
                g[(20 + i // 2) * 160 + (12 + i)] = 9
                g[(20 + i // 2) * 160 + (147 - i)] = 9

        elif self.scene_idx == 1:
            for i in range(42):
                g[(10 + i) * 160 + (28 + i)] = 9
                g[(10 + i) * 160 + (29 + i)] = 9
                g[(10 + i) * 160 + (131 - i)] = 9
                g[(10 + i) * 160 + (130 - i)] = 9
                g[(89 - i) * 160 + (28 + i)] = 9
                g[(89 - i) * 160 + (29 + i)] = 9
                g[(89 - i) * 160 + (131 - i)] = 9
                g[(89 - i) * 160 + (130 - i)] = 9

        elif self.scene_idx == 2:
            for row in range(18, 86, 12):
                offset = 10 if ((row // 12) & 1) else 4
                for col in range(16 + offset, 144, 14):
                    for dy in range(-1, 2):
                        for dx in range(-1, 2):
                            g[(row + dy) * 160 + (col + dx)] = 9

        self.particle_count = 0
        self.spawn_burst(2400)

    def spawn_burst(self, count=400):
        g = self.grid
        added = 0
        for y in range(6, 34):
            row = y * 160
            for x in range(16, 144, 2):
                if g[row + x] == 0:
                    c = 1 + ((x + y) % 5)
                    g[row + x] = c
                    added += 1
                    if added >= count:
                        self.particle_count += added
                        return
        self.particle_count += added

    def stir_vortex(self):
        g = self.grid
        for y in range(50, 95, 2):
            r_src = y * 160
            r_dst = (100 - y) * 160
            for x in range(10, 150, 2):
                c = g[r_src + x]
                if 1 <= c <= 5 and g[r_dst + (159 - x)] == 0:
                    g[r_dst + (159 - x)] = c
                    g[r_src + x] = 0
