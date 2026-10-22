# spf13 labs - Hardware Lab & Physics Playground
# Built for the GitHub Universe 2026 ARM Hackable Badge (RP2350 + MonaOS)
# Features:
#   1. 60 FPS @micropython.viper IMU Neon Sand Physics Toy (sand_and_3d.py)
#   2. Dual-Core pico3d Custom 3D Mesh Showcase (spf13_logo.p3d + Mona LODs)
#   3. Live 6-Axis LSM6DS3 IMU, CPU, Heap & Case LED Telemetry Inspector

import gc
import math
import os
import sys

try:
    import machine
except ImportError:
    machine = None

APP_DIR = "/system/apps/hacker"
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
if "/system/apps/menu" not in sys.path:
    sys.path.insert(0, "/system/apps/menu")
if "/" not in sys.path:
    sys.path.insert(0, "/")

try:
    badge.mode(HIRES | VSYNC)
    display.set_async(False)
except Exception:
    pass

try:
    import pico3d
except ImportError:
    pico3d = None

try:
    import mona3d
except ImportError:
    mona3d = None

try:
    from sand_and_3d import SCENE_NAMES, SandToy, step_and_render_sand_16
except ImportError:
    SandToy = None
    step_and_render_sand_16 = None
    SCENE_NAMES = ("SPF13 Collider", "Hourglass", "Plinko Pegs", "Open Basin")

# spf13 labs Palette
COLOR_CERULEAN = color.rgb(0, 106, 149)
COLOR_MAGENTA  = color.rgb(150, 25, 138)
COLOR_GOLD     = color.rgb(249, 193, 110)
COLOR_VIOLET   = color.rgb(186, 110, 249)
COLOR_CREAM    = color.rgb(248, 250, 244)
COLOR_DARK_BG  = color.rgb(14, 16, 26)
COLOR_SURFACE  = color.rgb(24, 28, 44)
COLOR_CARD     = color.rgb(32, 38, 58)
COLOR_BORDER   = color.rgb(55, 65, 95)
COLOR_GREEN    = color.rgb(46, 210, 115)
COLOR_RED      = color.rgb(235, 80, 80)
COLOR_WHITE    = color.rgb(255, 255, 255)
COLOR_DIM      = color.rgb(140, 145, 160)


