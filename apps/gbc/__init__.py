# spf13 labs Game Boy / Game Boy Color (DMG/CGB) Emulator - App & Launcher
# High-performance RP2350 (300 MHz ARM Thumb-2) handheld gaming for GitHub Universe 2026 Badge

import array
import gc
import os
import sys

try:
    import machine
except ImportError:
    machine = None

APP_DIR = "/system/apps/gbc"
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
    GBC,
    REG_SCALE_MODE,
    REG_PAL_DIRTY,
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
    "zelda_ladx.gbc": ("Zelda: Link's Awakening DX", "GBC", True),
    "zelda_ages.gbc": ("Zelda: Oracle of Ages", "GBC", True),
    "zelda_seasons.gbc": ("Zelda: Oracle of Seasons", "GBC", True),
    "pokemon_crystal.gbc": ("Pokemon: Crystal Version", "GBC", True),
    "pokemon_gold.gbc": ("Pokemon: Gold Version", "GBC", True),
    "dw_1_2.gbc": ("Dragon Warrior I & II", "GBC", True),
    "tetris_dx.gbc": ("Tetris DX", "GBC", True),
    "smb_dx.gbc": ("Super Mario Bros. Deluxe", "GBC", True),
    "mario_land.gb": ("Super Mario Land", "DMG", False),
    "ff_legend.gb": ("The Final Fantasy Legend", "DMG", True),
    "zelda_la.gb": ("Zelda: Link's Awakening", "DMG", True),
    "drmario.gb": ("Dr. Mario (GB)", "DMG", False),
}

DEFAULT_ORDER = [
    "zelda_ladx.gbc", "pokemon_crystal.gbc", "dw_1_2.gbc", "tetris_dx.gbc",
    "zelda_ages.gbc", "zelda_seasons.gbc", "smb_dx.gbc", "mario_land.gb",
    "ff_legend.gb", "pokemon_gold.gbc", "zelda_la.gb", "drmario.gb",
]

GAME_SLUGS = {
    "zelda_ladx.gbc": "ZDX",
    "zelda_ages.gbc": "ZOA",
    "zelda_seasons.gbc": "ZOS",
    "pokemon_crystal.gbc": "PKC",
    "pokemon_gold.gbc": "PKG",
    "dw_1_2.gbc": "DW1",
    "tetris_dx.gbc": "TDX",
    "smb_dx.gbc": "SMX",
    "mario_land.gb": "SML",
    "ff_legend.gb": "FFL",
    "zelda_la.gb": "ZLA",
    "drmario.gb": "DRM",
}

PAUSE_OPTIONS = [
    "Resume Game",
    "Save State Now",
    "Scale Mode",
    "Frame Skip",
    "Toggle Scanlines",
    "Reset Console",
    "Exit to ROM List",
]

AUTO_SAVE_IDLE_MS = 60000


def _strip_ext(rom_file):
    low = rom_file.lower()
    if low.endswith(".gbc"):
        return rom_file[:-4]
    if low.endswith(".gb"):
        return rom_file[:-3]
    return rom_file


def _discover_games():
    found = []
    seen = set()
    roms_dir = f"{APP_DIR}/roms"
    try:
        files = sorted(os.listdir(roms_dir))
    except Exception:
        files = []
    for rom in DEFAULT_ORDER + files:
        low = rom.lower()
        if not (low.endswith(".gb") or low.endswith(".gbc")) or rom in seen:
            continue
        if files and rom not in files:
            continue
        seen.add(rom)
        if rom in KNOWN_GAMES:
            title, sys_tag, battery = KNOWN_GAMES[rom]
        else:
            base = _strip_ext(rom)
            title = base.replace("_", " ").replace("-", " ").title()
            sys_tag = "GBC" if low.endswith(".gbc") else "DMG"
            battery = True
        found.append((rom, title, sys_tag, battery))
    if not found:
        for rom in DEFAULT_ORDER:
            title, sys_tag, battery = KNOWN_GAMES[rom]
            found.append((rom, title, sys_tag, battery))
    return found


GAMES = _discover_games()


