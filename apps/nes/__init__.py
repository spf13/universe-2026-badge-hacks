# spf13 labs NES Emulator - App & Launcher Integration
# High-performance RP2350 (300 MHz ARM Thumb-2) retro gaming for GitHub Universe 2026 Badge

import array
import gc
import os
import sys

try:
    import machine
except ImportError:
    machine = None

APP_DIR = "/system/apps/nes"
try:
    os.chdir(APP_DIR)
except Exception:
    pass
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if "/" not in sys.path:
    sys.path.insert(0, "/")

try:
    badge.mode(HIRES | VSYNC)
    badge.default_clear = None
    screen.antialias = image.OFF
    badge.autorotate(True)
    display.set_async(False)
    gc.collect()
except Exception:
    pass

from emu import (
    NES,
    NES_PALETTE,
    apply_scanlines_16,
    apply_scanlines_32,
)

# spf13 labs Branding Palette
COLOR_CERULEAN = color.rgb(0, 106, 149)
COLOR_MAGENTA  = color.rgb(150, 25, 138)
COLOR_GOLD     = color.rgb(249, 193, 110)
COLOR_VIOLET   = color.rgb(186, 110, 249)
COLOR_CREAM    = color.rgb(248, 250, 244)
COLOR_DARK_BG  = color.rgb(14, 16, 26)
COLOR_SURFACE  = color.rgb(24, 28, 44)
COLOR_SIDEBAR  = color.rgb(10, 12, 20)
COLOR_WHITE    = color.rgb(255, 255, 255)
COLOR_DIM      = color.rgb(140, 145, 160)
COLOR_ACCENT   = color.rgb(0, 180, 230)

KNOWN_GAMES = {
    "zelda.nes": ("Legend of Zelda", "MMC1", True),
    "ff1.nes": ("Final Fantasy", "MMC1", True),
    "dw1.nes": ("Dragon Warrior", "MMC1", True),
    "smb.nes": ("Super Mario Bros", "NROM", False),
    "tetris.nes": ("Tetris", "MMC1", False),
    "contra.nes": ("Contra", "UxROM", False),
    "castlevania.nes": ("Castlevania", "UxROM", False),
    "megaman2.nes": ("Mega Man 2", "MMC1", False),
    "metroid.nes": ("Metroid", "MMC1", True),
    "dk_classics.nes": ("Donkey Kong Classics", "CNROM", False),
    "pacman.nes": ("Pac-Man", "NROM", False),
    "galaga.nes": ("Galaga", "NROM", False),
    "excitebike.nes": ("Excitebike", "NROM", False),
    "drmario.nes": ("Dr. Mario", "MMC1", False),
}

DEFAULT_ORDER = [
    "zelda.nes", "ff1.nes", "dw1.nes", "smb.nes", "tetris.nes", "contra.nes",
    "castlevania.nes", "megaman2.nes", "metroid.nes", "dk_classics.nes",
    "pacman.nes", "galaga.nes", "excitebike.nes", "drmario.nes",
]

def _discover_games():
    found = []
    seen = set()
    roms_dir = f"{APP_DIR}/roms"
    try:
        files = sorted(os.listdir(roms_dir))
    except Exception:
        files = []
    for rom in DEFAULT_ORDER + files:
        if not rom.lower().endswith(".nes") or rom in seen:
            continue
        if files and rom not in files:
            continue
        seen.add(rom)
        if rom in KNOWN_GAMES:
            title, mapper, battery = KNOWN_GAMES[rom]
        else:
            title = rom[:-4].replace("_", " ").replace("-", " ").title()
            mapper, battery = "ROM", True
        found.append((rom, title, mapper, battery))
    if not found:
        for rom in DEFAULT_ORDER:
            title, mapper, battery = KNOWN_GAMES[rom]
            found.append((rom, title, mapper, battery))
    return found

GAMES = _discover_games()

GAME_SLUGS = {
    "zelda.nes": "ZLD",
    "ff1.nes": "FF1",
    "dw1.nes": "DW1",
    "smb.nes": "SMB",
    "tetris.nes": "TET",
    "contra.nes": "CTR",
    "castlevania.nes": "CAS",
    "megaman2.nes": "MM2",
    "metroid.nes": "MET",
    "dk_classics.nes": "DKC",
    "pacman.nes": "PAC",
    "galaga.nes": "GAL",
    "excitebike.nes": "EXC",
    "drmario.nes": "DRM",
}

PAUSE_OPTIONS = [
    "Resume Game",
    "Save State Now",
    "Frame Skip",
    "Toggle Scanlines",
    "Reset Console",
    "Exit to ROM List",
]