class HardwareLabApp:
    TAB_SAND = 0
    TAB_3D   = 1
    TAB_TELE = 2
    TAB_COUNT = 3
    TAB_NAMES = ["IMU Sand", "3D Engine", "Telemetry"]

    def __init__(self):
        self.tab = 0
        self.action_index = 0
        self.in_actions = True

        self.notification = "Welcome to spf13 labs Hardware Playground!"
        self.notification_time = badge.ticks

        self.logo = None
        for candidate in (f"{APP_DIR}/assets/spf13_logo.png", "assets/spf13_logo.png"):
            try:
                if file_exists(candidate):
                    self.logo = image.load(candidate)
                    break
            except Exception:
                pass

        self.benchmark_active = False
        self.benchmark_mona = None
        self.benchmark_models = []
        self.benchmark_model_idx = 0
        self.benchmark_frames = 0
        self.benchmark_start = 0
        self.benchmark_fps = 0.0

        self.sand_active = False
        self.sand_toy = None
        self.sand_fps = 60
        self.sand_frames = 0
        self.sand_timer = 0
        self.led_pulse_active = False

    def notify(self, msg):
        self.notification = msg
        self.notification_time = badge.ticks

    def get_action_count(self):
        if self.tab == self.TAB_SAND:
            return 4
        elif self.tab == self.TAB_3D:
            return 2
        elif self.tab == self.TAB_TELE:
            return 3
        return 0

    def execute_action(self):
        if self.tab == self.TAB_SAND:
            if self.action_index == 0:
                self.start_sand_toy(scene_idx=0)
            elif self.action_index == 1:
                self.start_sand_toy(scene_idx=1)
            elif self.action_index == 2:
                self.start_sand_toy(scene_idx=2)
            elif self.action_index == 3:
                self.start_sand_toy(scene_idx=3)
        elif self.tab == self.TAB_3D:
            if self.action_index == 0:
                self.start_3d_benchmark(start_idx=0)
            elif self.action_index == 1:
                self.start_3d_benchmark(start_idx=1)
        elif self.tab == self.TAB_TELE:
            if self.action_index == 0:
                gc.collect()
                free_kb = (gc.mem_free() // 1024) if hasattr(gc, "mem_free") else 128
                self.notify(f"GC complete! Free RAM: {free_kb} KB")
            elif self.action_index == 1:
                self.led_pulse_active = not self.led_pulse_active
                if not self.led_pulse_active:
                    try:
                        badge.caselights(0)
                    except Exception:
                        pass
                self.notify(f"Ambient IMU Case LEDs: {'ON' if self.led_pulse_active else 'OFF'}")
            elif self.action_index == 2:
                self.start_sand_toy(scene_idx=0)

    # -------------------------------------------------------------
    # 3D spf13 + Mona Tilt Showcase
    # -------------------------------------------------------------
    def start_3d_benchmark(self, start_idx=0):
        if mona3d is None or pico3d is None:
            self.notify("pico3d / mona3d engine not available")
            return
        try:
            self.sand_toy = None
            gc.collect()
            pico3d.engine.cores(2)
            pico3d.engine.work_bands(6)
            custom_p3d = f"{APP_DIR}/spf13_logo.p3d"
            self.benchmark_models = [(custom_p3d, "spf13 3D")] + list(mona3d.MODELS)
            self.benchmark_model_idx = start_idx % len(self.benchmark_models)
            model_path = self.benchmark_models[self.benchmark_model_idx][0]
            self.benchmark_mona = mona3d.Mona(model=model_path)
            self.benchmark_active = True
            self.benchmark_frames = 0
            self.benchmark_start = badge.ticks
            self.benchmark_fps = 0.0
        except Exception as e:
            self.benchmark_active = False
            self.notify(f"3D error: {e}")

    def update_3d_benchmark(self):
        if badge.pressed(BUTTON_BACK) or badge.pressed(BUTTON_SELECT):
            self.benchmark_active = False
            self.benchmark_mona = None
            gc.collect()
            self.notify("Exited 3D Showcase.")
            return

        if badge.pressed(BUTTON_LEFT) or badge.pressed(BUTTON_RIGHT):
            step = 1 if badge.pressed(BUTTON_RIGHT) else -1
            for _ in range(len(self.benchmark_models)):
                self.benchmark_model_idx = (self.benchmark_model_idx + step) % len(self.benchmark_models)
                try:
                    self.benchmark_mona.load(self.benchmark_models[self.benchmark_model_idx][0])
                    break
                except Exception:
                    pass

        if badge.pressed(BUTTON_DOWN):
            self.benchmark_mona.scale = mona3d.SCALE * 0.5 if self.benchmark_mona.scale == mona3d.SCALE else mona3d.SCALE

        self.benchmark_mona.prepare()
        drawn = self.benchmark_mona.render()

        self.benchmark_frames += 1
        now = badge.ticks
        if now - self.benchmark_start >= 500:
            self.benchmark_fps = self.benchmark_frames * 1000 / (now - self.benchmark_start)
            self.benchmark_frames = 0
            self.benchmark_start = now

        screen.pen = COLOR_DARK_BG
        screen.rectangle(0, 0, 320, 22)
        screen.pen = COLOR_GOLD
        model_name = self.benchmark_models[self.benchmark_model_idx][1] if self.benchmark_models else "3D"
        screen.text(f"spf13 labs 3D: {model_name} | {self.benchmark_fps:.1f} FPS | {drawn} tris", 6, 5)

        screen.pen = COLOR_DARK_BG
        screen.rectangle(0, 220, 320, 20)
        screen.pen = COLOR_CREAM
        screen.text("[< >] Model   [v] Scale   [BACK/SEL] Exit", 26, 224)

    # -------------------------------------------------------------
    # 60 FPS Viper IMU Neon Sand Physics Toy
    # -------------------------------------------------------------
    def start_sand_toy(self, scene_idx=0):
        if SandToy is None or step_and_render_sand_16 is None:
            self.notify("SandToy engine not available")
            return
        try:
            self.benchmark_mona = None
            gc.collect()
            if self.sand_toy is None:
                self.sand_toy = SandToy(screen, color)
            self.sand_toy.load_scene(scene_idx)
            self.sand_active = True
            self.sand_frames = 0
            self.sand_timer = badge.ticks
        except Exception as e:
            self.sand_active = False
            self.notify(f"Sand error: {e}")

    def update_sand_toy(self):
        if badge.buttons.pressed(BUTTON_LEFT) or badge.pressed(BUTTON_BACK):
            self.sand_active = False
            try:
                badge.caselights(0)
            except Exception:
                pass
            self.notify("Exited IMU Sand Physics Toy.")
            return

        if badge.pad.pressed(BUTTON_LEFT) or badge.pad.pressed(BUTTON_RIGHT) or badge.buttons.pressed(BUTTON_RIGHT):
            step = -1 if badge.pad.pressed(BUTTON_LEFT) else 1
            self.sand_toy.load_scene(self.sand_toy.scene_idx + step)

        if (
            badge.pad.pressed(BUTTON_BACK)
            or badge.pad.pressed(BUTTON_SELECT)
            or badge.pressed(BUTTON_DOWN)
            or badge.buttons.pressed(BUTTON_SELECT)
        ):
            self.sand_toy.spawn_burst(450)

        if badge.pressed(BUTTON_UP) or badge.pad.pressed(BUTTON_UP):
            self.sand_toy.load_scene(self.sand_toy.scene_idx)

        gx, gy = 0, 1
        try:
            ax, ay, _az = badge.imu()[:3]
            if abs(ax) + abs(ay) > 26000:
                self.sand_toy.stir_vortex()
            gx = 1 if ax > 3200 else (-1 if ax < -3200 else 0)
            gy = 1 if ay > 3200 else (-1 if ay < -3200 else 0)
            if gx == 0 and gy == 0:
                gy = 1
            badge.caselights(min(0.35, (abs(ax) + abs(ay)) / 65536.0))
        except Exception:
            gx, gy = 0, 1

        now = badge.ticks
        self.sand_frames += 1
        if now - self.sand_timer >= 1000:
            self.sand_fps = self.sand_frames
            self.sand_frames = 0
            self.sand_timer = now

        # Draw top & bottom HUD bars FIRST so screen.* opens the display fence before raw ptr16 writes
        screen.pen = COLOR_DARK_BG
        screen.rectangle(0, 0, 320, 20)
        screen.pen = COLOR_GOLD
        scene_name = SCENE_NAMES[self.sand_toy.scene_idx]
        screen.text(f"spf13 Sand: {scene_name} | {self.sand_fps} FPS", 6, 4)

        screen.pen = COLOR_DARK_BG
        screen.rectangle(0, 220, 320, 20)
        screen.pen = COLOR_CREAM
        screen.text("Tilt: Pour  [A/SEL] Spawn  [< >] Scene  [BACK] Exit", 8, 224)

        self.sand_toy.parity ^= 1
        flags = (gx + 1) | ((gy + 1) << 2) | (self.sand_toy.parity << 4)
        step_and_render_sand_16(self.sand_toy.grid, screen.raw, self.sand_toy.pal, flags)

    # -------------------------------------------------------------
    # Main UI Loop
    # -------------------------------------------------------------
    def update(self):
        if self.benchmark_active:
            self.update_3d_benchmark()
            return
        if self.sand_active:
            self.update_sand_toy()
            return

        now = badge.ticks

        if badge.pressed(BUTTON_BACK):
            if self.in_actions:
                self.in_actions = False
            else:
                try:
                    badge.caselights(0)
                    reset()
                except Exception:
                    pass
                return

        if not self.in_actions:
            if badge.pressed(BUTTON_LEFT):
                self.tab = (self.tab - 1) % self.TAB_COUNT
                self.action_index = 0
            elif badge.pressed(BUTTON_RIGHT):
                self.tab = (self.tab + 1) % self.TAB_COUNT
                self.action_index = 0
            elif badge.pressed(BUTTON_DOWN) or badge.pressed(BUTTON_SELECT):
                self.in_actions = True
                self.action_index = 0
        else:
            action_count = self.get_action_count()
            if badge.pressed(BUTTON_UP):
                if self.action_index > 0:
                    self.action_index -= 1
                else:
                    self.in_actions = False
            elif badge.pressed(BUTTON_DOWN):
                if self.action_index < action_count - 1:
                    self.action_index += 1
            elif badge.pressed(BUTTON_SELECT):
                self.execute_action()

        self.render(now)

    def render(self, now):
        screen.pen = COLOR_DARK_BG
        screen.clear()
        self.render_header()
        self.render_tabs()

        if self.tab == self.TAB_SAND:
            self.render_tab_sand()
        elif self.tab == self.TAB_3D:
            self.render_tab_3d()
        elif self.tab == self.TAB_TELE:
            self.render_tab_telemetry()

        self.render_footer(now)

    def render_header(self):
        screen.pen = COLOR_SURFACE
        screen.rectangle(0, 0, 320, 28)
        screen.pen = COLOR_GOLD
        screen.rectangle(0, 28, 320, 1)

        if self.logo is not None:
            try:
                screen.blit(self.logo, rect(6, 4, 20, 20))
            except Exception:
                screen.pen = COLOR_GOLD
                screen.text(">_", 8, 8)
        else:
            screen.pen = COLOR_GOLD
            screen.text(">_", 8, 8)

        screen.pen = COLOR_GOLD
        screen.text("spf13 labs", 32, 8)
        screen.pen = COLOR_VIOLET
        screen.text("HARDWARE LAB", 112, 8)

        try:
            bat = badge.battery_level()
            chg = badge.is_charging()
            chg_str = "+" if chg else ""
            screen.pen = COLOR_GREEN if (bat > 30 or chg) else COLOR_RED
            screen.text(f"{bat}%{chg_str}", 274, 8)
        except Exception:
            pass

    def render_tabs(self):
        tab_w = 100
        tab_h = 18
        start_x = 6
        y = 32

        for idx in range(self.TAB_COUNT):
            x = start_x + idx * (tab_w + 4)
            is_active = (idx == self.tab)

            if is_active:
                screen.pen = COLOR_CERULEAN if self.in_actions else COLOR_MAGENTA
                screen.rectangle(x, y, tab_w, tab_h)
                screen.pen = COLOR_GOLD
                screen.rectangle(x, y + tab_h - 2, tab_w, 2)
                screen.pen = COLOR_WHITE
            else:
                screen.pen = COLOR_CARD
                screen.rectangle(x, y, tab_w, tab_h)
                screen.pen = COLOR_DIM

            screen.text(self.TAB_NAMES[idx], x + 12, y + 4)

    def render_tab_sand(self):
        screen.pen = COLOR_CARD
        screen.rectangle(8, 54, 304, 68)
        screen.pen = COLOR_BORDER
        screen.rectangle(8, 54, 304, 1)

        screen.pen = COLOR_GOLD
        screen.text("60 FPS @micropython.viper IMU Sand Physics", 14, 58)
        screen.pen = COLOR_CREAM
        screen.text("160x100 cellular automata (4,000 neon particles)", 14, 74)
        screen.text("driven by real-time 6-axis LSM6DS3 gravity.", 14, 88)
        screen.pen = COLOR_DIM
        screen.text("Tilt to pour, shake for vortex, [A] to spawn!", 14, 104)

        actions = [
            "Launch Scene 1: SPF13 Collider",
            "Launch Scene 2: Neon Hourglass",
            "Launch Scene 3: Plinko Pegs",
            "Launch Scene 4: Open Fluid Basin",
        ]
        self.render_action_list(actions, start_y=130, item_h=18)

    def render_tab_3d(self):
        screen.pen = COLOR_CARD
        screen.rectangle(8, 54, 304, 76)
        screen.pen = COLOR_BORDER
        screen.rectangle(8, 54, 304, 1)

        screen.pen = COLOR_GOLD
        screen.text("Dual-Core RP2350 pico3d Mesh Showcase", 14, 58)
        screen.pen = COLOR_CREAM
        screen.text("Software rasterizer running across both Cortex-M33", 14, 74)
        screen.text("cores (6 work bands) with IMU tilt tracking.", 14, 88)
        screen.pen = COLOR_DIM
        screen.text("Includes custom P3DM v1 'spf13' mesh + Mona LODs.", 14, 106)

        actions = [
            "Launch Custom 3D 'SPF13' Crystal Mesh",
            "Launch Mona 3D Multi-LOD Benchmark (4k-20k tris)",
        ]
        self.render_action_list(actions, start_y=140, item_h=20)

    def render_tab_telemetry(self):
        screen.pen = COLOR_CARD
        screen.rectangle(8, 54, 304, 76)
        screen.pen = COLOR_BORDER
        screen.rectangle(8, 54, 304, 1)

        cpu = machine.freq() // 1000000 if machine else 230
        free_kb = (gc.mem_free() // 1024) if hasattr(gc, "mem_free") else 128
        alloc_kb = (gc.mem_alloc() // 1024) if hasattr(gc, "mem_alloc") else 64

        screen.pen = COLOR_CREAM
        screen.text(f"RP2350 CPU: {cpu} MHz   RAM: {free_kb}K free / {alloc_kb}K alloc", 14, 58)

        try:
            imu_data = badge.imu()
            ax, ay, az = imu_data[:3]
            gx, gy, gz = imu_data[3:6]
            if self.led_pulse_active:
                badge.caselights(min(0.35, (abs(ax) + abs(ay)) / 65536.0))
        except Exception:
            ax, ay, az = 0.0, 0.0, 1.0
            gx, gy, gz = 0.0, 0.0, 0.0

        screen.pen = COLOR_GOLD
        screen.text(f"Acc: X={ax:+.2f} Y={ay:+.2f} Z={az:+.2f}", 14, 76)
        screen.pen = COLOR_VIOLET
        screen.text(f"Gyr: X={gx:+.1f} Y={gy:+.1f} Z={gz:+.1f}", 14, 92)
        screen.pen = COLOR_GREEN
        screen.text("Display: 320x240 RGB565 (ST7789) + CAP1208 Touch", 14, 110)

        actions = [
            "Run Garbage Collection (gc.collect)",
            f"Toggle IMU Ambient Case LEDs ({'ON' if self.led_pulse_active else 'OFF'})",
            "Quick-Launch 60 FPS IMU Sand Toy",
        ]
        self.render_action_list(actions, start_y=138, item_h=18)

    def render_action_list(self, actions, start_y=140, item_h=16):
        for idx, act in enumerate(actions):
            y = start_y + idx * (item_h + 2)
            is_selected = (self.in_actions and idx == self.action_index)

            if is_selected:
                screen.pen = COLOR_CERULEAN
                screen.rectangle(8, y - 1, 304, item_h + 1)
                screen.pen = COLOR_GOLD
                screen.rectangle(8, y - 1, 3, item_h + 1)
                screen.pen = COLOR_WHITE
                prefix = "> "
            else:
                screen.pen = COLOR_CARD
                screen.rectangle(8, y - 1, 304, item_h + 1)
                screen.pen = COLOR_CREAM
                prefix = "  "

            screen.text(prefix + act, 12, y + 2)

    def render_footer(self, now):
        screen.pen = COLOR_SURFACE
        screen.rectangle(0, 218, 320, 22)
        screen.pen = COLOR_GOLD
        screen.rectangle(0, 218, 320, 1)

        if now - self.notification_time < 4000:
            screen.pen = COLOR_GOLD
            screen.text(self.notification[:46], 10, 224)
        else:
            screen.pen = COLOR_DIM
            if self.in_actions:
                screen.text("[^ v] Move   [SEL] Launch   [BACK] Tabs", 40, 224)
            else:
                screen.text("[< >] Tabs   [v / SEL] Actions   [BACK] Exit", 36, 224)


app = HardwareLabApp()
run(app.update)
