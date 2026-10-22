import os
import gc
import json
import math
import random

import fetch
import machine
import missing_details
import wifi
from badgeware import link as badgelink

try:
    import secrets
except ImportError:
    secrets = None

badge.mode(HIRES | VSYNC)
screen.antialias = image.X2

phosphor = color.rgb(211, 250, 55, 150)
white = color.rgb(235, 245, 255)
faded = color.rgb(235, 245, 255, 100)
spinner = color.rgb(211, 250, 55, 50)
black = color.rgb(0, 0, 0)

# spf13 labs palette
COLOR_CERULEAN = color.rgb(0, 75, 110)
COLOR_TEAL     = color.rgb(0, 120, 165)
COLOR_CYAN     = color.rgb(7, 157, 194)
COLOR_MAGENTA  = color.rgb(150, 25, 138)
COLOR_VIOLET   = color.rgb(186, 110, 249)
COLOR_GOLD     = color.rgb(249, 193, 110)
COLOR_CREAM    = color.rgb(248, 250, 244)
COLOR_WHITE    = color.rgb(245, 248, 255)
COLOR_DIM      = color.rgb(140, 150, 170)

SPF13_LEVELS = [
    color.rgb(12, 16, 28),
    COLOR_CERULEAN,
    COLOR_TEAL,
    COLOR_MAGENTA,
    COLOR_VIOLET,
]

BRAND_BG_LEVELS = [
    color.rgb(10, 12, 20),
    color.rgb(0, 35, 55),
    color.rgb(0, 55, 80),
    color.rgb(65, 15, 60),
    color.rgb(80, 45, 110),
]

mona_sans = missing_details.mona_sans()

HANDLE_SIZE = 28
NAME_SIZE = 16
VALUE_SIZE = 28
LABEL_SIZE = 14
NUDGE_SIZE = 17
NUDGE_RECT = (176, 70, 136, 150)

CONTRIB_URL = "https://github.com/{user}.contribs"
USER_AVATAR = "https://wsrv.nl/?url=https://github.com/{user}.png&w=150&output=png"
DETAILS_URL = "https://api.github.com/users/{user}"

CACHE_DIR = "/state"
USER_FILE = f"{CACHE_DIR}/badge_user.json"
CONTRIB_FILE = f"{CACHE_DIR}/badge_contribs.json"
AVATAR_FILE = f"{CACHE_DIR}/badge_avatar.png"

ASSET_SEARCH_PATHS = [
    "/state",
    "/system/apps/badge/assets",
    "/apps/badge/assets",
    "apps/badge/assets",
    "F:/apps/badge/assets",
]

def find_asset(filename):
    for base in ASSET_SEARCH_PATHS:
        path = f"{base}/{filename}"
        try:
            os.stat(path)
            return path
        except OSError:
            continue
    return None

def file_exists_safe(p):
    if not p:
        return False
    try:
        os.stat(p)
        return True
    except OSError:
        return False

HEADERS = {"User-Agent": "GitHub Universe Badge 2026"}

RETRY_MS = 30000

fetch.AsyncFetch.MAX_HEADER_BYTES = 8192

GRAPH_WEEKS = 53
GRAPH_DAYS = 7
TILE_SIZE = 33
TILE_STEP = 37
GRAPH_TOP = (240 - GRAPH_DAYS * TILE_STEP) // 2
PARALLAX_SHIFT = 8
AVATAR_SIZE = 150

try:
    os.mkdir(CACHE_DIR)
except OSError:
    pass

class Tilt:
    GRAVITY = 16384.0
    SMOOTH_MS = 120
    RECENTRE_MS = 2800

    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.rest_z = None
        self.last_ticks = None

    def update(self):
        ax, _, az = badge.imu()[:3]
        now = badge.ticks
        dt = min(now - self.last_ticks, 200) if self.last_ticks is not None else 0
        self.last_ticks = now
        smooth = 1 - math.exp(-dt / self.SMOOTH_MS)
        recentre = 1 - math.exp(-dt / self.RECENTRE_MS)
        if self.rest_z is None:
            self.rest_z = az
        self.rest_z += (az - self.rest_z) * recentre
        target_x = max(-1.0, min(1.0, -ax / self.GRAVITY * 2))
        target_y = max(-1.0, min(1.0, (az - self.rest_z) / self.GRAVITY * 2))
        self.x += (target_x - self.x) * smooth
        self.y += (target_y - self.y) * smooth