class GBCApp:
    MODE_LAUNCHER = 0
    MODE_GAME     = 1
    MODE_PAUSE    = 2

    def __init__(self):
        self.mode = self.MODE_LAUNCHER
        self.selected_index = 0
        self.scroll_top = 0
        self.scanlines_enabled = False
        self.frameskip_enabled = True
        self.scale_mode = 0  # 0 = 1:1 pixel-perfect (160x144), 1 = 1.5x (240x216)

        self.gbc = None
        self.active_game = None
        self.pause_index = 0
        self.menu_hold_start = None
        self.start_pulse = 0

        self.fps = 60
        self.fps_counter = 0
        self.fps_timer = 0
        self.last_autosave_ms = 0
        self.sram_msg_timer = 0
        self.sram_msg_text = "SAV"

        self.fpal = array.array("H", [0] * 64)
        self._saved_states = set()
        self._refresh_saved_states()

        self.img_wide = self._load_asset("spf13_wide.png")
        self.img_logo = self._load_asset("spf13_logo.png")

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
                if fn.startswith("gbc_") and fn.endswith(".state"):
                    self._saved_states.add(fn[4:-6])
        except Exception:
            pass

    def _load_asset(self, filename):
        for candidate in (f"{APP_DIR}/assets/{filename}", f"assets/{filename}", f"F:/apps/gbc/assets/{filename}"):
            try:
                if file_exists(candidate):
                    return image.load(candidate)
            except Exception:
                pass
        return None

    def _quit_to_menu(self):
        try:
            if self.gbc is not None:
                self.gbc.save_state(sync_sram=True)
            badge.autorotate(True)
        except Exception:
            pass
        reset()

    def _home_irq_handler(self, _pin):
        self._quit_to_menu()

    def _has_saved_state(self, rom_file):
        return _strip_ext(rom_file) in self._saved_states

    def launch_game(self, game_entry):
        rom_file, title, sys_tag, battery = game_entry
        rom_path = f"{APP_DIR}/roms/{rom_file}"
        if not file_exists(rom_path):
            rom_path = f"F:/apps/gbc/roms/{rom_file}"

        slug = _strip_ext(rom_file)
        sram_path = f"/state/gbc_{slug}.sav" if battery else None
        state_path = f"/state/gbc_{slug}.state"

        self.gbc = GBC(rom_path, sram_path=sram_path, state_path=state_path)
        self.gbc.st[REG_SCALE_MODE] = self.scale_mode
        self.active_game = game_entry
        if self.gbc.load_state():
            self.gbc.st[REG_SCALE_MODE] = self.scale_mode
            self.sram_msg_text = "RES"
            self.sram_msg_timer = 60
        else:
            self.sram_msg_text = "SAV"
            self.sram_msg_timer = 0

        self.gbc.update_palettes(self.fpal)
        self.mode = self.MODE_GAME
        now = badge.ticks
        self.fps_timer = now
        self.last_autosave_ms = now
        self.fps_counter = 0

    def update(self):
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

        if self.img_wide is not None:
            screen.blit(self.img_wide, vec2((320 - self.img_wide.width) // 2, 8))
        else:
            screen.pen = COLOR_GOLD
            screen.text("spf13 labs", 100, 12)

        screen.pen = COLOR_CREAM
        screen.text("GAME BOY COLOR", 98, 52)

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

        if (
            badge.pad.pressed(BUTTON_BACK)
            or badge.pad.pressed(BUTTON_SELECT)
            or badge.pad.pressed(BUTTON_HOME)
            or badge.buttons.pressed(BUTTON_SELECT)
            or badge.buttons.pressed(BUTTON_RIGHT)
        ):
            self.launch_game(GAMES[self.selected_index])
            return None

        if badge.buttons.pressed(BUTTON_LEFT):
            self._quit_to_menu()

        card_y = 70
        for i in range(self.scroll_top, min(len(GAMES), self.scroll_top + 4)):
            rom_file, title, sys_tag, battery = GAMES[i]
            is_selected = (i == self.selected_index)

            screen.pen = COLOR_CERULEAN if is_selected else COLOR_SURFACE
            screen.rectangle(20, card_y, 280, 32)

            if is_selected:
                screen.pen = COLOR_GOLD
                screen.rectangle(20, card_y, 4, 32)
                screen.pen = COLOR_WHITE
            else:
                screen.pen = COLOR_CREAM

            screen.text(title, 30, card_y + 8)

            screen.pen = COLOR_VIOLET if sys_tag == "GBC" else COLOR_ACCENT
            screen.text(sys_tag, 224, card_y + 8)

            if self._has_saved_state(rom_file):
                screen.pen = COLOR_ACCENT
                screen.text("[STA]", 258, card_y + 8)
            elif battery:
                screen.pen = COLOR_GOLD
                screen.text("[SAV]", 258, card_y + 8)

            card_y += 36

        screen.pen = COLOR_DIM
        screen.text("Gamepad: Move & Launch   HOME: Exit", 40, 218)
        return None

    def update_game(self):
        pad_dir = badge.pad.direction()
        d_up = (pad_dir.y < -0.25) or badge.pad.held(BUTTON_UP) or badge.buttons.held(BUTTON_UP)
        d_down = (pad_dir.y > 0.25) or badge.pad.held(BUTTON_DOWN) or badge.buttons.held(BUTTON_DOWN)
        d_left = (pad_dir.x < -0.25) or badge.pad.held(BUTTON_LEFT)
        d_right = (pad_dir.x > 0.25) or badge.pad.held(BUTTON_RIGHT)

        top_a = badge.pad.held(BUTTON_BACK)
        top_b = badge.pad.held(BUTTON_SELECT)
        mid_start = badge.pad.held(BUTTON_HOME)
        mid_select = badge.pad.held(BUTTON_MENU)

        both_top = top_a and top_b
        both_mid = mid_start and mid_select
        now = badge.ticks
        tap_both_start = False

        if both_top or both_mid:
            if self.menu_hold_start is None:
                self.menu_hold_start = now
            elif now - self.menu_hold_start >= 650:
                if self.gbc is not None:
                    self.gbc.save_state(sync_sram=True)
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

        if self.start_pulse > 0:
            self.start_pulse -= 1
            tap_both_start = True

        btn_start = mid_start or badge.buttons.held(BUTTON_SELECT) or tap_both_start
        btn_select = mid_select or badge.buttons.held(BUTTON_RIGHT) or badge.buttons.held(BUTTON_LEFT)
        btn_a = top_a and not both_top
        btn_b = top_b and not both_top

        # Build Game Boy Joypad bitfield: 0=Right, 1=Left, 2=Up, 3=Down, 4=A, 5=B, 6=Select, 7=Start
        pad = 0
        if d_right:    pad |= 0x01
        if d_left:     pad |= 0x02
        if d_up:       pad |= 0x04
        if d_down:     pad |= 0x08
        if btn_a:      pad |= 0x10
        if btn_b:      pad |= 0x20
        if btn_select: pad |= 0x40
        if btn_start:  pad |= 0x80

        if now - self.last_autosave_ms >= AUTO_SAVE_IDLE_MS and pad == 0:
            if self.gbc.save_state(sync_sram=False):
                self.sram_msg_text = "SAV"
                self.sram_msg_timer = 45
            self.last_autosave_ms = badge.ticks
            now = self.last_autosave_ms

        steps = 2 if self.frameskip_enabled else 1
        for _ in range(steps):
            self.gbc.step_frame(pad)

        if self.gbc.st[REG_PAL_DIRTY]:
            self.gbc.update_palettes(self.fpal)

        # Draw frame bezel & HUD sidebars BEFORE raw ptr16 writes
        screen.pen = COLOR_SIDEBAR
        if self.scale_mode:
            screen.rectangle(0, 0, 40, 240)
            screen.rectangle(280, 0, 40, 240)
            screen.rectangle(40, 0, 240, 12)
            screen.rectangle(40, 228, 240, 12)
        else:
            screen.rectangle(0, 0, 80, 240)
            screen.rectangle(240, 0, 80, 240)
            screen.rectangle(80, 0, 160, 48)
            screen.rectangle(80, 192, 160, 48)
            # Crisp Game Boy screen bezel around the 1:1 160x144 LCD (x=80..239, y=48..191)
            screen.pen = COLOR_SURFACE
            screen.rectangle(74, 42, 172, 6)
            screen.rectangle(74, 192, 172, 18)
            screen.rectangle(74, 48, 6, 144)
            screen.rectangle(240, 48, 6, 144)
            screen.pen = COLOR_GOLD
            screen.rectangle(78, 46, 164, 2)
            screen.rectangle(78, 192, 164, 2)
            screen.rectangle(78, 46, 2, 148)
            screen.rectangle(240, 46, 2, 148)
            screen.pen = COLOR_CREAM
            screen.text("GAME BOY", 118, 195)
            screen.pen = COLOR_GOLD
            screen.text("COLOR" if self.gbc.st[16] else "POCKET", 174, 195)
            if self.active_game is not None:
                screen.pen = COLOR_CREAM
                screen.text(self.active_game[1], 84, 22)

        if self.img_logo is not None:
            screen.blit(self.img_logo, vec2(4, 4))
        else:
            screen.pen = COLOR_GOLD
            screen.text("spf", 6, 8)

        self.fps_counter += 1
        if now - self.fps_timer >= 1000:
            self.fps = self.fps_counter
            self.fps_counter = 0
            self.fps_timer = now

        screen.pen = COLOR_ACCENT
        screen.text(f"{self.fps}", 8, 46)
        screen.pen = COLOR_DIM
        screen.text("fps", 8, 58)

        # Gamepad Visualizer in left sidebar
        cx, cy = 19, 118
        for dx, dy, active in ((0, -8, d_up), (-8, 0, d_left), (8, 0, d_right), (0, 8, d_down)):
            screen.pen = COLOR_GOLD if active else COLOR_SURFACE
            screen.circle(cx + dx, cy + dy, 3)
        screen.pen = COLOR_ACCENT if btn_b else COLOR_SURFACE
        screen.circle(12, 156, 4)
        screen.pen = COLOR_GOLD if btn_a else COLOR_SURFACE
        screen.circle(26, 148, 4)
        screen.pen = COLOR_VIOLET if btn_select else COLOR_SURFACE
        screen.rectangle(6, 172, 10, 4)
        screen.pen = COLOR_GOLD if btn_start else COLOR_SURFACE
        screen.rectangle(20, 172, 10, 4)

        slug = GAME_SLUGS.get(self.active_game[0], "GBC")
        screen.pen = COLOR_GOLD
        screen.text(slug, 288, 8)

        screen.pen = COLOR_VIOLET
        screen.text("GBC" if self.gbc.st[16] else "DMG", 288, 22)

        screen.pen = COLOR_GOLD
        screen.text("PAD", 288, 44)
        screen.pen = COLOR_CREAM
        screen.text("A:UR", 286, 58)
        screen.text("B:LL", 286, 70)
        screen.text("St:M1", 284, 86)
        screen.text("Sl:M2", 284, 98)

        screen.pen = COLOR_GOLD
        screen.text("MENU", 285, 124)
        screen.pen = COLOR_CREAM
        screen.text("Hold", 286, 138)
        screen.text("A+B", 288, 150)

        if self.sram_msg_timer > 0:
            self.sram_msg_timer -= 1
            screen.pen = COLOR_GOLD
            screen.text(self.sram_msg_text, 288, 218)

        fb = screen.raw
        if hasattr(fb, "cast"):
            fb = memoryview(fb).cast("H")

        self.gbc.render(fb, self.fpal, scanlines=self.scanlines_enabled)
        return None

    def update_pause(self):
        if badge.pressed(BUTTON_UP) or badge.pad.pressed(BUTTON_UP):
            self.pause_index = (self.pause_index - 1) % len(PAUSE_OPTIONS)
        elif badge.pressed(BUTTON_DOWN) or badge.pad.pressed(BUTTON_DOWN) or badge.pad.pressed(BUTTON_MENU):
            self.pause_index = (self.pause_index + 1) % len(PAUSE_OPTIONS)

        if badge.buttons.pressed(BUTTON_LEFT):
            self.mode = self.MODE_GAME
            return None

        if (
            badge.pad.pressed(BUTTON_BACK)
            or badge.pad.pressed(BUTTON_SELECT)
            or badge.pad.pressed(BUTTON_HOME)
            or badge.buttons.pressed(BUTTON_SELECT)
        ):
            choice = self.pause_index
            if choice == 0:
                self.mode = self.MODE_GAME
            elif choice == 1:
                if self.gbc is not None:
                    self.gbc.save_state(sync_sram=True)
                    self.sram_msg_text = "SAV"
                    self.sram_msg_timer = 90
                    self.last_autosave_ms = badge.ticks
                self.mode = self.MODE_GAME
            elif choice == 2:
                self.scale_mode = 0 if self.scale_mode else 1
                if self.gbc is not None:
                    self.gbc.st[REG_SCALE_MODE] = self.scale_mode
            elif choice == 3:
                self.frameskip_enabled = not self.frameskip_enabled
            elif choice == 4:
                self.scanlines_enabled = not self.scanlines_enabled
            elif choice == 5:
                if self.gbc is not None:
                    self.gbc.reset_console()
                    self.gbc.update_palettes(self.fpal)
                    self.gbc.save_state(sync_sram=False)
                self.last_autosave_ms = badge.ticks
                self.mode = self.MODE_GAME
            elif choice == 6:
                if self.gbc is not None:
                    self.gbc.save_state(sync_sram=True)
                self._refresh_saved_states()
                self.gbc = None
                self.active_game = None
                self.mode = self.MODE_LAUNCHER
            return None

        screen.pen = COLOR_DARK_BG
        screen.rectangle(44, 22, 232, 196)
        screen.pen = COLOR_GOLD
        screen.rectangle(44, 22, 232, 2)
        screen.rectangle(44, 216, 232, 2)
        screen.rectangle(44, 22, 2, 196)
        screen.rectangle(274, 22, 2, 196)

        screen.pen = COLOR_CREAM
        screen.text("spf13 labs - GBC PAUSED", 70, 30)

        opt_y = 52
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
                label = "Scale: 1.5x (240x216)" if self.scale_mode else "Scale: 1:1 (160x144)"
            elif idx == 3:
                label = "Frame Skip: 2x ON" if self.frameskip_enabled else "Frame Skip: OFF"
            elif idx == 4:
                label = "LCD Grid: ON" if self.scanlines_enabled else "LCD Grid: OFF"

            screen.text(label, 60, opt_y + 2)
            opt_y += 22

        return None


app = GBCApp()


def on_exit():
    try:
        if app.gbc is not None:
            app.gbc.save_state(sync_sram=True)
        badge.autorotate(True)
    except Exception:
        pass


run(app.update)