AUTO_SAVE_IDLE_MS = 60000

class NESApp:
    MODE_LAUNCHER = 0
    MODE_GAME     = 1
    MODE_PAUSE    = 2

    def __init__(self):
        self.mode = self.MODE_LAUNCHER
        self.selected_index = 0
        self.scroll_top = 0
        self.scanlines_enabled = False
        self.frameskip_enabled = True

        self.nes = None
        self.active_game = None
        self.pause_index = 0
        self.menu_hold_start = None

        self.fps = 60
        self.fps_counter = 0
        self.fps_timer = 0
        self.last_autosave_ms = 0
        self.sram_msg_timer = 0
        self.sram_msg_text = "SAV"

        # Detect screen bit depth
        try:
            self.pixel_bytes = len(screen.raw) // (320 * 240)
        except Exception:
            self.pixel_bytes = 2

        # Build hardware palette lookup table from display image pen
        self.hw_pal = self._build_hw_pal()
        if self.pixel_bytes == 2:
            self.frame_pal = array.array("H", [0] * 32)
        else:
            self.frame_pal = array.array("I", [0] * 32)

        # Preload spf13 branding assets
        self.img_wide = self._load_asset("spf13_wide.png")
        self.img_logo = self._load_asset("spf13_logo.png")
        self._saved_states = set()
        self._refresh_saved_states()

        # Hook physical HOME button IRQ to save state & exit cleanly to MonaOS menu
        try:
            if machine is not None and hasattr(machine, "Pin") and hasattr(machine.Pin, "board"):
                machine.Pin.board.BUTTON_HOME.irq(
                    trigger=machine.Pin.IRQ_FALLING,
                    handler=self._home_irq_handler,
                )
        except Exception:
            pass

    def _refresh_saved_states(self):
        self._saved_states.clear()
        try:
            for fn in os.listdir("/state"):
                if fn.startswith("nes_") and fn.endswith(".state"):
                    self._saved_states.add(fn[4:-6])
        except Exception:
            pass

    def _load_asset(self, filename):
        for candidate in (f"{APP_DIR}/assets/{filename}", f"assets/{filename}", f"F:/apps/nes/assets/{filename}"):
            try:
                if file_exists(candidate):
                    return image.load(candidate)
            except Exception:
                pass
        return None

    def _build_hw_pal(self):
        try:
            lut_img = image(64, 1, bytearray(64 * self.pixel_bytes), image.RGB565 if self.pixel_bytes == 2 else image.RGBA)
            for i, rgb in enumerate(NES_PALETTE):
                lut_img.pen = color.rgb(*rgb)
                lut_img.put(i, 0)
            import struct
            fmt = "<64H" if self.pixel_bytes == 2 else "<64I"
            arr_t = "H" if self.pixel_bytes == 2 else "I"
            return array.array(arr_t, struct.unpack(fmt, lut_img.raw))
        except Exception:
            if self.pixel_bytes == 2:
                arr = array.array("H", [0] * 64)
                for i, (r, g, b) in enumerate(NES_PALETTE):
                    arr[i] = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                return arr
            else:
                return array.array("I", [(r << 16) | (g << 8) | b for r, g, b in NES_PALETTE])

    def _quit_to_menu(self):
        try:
            if self.nes is not None:
                self.nes.save_state(sync_sram=True)
            badge.autorotate(True)
        except Exception:
            pass
        reset()

    def _home_irq_handler(self, _pin):
        self._quit_to_menu()

    def _has_saved_state(self, rom_file):
        return rom_file.replace(".nes", "") in self._saved_states

    def launch_game(self, game_entry):
        rom_file, title, mapper, battery = game_entry
        rom_path = f"{APP_DIR}/roms/{rom_file}"
        if not file_exists(rom_path):
            rom_path = f"F:/apps/nes/roms/{rom_file}"

        slug = rom_file.replace(".nes", "")
        sram_path = f"/state/nes_{slug}.sav" if battery else None
        state_path = f"/state/nes_{slug}.state"

        self.nes = NES(rom_path, sram_path=sram_path, state_path=state_path)
        self.active_game = game_entry
        if self.nes.load_state():
            self.sram_msg_text = "RES"
            self.sram_msg_timer = 60
        else:
            self.sram_msg_text = "SAV"
            self.sram_msg_timer = 0

        self.mode = self.MODE_GAME
        self.sidebar_dirty = 4
        now = badge.ticks
        self.fps_timer = now
        self.last_autosave_ms = now
        self.fps_counter = 0

    def update(self):
        # Physical mechanical HOME button on back/edge always saves and exits to MonaOS
        if badge.buttons.pressed(BUTTON_HOME):
            self._quit_to_menu()
            return None

        if self.mode == self.MODE_LAUNCHER:
            return self.update_launcher()
        elif self.mode == self.MODE_GAME:
            return self.update_game()
        elif self.mode == self.MODE_PAUSE:
            return self.update_pause()
        return None

    def update_launcher(self):
        screen.pen = COLOR_DARK_BG
        screen.clear()

        # Header branding
        if self.img_wide is not None:
            screen.blit(self.img_wide, vec2((320 - self.img_wide.width) // 2, 8))
        else:
            screen.pen = COLOR_GOLD
            screen.text("spf13 labs", 100, 12)

        screen.pen = COLOR_CREAM
        screen.text("NES EMULATOR", 104, 52)

        # Navigation (supports both gamepad badge.pad and badge.buttons; auto-rotates 180 when upside-down)
        if badge.pad.pressed(BUTTON_UP) or badge.buttons.pressed(BUTTON_UP) or badge.pressed(BUTTON_UP):
            self.selected_index = max(0, self.selected_index - 1)
            if self.selected_index < self.scroll_top:
                self.scroll_top = self.selected_index
        elif (
            badge.pad.pressed(BUTTON_DOWN)
            or badge.buttons.pressed(BUTTON_DOWN)
            or badge.pressed(BUTTON_DOWN)
            or badge.pad.pressed(BUTTON_MENU)
        ):
            self.selected_index = min(len(GAMES) - 1, self.selected_index + 1)
            if self.selected_index >= self.scroll_top + 4:
                self.scroll_top = self.selected_index - 3

        # Press A (BUTTON_BACK), B (BUTTON_SELECT), or START (BUTTON_HOME) to launch the selected game
        if (
            badge.pad.pressed(BUTTON_BACK)
            or badge.pad.pressed(BUTTON_SELECT)
            or badge.pad.pressed(BUTTON_HOME)
            or badge.buttons.pressed(BUTTON_SELECT)
            or badge.buttons.pressed(BUTTON_RIGHT)
        ):
            self.launch_game(GAMES[self.selected_index])
            return None

        # Mechanical left button exits back to badge menu
        if badge.buttons.pressed(BUTTON_LEFT):
            self._quit_to_menu()

        # Draw visible game cards (4 cards)
        card_y = 70
        for i in range(self.scroll_top, min(len(GAMES), self.scroll_top + 4)):
            rom_file, title, mapper, battery = GAMES[i]
            is_selected = (i == self.selected_index)

            # Card background
            screen.pen = COLOR_CERULEAN if is_selected else COLOR_SURFACE
            screen.rectangle(20, card_y, 280, 32)

            if is_selected:
                screen.pen = COLOR_GOLD
                screen.rectangle(20, card_y, 4, 32)
                screen.pen = COLOR_WHITE
            else:
                screen.pen = COLOR_CREAM

            screen.text(title, 32, card_y + 8)

            # Mapper badge
            screen.pen = COLOR_VIOLET
            screen.text(mapper, 218, card_y + 8)

            # State / Battery SRAM badge
            if self._has_saved_state(rom_file):
                screen.pen = COLOR_ACCENT
                screen.text("[STA]", 258, card_y + 8)
            elif battery:
                screen.pen = COLOR_GOLD
                screen.text("[SAV]", 258, card_y + 8)

            card_y += 36

        # Footer hints
        screen.pen = COLOR_DIM
        screen.text("Gamepad: Move & Launch   HOME: Exit", 40, 218)
        return None

    def update_game(self):
        pad_dir = badge.pad.direction()
        d_up = (pad_dir.y < -0.25) or badge.pad.held(BUTTON_UP) or badge.buttons.held(BUTTON_UP)
        d_down = (pad_dir.y > 0.25) or badge.pad.held(BUTTON_DOWN) or badge.buttons.held(BUTTON_DOWN)
        d_left = (pad_dir.x < -0.25) or badge.pad.held(BUTTON_LEFT)
        d_right = (pad_dir.x > 0.25) or badge.pad.held(BUTTON_RIGHT)

        top_a = badge.pad.held(BUTTON_BACK)     # Action pad A on gamepad
        top_b = badge.pad.held(BUTTON_SELECT)   # Action pad B on gamepad

        mid_start = badge.pad.held(BUTTON_HOME)   # Right middle button on gamepad -> START
        mid_select = badge.pad.held(BUTTON_MENU)  # Left middle button on gamepad -> SELECT

        both_top = top_a and top_b
        both_mid = mid_start and mid_select
        now = badge.ticks
        tap_both_start = False

        if both_top or both_mid:
            if self.menu_hold_start is None:
                self.menu_hold_start = now
            elif now - self.menu_hold_start >= 650:
                if self.nes is not None:
                    self.nes.save_state(sync_sram=True)
                    self.sram_msg_text = "SAV"
                    self.sram_msg_timer = 60
                self.mode = self.MODE_PAUSE
                self.menu_hold_start = None
                return None
        else:
            if self.menu_hold_start is not None:
                if now - self.menu_hold_start < 550 and not mid_start and not mid_select:
                    self.start_pulse = 6
                self.menu_hold_start = None

        if getattr(self, "start_pulse", 0) > 0:
            self.start_pulse -= 1
            tap_both_start = True

        btn_start = (
            mid_start
            or badge.buttons.held(BUTTON_SELECT)
            or tap_both_start
        )
        btn_select = (
            mid_select
            or badge.buttons.held(BUTTON_RIGHT)
            or badge.buttons.held(BUTTON_LEFT)
        )
        btn_a = top_a and not both_top
        btn_b = top_b and not both_top

        # Build NES controller 1 shift bitfield
        pad = 0
        if btn_a:      pad |= 0x01
        if btn_b:      pad |= 0x02
        if btn_select: pad |= 0x04
        if btn_start:  pad |= 0x08
        if d_up:       pad |= 0x10
        if d_down:     pad |= 0x20
        if d_left:     pad |= 0x40
        if d_right:    pad |= 0x80

        # Idle auto-save every 60s when no buttons are held (also saves on Pause, Exit, and HOME)
        if now - self.last_autosave_ms >= AUTO_SAVE_IDLE_MS and pad == 0:
            if self.nes.save_state(sync_sram=False):
                self.sram_msg_text = "SAV"
                self.sram_msg_timer = 45
            self.last_autosave_ms = badge.ticks
            now = self.last_autosave_ms

        # Step NES emulation (1 step by default for smooth VSYNC, or 2 steps when Frame Skip enabled)
        steps = 2 if self.frameskip_enabled else 1
        for _ in range(steps):
            self.nes.step_frame(pad)

        # Map active PPU palette through display hardware LUT
        hw = self.hw_pal
        fpal = self.frame_pal
        pmem = self.nes.mem
        for i in range(32):
            fpal[i] = hw[pmem[0x0900 + i] & 0x3F]

        # Render left sidebar every frame first so screen.* opens the display fence before raw ptr16 writes
        screen.pen = COLOR_SIDEBAR
        screen.rectangle(0, 0, 32, 240)
        if self.img_logo is not None:
            screen.blit(self.img_logo, vec2(0, 4))
        else:
            screen.pen = COLOR_GOLD
            screen.text("spf", 4, 8)

        # FPS calculation & display
        self.fps_counter += 1
        if now - self.fps_timer >= 1000:
            self.fps = self.fps_counter
            self.fps_counter = 0
            self.fps_timer = now

        screen.pen = COLOR_ACCENT
        screen.text(f"{self.fps}", 6, 46)
        screen.pen = COLOR_DIM
        screen.text("fps", 6, 58)

        # Live Gamepad Input Visualizer in left sidebar
        cx, cy = 16, 118
        for dx, dy, active in ((0, -8, d_up), (-8, 0, d_left), (8, 0, d_right), (0, 8, d_down)):
            screen.pen = COLOR_GOLD if active else COLOR_SURFACE
            screen.circle(cx + dx, cy + dy, 3)
        screen.pen = COLOR_ACCENT if btn_b else COLOR_SURFACE
        screen.circle(10, 156, 4)
        screen.pen = COLOR_GOLD if btn_a else COLOR_SURFACE
        screen.circle(22, 148, 4)
        screen.pen = COLOR_VIOLET if btn_select else COLOR_SURFACE
        screen.rectangle(4, 172, 10, 4)
        screen.pen = COLOR_GOLD if btn_start else COLOR_SURFACE
        screen.rectangle(18, 172, 10, 4)

        # Render right sidebar every frame so it never blinks (x=288..319)
        screen.pen = COLOR_SIDEBAR
        screen.rectangle(288, 0, 32, 240)

        slug = GAME_SLUGS.get(self.active_game[0], "NES")
        screen.pen = COLOR_GOLD
        screen.text(slug, 292, 8)

        # Controls legend in right sidebar
        screen.pen = COLOR_GOLD
        screen.text("PAD", 290, 34)
        screen.pen = COLOR_CREAM
        screen.text("A:UR", 290, 48)
        screen.text("B:LL", 290, 60)
        screen.text("St:M1", 289, 76)
        screen.text("Sl:M2", 289, 88)

        screen.pen = COLOR_GOLD
        screen.text("MENU", 289, 114)
        screen.pen = COLOR_CREAM
        screen.text("Hold", 290, 128)
        screen.text("A+B", 290, 140)

        if self.sram_msg_timer > 0:
            self.sram_msg_timer -= 1
            screen.pen = COLOR_GOLD
            screen.text(self.sram_msg_text, 291, 218)

        # Render 256x240 NES screen directly into screen.raw at x=32 AFTER screen.* calls
        fb = screen.raw
        if hasattr(fb, "cast"):
            fb = memoryview(fb).cast("H" if self.pixel_bytes == 2 else "I")

        self.nes.render(fb, fpal, self.pixel_bytes, scanlines=self.scanlines_enabled)

        return None

    def update_pause(self):
        if badge.pressed(BUTTON_UP) or badge.pad.pressed(BUTTON_UP):
            self.pause_index = (self.pause_index - 1) % len(PAUSE_OPTIONS)
        elif badge.pressed(BUTTON_DOWN) or badge.pad.pressed(BUTTON_DOWN) or badge.pad.pressed(BUTTON_MENU):
            self.pause_index = (self.pause_index + 1) % len(PAUSE_OPTIONS)

        if badge.buttons.pressed(BUTTON_LEFT):
            self.sidebar_dirty = 4
            self.mode = self.MODE_GAME
            return None

        if (
            badge.pad.pressed(BUTTON_BACK)
            or badge.pad.pressed(BUTTON_SELECT)
            or badge.pad.pressed(BUTTON_HOME)
            or badge.buttons.pressed(BUTTON_SELECT)
        ):
            choice = self.pause_index
            if choice == 0:  # Resume Game
                self.sidebar_dirty = 4
                self.mode = self.MODE_GAME
            elif choice == 1:  # Save State Now
                if self.nes is not None:
                    self.nes.save_state(sync_sram=True)
                    self.sram_msg_text = "SAV"
                    self.sram_msg_timer = 90
                    self.last_autosave_ms = badge.ticks
                self.sidebar_dirty = 4
                self.mode = self.MODE_GAME
            elif choice == 2:  # Toggle Frame Skip
                self.frameskip_enabled = not self.frameskip_enabled
            elif choice == 3:  # Toggle Scanlines
                self.scanlines_enabled = not self.scanlines_enabled
            elif choice == 4:  # Reset Console (Fresh Boot)
                if self.nes is not None:
                    self.nes.reset_console()
                    self.nes.save_state(sync_sram=False)
                self.last_autosave_ms = badge.ticks
                self.sidebar_dirty = 4
                self.mode = self.MODE_GAME
            elif choice == 5:  # Exit to ROM list
                if self.nes is not None:
                    self.nes.save_state(sync_sram=True)
                self._refresh_saved_states()
                self.nes = None
                self.active_game = None
                self.mode = self.MODE_LAUNCHER
            return None

        # Draw pause dialog
        screen.pen = COLOR_DARK_BG
        screen.rectangle(44, 28, 232, 184)
        screen.pen = COLOR_GOLD
        screen.rectangle(44, 28, 232, 2)
        screen.rectangle(44, 210, 232, 2)
        screen.rectangle(44, 28, 2, 184)
        screen.rectangle(274, 28, 2, 184)

        screen.pen = COLOR_CREAM
        screen.text("spf13 labs - PAUSED", 82, 36)

        opt_y = 60
        for idx, opt in enumerate(PAUSE_OPTIONS):
            is_sel = (idx == self.pause_index)
            if is_sel:
                screen.pen = COLOR_CERULEAN
                screen.rectangle(50, opt_y - 2, 220, 20)
                screen.pen = COLOR_GOLD
                screen.rectangle(50, opt_y - 2, 3, 20)
                screen.pen = COLOR_WHITE
            else:
                screen.pen = COLOR_CREAM

            label = opt
            if idx == 2:
                label = "Frame Skip: 2x ON" if self.frameskip_enabled else "Frame Skip: OFF"
            elif idx == 3:
                label = "CRT Scanlines: ON" if self.scanlines_enabled else "CRT Scanlines: OFF"

            screen.text(label, 60, opt_y + 2)
            opt_y += 24

        return None

app = NESApp()

def on_exit():
    try:
        if app.nes is not None:
            app.nes.save_state(sync_sram=True)
        badge.autorotate(True)
    except Exception:
        pass

run(app.update)