tilt = Tilt()

def fake_number():
    return random.randint(10000, 99999)

def placeholder_if_none(text):
    if text:
        return text
    random.seed(badge.ticks // 100)
    chars = "!\"£$%^&*()_+-={}[]:@~;'#<>?,./\\|"
    return "".join(random.choice(chars) for _ in range(20))

class Download:
    def __init__(self, url, file, force_update):
        self.file = file
        self.temp_file = file + ".tmp"
        self.request = None
        if force_update or not file_exists_safe(file):
            self.request = fetch.url(url, headers=HEADERS).to(self.temp_file)

    def ready(self):
        if self.request is None:
            return True
        if self.request:
            try:
                os.rename(self.temp_file, self.file)
            except Exception:
                pass
            self.request = None
            return True
        return False

    @property
    def state(self):
        if self.request is None:
            return None
        return self.request.state

    @property
    def error(self):
        if self.request is None:
            return None
        return self.request.error

class User:
    levels = [
        color.rgb(10, 13, 17),
        color.rgb(1, 29, 11),
        color.rgb(12, 54, 23),
        color.rgb(23, 80, 33),
        color.rgb(43, 105, 50),
    ]

    def __init__(self, handle):
        self.handle = handle
        self.tile = shape.rounded_rectangle(0, 0, TILE_SIZE, TILE_SIZE, 4)
        self.avatar = None
        self.profile_avatar = None
        self.wide_banner = None
        self.brain_logo = None
        self.qr_code = None
        self.update()
        self.load_user_data()
        self.load_contrib_data()
        self.load_avatar()

    def update(self, force_update=False):
        self.name = None
        self.followers = None
        self.contribs = None
        self.contribution_data = None
        self.repos = None
        self._download = None
        self._force_update = force_update
        self._error = None
        self._retry_at = 0
        self.missing = []

    def load_user_data(self):
        target_file = USER_FILE if file_exists_safe(USER_FILE) else find_asset("badge_user.json")
        if target_file and file_exists_safe(target_file):
            try:
                with open(target_file, "r") as f:
                    data = json.load(f)
                login = data.get("login", "")
                if self.handle and login and login.lower() != self.handle.lower() and not self._force_update:
                    pass
                else:
                    self.name = data.get("name") or login or "Steve Francia"
                    self.handle = login or self.handle
                    self.followers = data.get("followers", 16702)
                    self.repos = data.get("public_repos", 124)
                    del data
                    gc.collect()
                    return
            except Exception as e:
                print(f"User JSON error: {e}")
        if self.handle and self.handle.lower() == "spf13":
            self.name = "Steve Francia"
            self.followers = 16702
            self.repos = 124

    def load_contrib_data(self):
        target_file = CONTRIB_FILE if file_exists_safe(CONTRIB_FILE) else find_asset("badge_contribs.json")
        if target_file and file_exists_safe(target_file):
            try:
                with open(target_file, "r") as f:
                    data = json.load(f)
                self.contribs = data.get("total_contributions", 5042)
                self.contribution_data = [[0 for _ in range(GRAPH_WEEKS)] for _ in range(GRAPH_DAYS)]
                for week_index, week in enumerate(data.get("weeks", [])[:GRAPH_WEEKS]):
                    for day, contribution in enumerate(week.get("contribution_days", [])[:GRAPH_DAYS]):
                        self.contribution_data[day][week_index] = contribution.get("level", 0)
                del data
                gc.collect()
                return
            except Exception as e:
                print(f"Contrib JSON error: {e}")
        if self.handle and self.handle.lower() == "spf13":
            self.contribs = 5042
            self.contribution_data = [[(x + y) % 5 for x in range(GRAPH_WEEKS)] for y in range(GRAPH_DAYS)]

    def load_avatar(self):
        logo_path = AVATAR_FILE if file_exists_safe(AVATAR_FILE) else find_asset("spf13_logo_150.png")
        if logo_path and file_exists_safe(logo_path):
            try:
                self.avatar = image.load(logo_path)
            except Exception as e:
                print(f"Avatar load error: {e}")

        prof_path = find_asset("spf13_profile_150.png")
        if prof_path and file_exists_safe(prof_path):
            try:
                self.profile_avatar = image.load(prof_path)
            except Exception:
                pass

        wide_path = find_asset("spf13_wide_300.png")
        if wide_path and file_exists_safe(wide_path):
            try:
                self.wide_banner = image.load(wide_path)
            except Exception:
                pass

        brain_path = find_asset("spf13_brain_180.png")
        if brain_path and file_exists_safe(brain_path):
            try:
                self.brain_logo = image.load(brain_path)
            except Exception:
                pass

    def next_download(self):
        if self.name is None:
            self.load_user_data()
            if self.name is None:
                return "fetching user data...", DETAILS_URL, USER_FILE, self.load_user_data
        if self.contribs is None:
            self.load_contrib_data()
            if self.contribs is None:
                return "fetching contribs...", CONTRIB_URL, CONTRIB_FILE, self.load_contrib_data
        if self.avatar is None:
            self.load_avatar()
            if self.avatar is None:
                return "fetching avatar...", USER_AVATAR, AVATAR_FILE, self.load_avatar
        return None

    def fail(self, error):
        print(f"Badge: {error}")
        self._error = error
        self._retry_at = badge.ticks + RETRY_MS
        self._download = None

    def fetch_step(self):
        if self._error:
            if badge.ticks < self._retry_at:
                return "fetch failed, retrying..."
            self._error = None

        pending = self.next_download()
        if pending is None:
            return None

        label, url, file, load = pending

        if self._download is None or self._download.file != file:
            if self._force_update or not file_exists_safe(file):
                self.missing = missing_details.missing("WIFI_SSID")
                if self.missing:
                    return label
            self._download = Download(url.format(user=self.handle), file, self._force_update)

        if self._download.ready():
            self._download = None
            try:
                load()
            except (OSError, ValueError, KeyError) as e:
                try:
                    os.remove(file)
                except OSError:
                    pass
                self.fail(e)
            return label

        if self._download.error:
            self.fail(self._download.error)
            return label

        return self._download.state if self._download.state not in ("Idle", "Fetching") else label

    @property
    def loaded(self):
        return self.name is not None

    @property
    def failed(self):
        return self._error is not None

    def draw_stat(self, title, value, x, y, title_pen=phosphor, val_pen=white):
        screen.font = mona_sans
        screen.pen = val_pen if value else faded
        if value is not None:
            text = str(value)
        elif self.failed:
            text = "-"
        else:
            text = str(fake_number())
        screen.text(text, x, y, VALUE_SIZE)
        screen.pen = title_pen
        screen.text(title, x, y + VALUE_SIZE + 2, LABEL_SIZE)

    def draw_graph(self, palette_levels=None):
        levels = palette_levels or User.levels
        graph_width = GRAPH_WEEKS * TILE_STEP
        travel = (graph_width - screen.width) / 2
        offset_x = int(-math.sin(badge.ticks / 5000) * travel + travel + tilt.x * PARALLAX_SHIFT)
        offset_y = GRAPH_TOP - int(tilt.y * PARALLAX_SHIFT)

        for y in range(GRAPH_DAYS):
            for x in range(GRAPH_WEEKS):
                pos_x = x * TILE_STEP - offset_x
                if pos_x + TILE_SIZE < 0 or pos_x > screen.width:
                    continue
                level = self.contribution_data[y][x] if self.contribution_data else 1
                screen.pen = levels[level]
                self.tile.transform = mat3().translate(pos_x, y * TILE_STEP + offset_y)
                screen.shape(self.tile)

    def draw_avatar(self, mode=0):
        current_img = self.profile_avatar if (mode == 1 and self.profile_avatar) else (self.avatar or self.profile_avatar)
        if current_img:
            screen.blit(current_img, rect(10, 74, AVATAR_SIZE, AVATAR_SIZE))
            return

        squircle = shape.squircle(0, 0, 20, 5)
        screen.pen = spinner
        for i in range(4):
            mul = math.sin(badge.ticks / 1000) * 14000
            squircle.transform = mat3().translate(85, 149).rotate((badge.ticks + i * mul) / 40).scale(1 + i / 1.3)
            screen.shape(squircle)

    def draw_spf13_badge(self, status, mode=0):
        self.draw_graph(SPF13_LEVELS)

        screen.font = mona_sans
        screen.pen = COLOR_WHITE
        center_text(status or ("@" + self.handle), 4, HANDLE_SIZE)

        screen.pen = COLOR_GOLD
        sub = "Steve Francia • spf13 labs" if self.handle.lower() == "spf13" else placeholder_if_none(self.name)
        center_text(sub, 36, NAME_SIZE)

        self.draw_stat("followers", self.followers, 176, 66, title_pen=COLOR_CYAN, val_pen=COLOR_WHITE)
        self.draw_stat("contribs", self.contribs, 176, 124, title_pen=COLOR_GOLD, val_pen=COLOR_WHITE)
        self.draw_stat("repos", self.repos, 176, 182, title_pen=COLOR_VIOLET, val_pen=COLOR_WHITE)

        self.draw_avatar(mode)

    def draw_brand_showcase(self, show_qr=False):
        self.draw_graph(BRAND_BG_LEVELS)

        banner = self.wide_banner
        bx = (screen.width - 300) // 2 + int(tilt.x * 6)
        by = 8 - int(tilt.y * 6)
        if banner:
            screen.blit(banner, rect(bx, by, 300, 96))
        else:
            screen.font = mona_sans
            screen.pen = COLOR_GOLD
            center_text("spf13 labs", by + 12, 36)

        if show_qr:
            if self.qr_code is None:
                try:
                    self.qr_code = image.qr("https://spf13.com", image.QR_LOW, 2)
                except Exception:
                    self.qr_code = None
            if self.qr_code:
                qr_size = 168
                qx = (screen.width - qr_size) // 2
                qy = 10
                screen.pen = white
                screen.rectangle(qx - 8, qy - 6, qr_size + 16, qr_size + 12)
                screen.blit(
                    self.qr_code,
                    rect(0, 0, self.qr_code.width, self.qr_code.height),
                    rect(qx, qy, qr_size, qr_size),
                )
            screen.font = mona_sans
            screen.pen = COLOR_GOLD
            center_text("https://spf13.com", 190, 18)
            screen.pen = COLOR_CREAM
            center_text("Steve Francia  •  @spf13", 214, 14)
        else:
            center_logo = self.avatar or self.brain_logo
            lx = (screen.width - 150) // 2 + int(tilt.x * 10)
            ly = 98 - int(tilt.y * 10)
            if center_logo:
                screen.blit(center_logo, rect(lx, ly, 150, 150))
            else:
                screen.font = mona_sans
                screen.pen = COLOR_WHITE
                center_text("spf13", 130, 32)

            screen.font = mona_sans
            screen.pen = COLOR_GOLD
            center_text("Creator of Hugo, Cobra & Viper", 212, 14)
            screen.pen = COLOR_DIM
            center_text("spf13.com  •  ^ QR", 228, 11)

    def draw_classic(self, status, mode=0):
        self.draw_graph(User.levels)

        screen.font = mona_sans
        screen.pen = white
        center_text(status or self.handle, 4, HANDLE_SIZE)

        screen.pen = phosphor
        center_text(placeholder_if_none(self.name), 36, NAME_SIZE)

        self.draw_stat("followers", self.followers, 176, 66)
        self.draw_stat("contribs", self.contribs, 176, 124)
        self.draw_stat("repos", self.repos, 176, 182)

        self.draw_avatar(mode)

def center_text(text, y, size):
    w, _ = screen.measure_text(text, font_size=size)
    if w > screen.width - 16:
        size = size * (screen.width - 16) / w
        w = screen.width - 16
    screen.text(text, screen.width / 2 - w / 2, y, size)

def setting(key):
    return str(getattr(secrets, key, "") or "").strip()

def linkedin_label(url):
    if not url:
        return ""
    path = url.split("://")[-1]
    if path.startswith("www."):
        path = path[4:]
    return path.rstrip("/")

handle = setting("GITHUB_USERNAME") or "spf13"
contact_name = setting("CONTACT_NAME") or "Steve Francia"
linkedin = linkedin_label(setting("CONTACT_LINKEDIN")) or "linkedin.com/in/stevefrancia"
user = User(handle)
badge_id = bytes(machine.unique_id())[:badgelink.ID_LEN]

current_view = 0
avatar_mode = 0
show_qr = False

def draw_simple(status):
    user.draw_graph(SPF13_LEVELS)
    screen.font = mona_sans
    screen.pen = white
    center_text(contact_name or handle or linkedin, 4, HANDLE_SIZE)
    details = []
    if handle and contact_name:
        details.append("@" + handle)
    if linkedin and (contact_name or handle):
        details.append(linkedin)
    screen.pen = COLOR_GOLD
    for index, line in enumerate(details):
        center_text(line, 36 + index * 18, NAME_SIZE)
    badgelink.draw_identicon(screen, badge_id, 10, 74, AVATAR_SIZE)

    if not handle:
        nudge = "Add your GitHub username in Settings or visit Hack Your Badge to show your stats"
    elif user.failed and wifi.failure():
        nudge = "Couldn't connect to WiFi. Visit the Hack Your Badge station to get your GitHub stats"
    elif user.missing or user.failed:
        nudge = "Visit the Hack Your Badge station to get your GitHub stats"
    else:
        nudge = status or "connecting to GitHub..."
    screen.pen = white
    screen.text(nudge, rect(*NUDGE_RECT), NUDGE_SIZE, align=(LEFT, TOP))

def update():
    global current_view, avatar_mode, show_qr
    tilt.update()

    screen.pen = black
    screen.clear()

    # View navigation
    if badge.pressed(BUTTON_SELECT):
        current_view = (current_view + 1) % 3
    elif badge.pressed(BUTTON_RIGHT) and not badge.held(BUTTON_LEFT):
        current_view = (current_view + 1) % 3
    elif badge.pressed(BUTTON_LEFT) and not badge.held(BUTTON_RIGHT):
        current_view = (current_view - 1) % 3

    # Toggle avatar / QR
    if badge.pressed(BUTTON_UP) or badge.pressed(BUTTON_DOWN):
        if current_view == 1:
            show_qr = not show_qr
        else:
            avatar_mode = 1 - avatar_mode

    # Force update if both held
    both_held = badge.held(BUTTON_LEFT) and badge.held(BUTTON_RIGHT)
    if handle and both_held and (badge.pressed(BUTTON_LEFT) or badge.pressed(BUTTON_RIGHT)):
        user.update(True)

    status = user.fetch_step() if handle else None

    # Caselights soft breathing glow on views 0 and 1
    if current_view == 0 or current_view == 1:
        glow = 0.08 + 0.06 * math.sin(badge.ticks / 1000)
        try:
            badge.caselights(glow)
        except Exception:
            pass
    else:
        try:
            badge.caselights(0)
        except Exception:
            pass

    if user.loaded:
        if current_view == 0:
            user.draw_spf13_badge(status, avatar_mode)
        elif current_view == 1:
            user.draw_brand_showcase(show_qr)
        else:
            user.draw_classic(status, avatar_mode)
    else:
        draw_simple(status)

run(update)
