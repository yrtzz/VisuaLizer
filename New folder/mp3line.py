import pygame
import numpy as np
import sounddevice as sd
import math
import time
import cv2
import ctypes
import json
import os

from spotify_client import SpotifyClient

# DPI-awareness — чёткие шрифты на HiDPI / 4K мониторах (fix #2)
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor v1
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    PYCAW_OK = True
except ImportError:
    PYCAW_OK = False

# ===== CONFIG =====
BARS       = 64
SAMPLERATE = 44100
BLOCKSIZE  = 1024
ROW_HEIGHT = 30
FORCE_REFRESH_DELAY = 0.15     # было 0.4 — быстрее обновление при переключении

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
KEYEVENTF_KEYUP     = 0x0002

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
DEFAULT_SETTINGS = {
    "device": 18, "sensitivity": 1.0, "always_on_top": False,
    "hide_ui": False, "hide_silent": True, "win_w": 900, "win_h": 600,
}

def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except:
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(s, f, indent=2)
    except:
        pass

settings = load_settings()

# ===== INIT =====
pygame.init()
screen = pygame.display.set_mode(
    (settings["win_w"], settings["win_h"]),
    pygame.RESIZABLE | pygame.DOUBLEBUF
)
pygame.display.set_caption("mp3line")

# Иконка приложения — программная (fix #6)
_icon = pygame.Surface((32, 32), pygame.SRCALPHA)
_icon.fill((0, 0, 0, 0))
pygame.draw.circle(_icon, (20, 20, 38), (16, 16), 15)
pygame.draw.circle(_icon, (80, 140, 255), (16, 16), 14, 2)
pygame.draw.line(_icon,   (80, 140, 255), (13, 7),  (13, 20), 2)
pygame.draw.line(_icon,   (80, 140, 255), (13, 7),  (21, 5),  2)
pygame.draw.line(_icon,   (80, 140, 255), (21, 5),  (21, 17), 2)
pygame.draw.circle(_icon, (80, 140, 255), (11, 21), 3)
pygame.draw.circle(_icon, (80, 140, 255), (19, 18), 3)
pygame.display.set_icon(_icon)
clock = pygame.time.Clock()

font       = pygame.font.SysFont("Segoe UI", 19)
font_small = pygame.font.SysFont("Segoe UI", 15)
font_tiny  = pygame.font.SysFont("Segoe UI", 13)
font_bold  = pygame.font.SysFont("Segoe UI", 17, bold=True)

# ===== SCALING (fix #5) =====
def calc_radius(W, H):
    """Радиус центрального круга — умеренный рост с окном.
    900×600 → 120,  1280×720 → 144,  1920×1080 → 190 (макс)."""
    factor = min(W / 900.0, H / 600.0)
    return max(80, min(190, int(120 * factor)))

# 3 набора шрифтов для имён треков: base / mid / large
_FSET = [
    (pygame.font.SysFont("Segoe UI", 19), pygame.font.SysFont("Segoe UI", 15)),  # ×1.0
    (pygame.font.SysFont("Segoe UI", 22), pygame.font.SysFont("Segoe UI", 17)),  # ×1.15
    (pygame.font.SysFont("Segoe UI", 26), pygame.font.SysFont("Segoe UI", 20)),  # ×1.35
]

def _track_fonts(sf: float):
    """Возвращает (font_main, font_next) под текущий scale factor."""
    if sf >= 1.35: return _FSET[2]
    if sf >= 1.15: return _FSET[1]
    return _FSET[0]

is_fullscreen = False

# ===== SYSTEM =====
def set_always_on_top(enable):
    try:
        hwnd = pygame.display.get_wm_info()["window"]
        flag = -1 if enable else -2
        ctypes.windll.user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, 0x0001 | 0x0002)
    except:
        pass

def center_window(w, h):
    """Центрирует окно на экране (fix #7)."""
    try:
        hwnd = pygame.display.get_wm_info()["window"]
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        x  = max(0, (sw - w) // 2)
        y  = max(0, (sh - h) // 2)
        ctypes.windll.user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0040)
    except Exception:
        pass

# Последние данные для быстрой перерисовки после смены режима (fix #9.6)
_last_draw_state: dict = {"info": None, "values": None}

def toggle_fullscreen():
    """Переключает полноэкранный режим + мгновенная перерисовка (fix #9.6)."""
    global screen, is_fullscreen
    is_fullscreen = not is_fullscreen
    if is_fullscreen:
        screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.DOUBLEBUF)
    else:
        screen = pygame.display.set_mode(
            (settings["win_w"], settings["win_h"]), pygame.RESIZABLE | pygame.DOUBLEBUF)
        center_window(settings["win_w"], settings["win_h"])
    if settings["always_on_top"]:
        set_always_on_top(True)
    # Немедленная перерисовка — убирает чёрный кадр (fix #9.6)
    if _last_draw_state["info"] is not None:
        v = _last_draw_state["values"]
        if v is None:
            v = np.zeros(BARS)
        draw(_last_draw_state["info"], v)
        pygame.display.flip()

def send_media_key(vk):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

if settings["always_on_top"]:
    set_always_on_top(True)

# Центрируем окно при запуске (fix #7)
center_window(settings["win_w"], settings["win_h"])

# ===== AUDIO =====
audio_buffer = np.zeros(BLOCKSIZE)

def audio_callback(indata, frames, time_, status):
    global audio_buffer
    mono = indata[:, 0] if indata.ndim > 1 else indata.flatten()
    # NaN защита на входе
    mono = np.nan_to_num(mono, nan=0.0, posinf=0.0, neginf=0.0)
    audio_buffer = mono

def make_stream(device_id):
    global audio_buffer, heights, volume_smooth, pulse_value
    # сбрасываем состояние при смене устройства
    audio_buffer  = np.zeros(BLOCKSIZE)
    heights       = np.zeros(BARS)
    volume_smooth = 0.1
    pulse_value   = 1.0
    try:
        dev = sd.query_devices(device_id)
        ch  = min(2, int(dev["max_input_channels"]))
        if ch == 0:
            raise ValueError("No input channels")
        s = sd.InputStream(device=device_id, channels=ch,
                           samplerate=SAMPLERATE, blocksize=BLOCKSIZE,
                           callback=audio_callback)
        s.start()
        return s
    except Exception as e:
        print("Stream error:", e)
        try:
            s = sd.InputStream(channels=1, samplerate=SAMPLERATE,
                               blocksize=BLOCKSIZE, callback=audio_callback)
            s.start()
            return s
        except:
            return None

stream_ref = [make_stream(settings["device"])]

# ===== VISUAL UTILS =====
def blur_surface(surface, ksize=35):
    arr = pygame.surfarray.array3d(surface)
    arr = np.transpose(arr, (1, 0, 2))
    arr = cv2.GaussianBlur(arr, (ksize, ksize), 0)
    arr = np.transpose(arr, (1, 0, 2))
    return pygame.surfarray.make_surface(arr)

def create_bloom(radius, color):
    size   = radius * 4
    surf   = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(surf, (*color, 255), (size//2, size//2), radius)
    arr = pygame.surfarray.array3d(surf)
    arr = np.transpose(arr, (1, 0, 2))
    arr = cv2.GaussianBlur(arr, (101, 101), 0)
    arr = np.transpose(arr, (1, 0, 2))
    return pygame.surfarray.make_surface(arr)

def rebuild_bg(cover_surface):
    w, h = screen.get_size()
    bg   = pygame.transform.smoothscale(cover_surface, (w, h))
    bg   = blur_surface(bg, 35)
    dark = pygame.Surface((w, h), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 200))
    bg.blit(dark, (0, 0))
    return bg

def make_circle_cover(cover_surface, r):
    surf = pygame.transform.smoothscale(cover_surface, (r * 2, r * 2))
    mask = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255), (r, r), r)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
    return surf

def draw_gear(surf, cx, cy, r_out, r_in, teeth, color, alpha=200):
    g  = pygame.Surface((r_out*2+4, r_out*2+4), pygame.SRCALPHA)
    gc = r_out + 2
    n  = teeth * 4
    pts = []
    for i in range(n):
        angle = (i/n)*2*math.pi - math.pi/2
        r = r_out if (i%4 < 2) else r_out-4
        pts.append((gc + math.cos(angle)*r, gc + math.sin(angle)*r))
    pygame.draw.polygon(g, (*color, alpha), pts)
    pygame.draw.circle(g, (0,0,0,0), (gc,gc), r_in)
    surf.blit(g, (cx-gc, cy-gc))

# ===== CHEVRON (стрелка шторки) =====
def draw_chevron(surf, cx, cy, w, h, flipped, color, alpha):
    """Рисует тонкую стрелку ∨ или ∧"""
    c = pygame.Surface((w+4, h+4), pygame.SRCALPHA)
    half = w // 2
    if not flipped:
        pts = [(2, 2), (half+2, h+2), (w+2, 2)]
    else:
        pts = [(2, h+2), (half+2, 2), (w+2, h+2)]
    pygame.draw.lines(c, (*color, alpha), False, pts, 2)
    surf.blit(c, (cx - half - 2, cy - h//2 - 2))

# ===== BUTTON ICONS =====
def draw_icon_prev(surf, cx, cy, size, color):
    for off in (size//3, -size//3+2):
        pts = [(cx+off,cy),(cx+off+size,cy-size),(cx+off+size,cy+size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_next(surf, cx, cy, size, color):
    for off in (-size//3, size//3-2):
        pts = [(cx+off,cy),(cx+off-size,cy-size),(cx+off-size,cy+size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_play(surf, cx, cy, size, color):
    pygame.draw.polygon(surf, color,
        [(cx+size,cy),(cx-size//2,cy-size),(cx-size//2,cy+size)])

def draw_icon_pause(surf, cx, cy, size, color):
    bw = max(3, size//3); g = size//2
    pygame.draw.rect(surf, color, (cx-g-bw, cy-size, bw, size*2))
    pygame.draw.rect(surf, color, (cx+g,    cy-size, bw, size*2))

def _draw_fullscreen_icon(surf, cx, cy, r, color, alpha=200):
    g = pygame.Surface((r*2+4, r*2+4), pygame.SRCALPHA)
    c = r + 2
    col = (*color, alpha)
    for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]:
        ax, ay = c+sx*r,   c+sy*r
        pygame.draw.line(g, col, (ax,ay), (c+sx*(r-4), c+sy*r), 2)
        pygame.draw.line(g, col, (ax,ay), (c+sx*r,     c+sy*(r-4)), 2)
    surf.blit(g, (cx-c, cy-c))

# ===== ALBUM CAROUSEL =====
ALBUM_CARD_W  = 120   # ширина карточки
ALBUM_CARD_H  = 140   # высота (обложка + текст)
ALBUM_COVER_S = 110   # размер обложки
ALBUM_GAP     = 16    # gap между карточками
CAROUSEL_PH   = ALBUM_CARD_H + 32   # высота панели шторки

class AlbumCarousel:
    def __init__(self):
        self.open        = False
        self.panel_y     = -CAROUSEL_PH   # текущая Y (анимация шторки)
        self.target_y    = -CAROUSEL_PH
        self.albums      = []
        self.scroll_x    = 0.0    # текущий пиксельный сдвиг (float)
        self.target_x    = 0.0   # цель скролла
        self.drag_start  = None  # (mouse_x, scroll_x_at_start)
        self.dragging    = False
        self.cover_cache = {}    # uri → scaled surface
        self._card_w     = ALBUM_CARD_W + ALBUM_GAP

    def toggle(self):
        self.open    = not self.open
        self.target_y = 0 if self.open else -CAROUSEL_PH

    def set_albums(self, albums):
        if albums and albums != self.albums:
            self.albums = albums
            self.cover_cache = {}

    def _get_cover(self, album):
        uri = album["uri"]
        if uri not in self.cover_cache:
            raw = album.get("cover")
            if raw:
                scaled = pygame.transform.smoothscale(raw, (ALBUM_COVER_S, ALBUM_COVER_S))
                # закруглённая маска
                mask = pygame.Surface((ALBUM_COVER_S, ALBUM_COVER_S), pygame.SRCALPHA)
                pygame.draw.rect(mask, (255,255,255), (0,0,ALBUM_COVER_S,ALBUM_COVER_S), border_radius=10)
                scaled.blit(mask, (0,0), special_flags=pygame.BLEND_RGBA_MULT)
                self.cover_cache[uri] = scaled
            else:
                # плейсхолдер
                ph = pygame.Surface((ALBUM_COVER_S, ALBUM_COVER_S), pygame.SRCALPHA)
                ph.fill((45, 45, 65))
                pygame.draw.rect(ph, (70,70,100), (0,0,ALBUM_COVER_S,ALBUM_COVER_S), border_radius=10)
                self.cover_cache[uri] = ph
        return self.cover_cache[uri]

    def _clamp_target(self):
        if not self.albums:
            return
        max_scroll = max(0, len(self.albums) * self._card_w - 3 * self._card_w)
        self.target_x = max(0.0, min(float(max_scroll), self.target_x))

    def handle(self, event, W):
        """Возвращает album uri если нажали на альбом, иначе None."""
        if not self.open:
            return None

        # область панели (y = int(panel_y) до int(panel_y)+CAROUSEL_PH)
        panel_rect = pygame.Rect(0, int(self.panel_y), W, CAROUSEL_PH)

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ex, ey = event.pos
            if not panel_rect.collidepoint(ex, ey):
                return None
            self.drag_start = (ex, self.scroll_x)
            self.dragging   = True

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if self.dragging and self.drag_start:
                ex, ey = event.pos
                moved = abs(ex - self.drag_start[0])
                if moved < 6:
                    # это клик — ищем альбом под курсором
                    uri = self._album_at(ex, ey, W)
                    self.dragging   = False
                    self.drag_start = None
                    return uri
            self.dragging   = False
            self.drag_start = None

        elif event.type == pygame.MOUSEMOTION:
            if self.dragging and self.drag_start:
                ex = event.pos[0]
                delta = self.drag_start[0] - ex
                self.scroll_x  = self.drag_start[1] + delta
                self.target_x  = self.scroll_x
                self._clamp_target()
                self.scroll_x  = self.target_x

        elif event.type == pygame.MOUSEWHEEL:
            mx, my = pygame.mouse.get_pos()
            if panel_rect.collidepoint(mx, my):
                self.target_x -= event.y * self._card_w
                self._clamp_target()

        return None

    def _album_at(self, ex, ey, W):
        """Возвращает uri альбома под (ex, ey) или None."""
        start_x = (W - 3 * self._card_w + ALBUM_GAP) // 2
        panel_top = int(self.panel_y) + 12

        for i, album in enumerate(self.albums):
            ax = start_x + i * self._card_w - int(self.scroll_x)
            rect = pygame.Rect(ax, panel_top, ALBUM_CARD_W, ALBUM_CARD_H)
            if rect.collidepoint(ex, ey):
                return album["uri"]
        return None

    def update(self):
        # плавная анимация шторки
        self.panel_y += (self.target_y - self.panel_y) * 0.18
        if abs(self.panel_y - self.target_y) < 0.5:
            self.panel_y = float(self.target_y)

        # плавный горизонтальный скролл
        self.scroll_x += (self.target_x - self.scroll_x) * 0.14
        if abs(self.scroll_x - self.target_x) < 0.3:
            self.scroll_x = self.target_x

    def draw(self):
        if self.panel_y <= -CAROUSEL_PH + 1 and not self.open:
            return

        W, H = screen.get_size()
        py   = int(self.panel_y)

        # фон панели
        panel = pygame.Surface((W, CAROUSEL_PH), pygame.SRCALPHA)
        panel.fill((12, 12, 22, 110))
        screen.blit(panel, (0, py))

        # нижняя линия
        pygame.draw.line(screen, (60,60,100), (0, py+CAROUSEL_PH-1), (W, py+CAROUSEL_PH-1))

        if not self.albums:
            msg = font_small.render("Загрузка альбомов...", True, (120,120,150))
            screen.blit(msg, (W//2 - msg.get_width()//2, py+CAROUSEL_PH//2-8))
            return

        # рисуем 3 видимых + частично соседние
        start_x   = (W - 3 * self._card_w + ALBUM_GAP) // 2
        panel_top = py + 12
        scroll_i  = int(self.scroll_x)

        old_clip = screen.get_clip()
        screen.set_clip(pygame.Rect(0, py, W, CAROUSEL_PH))

        mx, my = pygame.mouse.get_pos()

        for i, album in enumerate(self.albums):
            ax = start_x + i * self._card_w - scroll_i
            # рисуем только видимые карточки
            if ax + ALBUM_CARD_W < 0 or ax > W:
                continue

            cover = self._get_cover(album)

            card_rect = pygame.Rect(ax, panel_top, ALBUM_CARD_W, ALBUM_CARD_H)
            hover     = card_rect.collidepoint(mx, my)

            # подсветка при hover
            if hover:
                hl = pygame.Surface((ALBUM_CARD_W, ALBUM_CARD_H), pygame.SRCALPHA)
                hl.fill((255,255,255,18))
                pygame.draw.rect(hl, (255,255,255,40), (0,0,ALBUM_CARD_W,ALBUM_CARD_H), border_radius=10)
                screen.blit(hl, (ax, panel_top))

            # обложка
            cover_x = ax + (ALBUM_CARD_W - ALBUM_COVER_S) // 2
            screen.blit(cover, (cover_x, panel_top + 2))

            # название альбома
            name = album["name"]
            if len(name) > 14:
                name = name[:13] + "…"
            nt = font_tiny.render(name, True, (210,210,230) if hover else (160,160,185))
            screen.blit(nt, (ax + (ALBUM_CARD_W - nt.get_width())//2, panel_top + ALBUM_COVER_S + 6))

            # артист
            artist = album["artist"]
            if len(artist) > 14:
                artist = artist[:13] + "…"
            at = font_tiny.render(artist, True, (120,120,150))
            screen.blit(at, (ax + (ALBUM_CARD_W - at.get_width())//2, panel_top + ALBUM_COVER_S + 20))

        screen.set_clip(old_clip)

        # индикатор скролла
        if len(self.albums) > 3:
            tot    = len(self.albums)
            max_sc = max(1, tot * self._card_w - 3 * self._card_w)
            frac   = self.scroll_x / max_sc
            dot_w  = max(30, int(W * 3 / tot))
            dot_x  = int(frac * (W - dot_w))
            pygame.draw.rect(screen, (40,40,60), (0, py+CAROUSEL_PH-4, W, 3), border_radius=2)
            pygame.draw.rect(screen, (80,120,220), (dot_x, py+CAROUSEL_PH-4, dot_w, 3), border_radius=2)


# ===== SETTINGS UI =====
class SettingsUI:
    PW, PH = 660, 560    # было 580×510 — увеличено чтобы UI не вылезал (fix #1)
    TAB_AUDIO, TAB_MIXER = 0, 1

    def __init__(self):
        self.open         = False
        self.tab          = self.TAB_AUDIO
        self.devices      = []
        self.dev_scroll   = 0
        self.sens_drag    = False
        self.mix_sessions = []
        self.mix_scroll   = 0
        self.mix_drag     = None
        self.mix_last_ref = 0
        self.muted_vols: dict[str, float] = {}   # имя→громкость до заглушки (fix #4)
        self._fade_alpha  = 0.0                  # 0..255, анимация появления (fix #9.5)
        self._fade_target = 0.0
        self._scan_devices()

    def _scan_devices(self):
        self.devices = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                self.devices.append((i, d["name"]))

    def _refresh_mixer(self):
        if not PYCAW_OK or time.time() - self.mix_last_ref < 0.6:
            return
        self.mix_last_ref = time.time()
        try:
            sessions = AudioUtilities.GetAllSessions()
            result   = []
            for s in sessions:
                if s.Process:
                    ctl = s._ctl.QueryInterface(ISimpleAudioVolume)
                    result.append({"name": s.Process.name().replace(".exe",""),
                                   "volume": ctl.GetMasterVolume(), "_ctl": ctl})
            self.mix_sessions = result
        except Exception as e:
            print("Mixer refresh:", e)

    def _visible(self):
        if settings.get("hide_silent"):
            return [s for s in self.mix_sessions if s["volume"] > 0.005]
        return self.mix_sessions

    def toggle(self):
        self.open = not self.open
        self._fade_target = 255.0 if self.open else 0.0
        if self.open:
            self._scan_devices()

    def _panel(self):
        W, H = screen.get_size()
        return pygame.Rect((W-self.PW)//2, (H-self.PH)//2, self.PW, self.PH)

    def handle(self, event):
        if not self.open:
            return False
        p      = self._panel()
        mx, my = pygame.mouse.get_pos()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ex, ey = event.pos
            if pygame.Rect(p.right-30, p.top+8, 22, 22).collidepoint(ex, ey):
                self.open = False; self._fade_target = 0.0; save_settings(settings); return True
            if not p.collidepoint(ex, ey):
                self.open = False; self._fade_target = 0.0; save_settings(settings); return True
            tab_y = p.top+40; tw = self.PW//2
            if pygame.Rect(p.x, tab_y, tw, 28).collidepoint(ex, ey):    self.tab = self.TAB_AUDIO
            if pygame.Rect(p.x+tw, tab_y, tw, 28).collidepoint(ex, ey): self.tab = self.TAB_MIXER
            cy = tab_y + 38
            if self.tab == self.TAB_AUDIO:  self._click_audio(ex, ey, p, cy)
            else:                            self._click_mixer(ex, ey, p, cy)
            return True

        elif event.type == pygame.MOUSEBUTTONUP:
            self.sens_drag = False; self.mix_drag = None

        elif event.type == pygame.MOUSEMOTION:
            if self.sens_drag:    self._drag_sens(mx, self._panel())
            if self.mix_drag is not None: self._drag_mix(mx)

        elif event.type == pygame.MOUSEWHEEL:
            if p.collidepoint(*pygame.mouse.get_pos()):
                if self.tab == self.TAB_AUDIO:
                    self.dev_scroll = max(0, min(len(self.devices)-4, self.dev_scroll - event.y))
                else:
                    vis = self._visible()
                    self.mix_scroll = max(0, min(max(0,len(vis)-7), self.mix_scroll - event.y))
                return True

        return self.open

    def _click_audio(self, ex, ey, p, cy):
        global screen, is_fullscreen
        cx = p.x+20
        for i, (did, _) in enumerate(self.devices[self.dev_scroll:self.dev_scroll+4]):
            if pygame.Rect(cx, cy+22+i*26, self.PW-40, 22).collidepoint(ex, ey):
                settings["device"] = did
                if stream_ref[0]:
                    try: stream_ref[0].stop(); stream_ref[0].close()
                    except: pass
                stream_ref[0] = make_stream(did)
                return
        sy = cy+140; sx = cx+110; sw = self.PW-205   # синхронизировано с _draw_audio
        if pygame.Rect(sx-5, sy, sw+10, 20).collidepoint(ex, ey):
            self.sens_drag = True; self._drag_sens(ex, self._panel()); return
        ty0 = sy+42
        for i, key in enumerate(["always_on_top","hide_ui","hide_silent"]):
            if pygame.Rect(p.x+self.PW-62, ty0+i*36, 44, 22).collidepoint(ex, ey):
                settings[key] = not settings[key]
                if key == "always_on_top": set_always_on_top(settings["always_on_top"])
                return
        # window size presets
        wy0    = ty0 + 3*36 + 18
        btn_w  = (self.PW - 44) // 4 - 6
        presets = [(900,600),(1280,720),(1600,900),(1920,1080)]
        for i, (pw, ph) in enumerate(presets):
            bx = cx + i * (btn_w + 8)
            if pygame.Rect(bx, wy0+18, btn_w, 34).collidepoint(ex, ey):
                settings["win_w"] = pw
                settings["win_h"] = ph
                if not is_fullscreen:
                    screen = pygame.display.set_mode(
                        (pw, ph), pygame.RESIZABLE | pygame.DOUBLEBUF)
                    center_window(pw, ph)      # центрировать после смены размера (fix #7)
                    if settings["always_on_top"]:
                        set_always_on_top(True)
                return

    def _drag_sens(self, mx, p):
        sx = p.x+130; sw = self.PW-205   # синхронизировано
        frac = max(0.0, min(1.0, (mx-sx)/sw))
        settings["sensitivity"] = round(0.3 + frac*2.7, 2)

    def _click_mixer(self, ex, ey, p, cy):
        vis = self._visible()
        sx  = p.x + 195          # сдвинуто вправо под кнопку заглушки (fix #3)
        sw  = self.PW - 260      # короче (fix #3)
        row_h = 40
        for i, s in enumerate(vis[self.mix_scroll:self.mix_scroll+7]):
            abs_i = i + self.mix_scroll
            ry    = cy + i * row_h

            # --- кнопка заглушки (fix #4) ---
            mute_rect = pygame.Rect(p.x + 20, ry + 9, 22, 22)
            if mute_rect.collidepoint(ex, ey):
                name = s["name"]
                if name in self.muted_vols:
                    # возвращаем прежний уровень
                    prev = self.muted_vols.pop(name)
                    try: s["_ctl"].SetMasterVolume(prev, None); s["volume"] = prev
                    except: pass
                else:
                    # запоминаем и заглушаем
                    self.muted_vols[name] = s["volume"]
                    try: s["_ctl"].SetMasterVolume(0.0, None); s["volume"] = 0.0
                    except: pass
                return

            # --- слайдер ---
            if pygame.Rect(sx, ry + 8, sw, 18).inflate(0, 8).collidepoint(ex, ey):
                frac = max(0.0, min(1.0, (ex - sx) / sw))
                try: s["_ctl"].SetMasterVolume(frac, None); s["volume"] = frac
                except: pass
                self.mix_drag = (abs_i, sx, sw)
                # если перетаскиваем — снимаем заглушку для этого канала
                if s["name"] in self.muted_vols:
                    self.muted_vols.pop(s["name"])
                return

    def _drag_mix(self, mx):
        if self.mix_drag is None: return
        abs_i, sx, sw = self.mix_drag
        vis = self._visible()
        if abs_i < len(vis):
            frac = max(0.0, min(1.0, (mx-sx)/sw))
            try: vis[abs_i]["_ctl"].SetMasterVolume(frac, None); vis[abs_i]["volume"] = frac
            except: pass

    def draw(self):
        # анимация появления/скрытия (fix #9.5)
        self._fade_alpha += (self._fade_target - self._fade_alpha) * 0.18
        if self._fade_alpha < 1.0:
            return
        fade = min(1.0, self._fade_alpha / 255.0)

        if self.tab == self.TAB_MIXER: self._refresh_mixer()
        W, H   = screen.get_size()
        p      = self._panel()
        mx, my = pygame.mouse.get_pos()

        dim = pygame.Surface((W,H), pygame.SRCALPHA)
        dim.fill((0,0,0, int(150 * fade)))
        screen.blit(dim, (0,0))

        ps = pygame.Surface((self.PW,self.PH), pygame.SRCALPHA)
        ps.fill((16,16,26, int(235 * fade)))
        screen.blit(ps, (p.x,p.y))
        pygame.draw.rect(screen,(55,55,80),p,1,border_radius=10)

        screen.blit(font_bold.render("Settings",True,(210,210,230)),(p.x+20,p.y+12))
        cr  = pygame.Rect(p.right-30,p.top+8,22,22)
        cc  = (220,70,70) if cr.collidepoint(mx,my) else (130,130,150)
        pygame.draw.line(screen,cc,(cr.left+5,cr.top+5),(cr.right-5,cr.bottom-5),2)
        pygame.draw.line(screen,cc,(cr.right-5,cr.top+5),(cr.left+5,cr.bottom-5),2)

        tab_y=p.top+40; tw=self.PW//2
        for i,lbl in enumerate(["Audio & Display","Volume Mixer"]):
            tx=p.x+i*tw; act=(i==self.tab)
            ts=pygame.Surface((tw,28),pygame.SRCALPHA)
            ts.fill((38,38,60,200) if act else (22,22,35,120)); screen.blit(ts,(tx,tab_y))
            tc=font_small.render(lbl,True,(255,255,255) if act else (110,110,140))
            screen.blit(tc,(tx+(tw-tc.get_width())//2,tab_y+6))
            if act: pygame.draw.line(screen,(80,140,255),(tx,tab_y+27),(tx+tw,tab_y+27),2)
        pygame.draw.line(screen,(50,50,75),(p.x,tab_y+28),(p.right,tab_y+28))

        cy=tab_y+38
        if self.tab==self.TAB_AUDIO: self._draw_audio(p,cy,mx,my)
        else:                         self._draw_mixer(p,cy,mx,my)

    def _draw_audio(self,p,cy,mx,my):
        cx=p.x+20
        self._label(cx,cy,"INPUT DEVICE")
        for i,(did,dname) in enumerate(self.devices[self.dev_scroll:self.dev_scroll+4]):
            row=pygame.Rect(cx,cy+20+i*26,self.PW-44,22)
            sel=(did==settings["device"]); hov=row.collidepoint(mx,my)
            rs=pygame.Surface((row.w,row.h),pygame.SRCALPHA)
            rs.fill((50,100,190,160) if sel else ((45,45,68,90) if hov else (0,0,0,0)))
            screen.blit(rs,(row.x,row.y))
            if sel: pygame.draw.rect(screen,(80,140,255),row,1,border_radius=3)
            nm=dname[:48]+"…" if len(dname)>48 else dname
            screen.blit(font_tiny.render(nm,True,(255,255,255) if sel else (170,170,200)),(row.x+6,row.y+4))
        tot=len(self.devices)
        if tot>4:
            bh=max(18,4/tot*(4*26)); by=self.dev_scroll/(tot-4)*(4*26-bh)
            pygame.draw.rect(screen,(55,55,80),(p.right-12,cy+20,4,4*26),border_radius=2)
            pygame.draw.rect(screen,(120,120,180),(p.right-12,cy+20+by,4,bh),border_radius=2)

        sy=cy+130; self._label(cx,sy,"SENSITIVITY")
        ssx=cx+110; ssw=self.PW-205     # было PW-155 — оставляем место для метки и скроллбара
        frac=(settings["sensitivity"]-0.3)/2.7
        self._slider(ssx,sy+14,ssw,frac,mx,my,f"{settings['sensitivity']:.1f}×")

        ty0=sy+46
        for i,(key,lbl) in enumerate([
            ("always_on_top","Always on Top"),
            ("hide_ui",      "Hide UI  (H)"),
            ("hide_silent",  "Hide silent in Mixer"),
        ]):
            ty=ty0+i*36
            screen.blit(font_small.render(lbl,True,(190,190,215)),(cx,ty+3))
            self._toggle(p.x+self.PW-62,ty,settings[key])

        # --- Window Size presets ---
        wy0 = ty0 + 3*36 + 18
        self._label(cx, wy0, "WINDOW SIZE")
        presets = [(900,600),(1280,720),(1600,900),(1920,1080)]
        labels  = ["900×600","1280×720","1600×900","1920×1080"]
        notes   = ["compact","bars fit ✓","comfortable ✓","full HD ✓"]
        btn_w   = (self.PW - 44) // 4 - 6
        for i, ((pw, ph), lbl, note) in enumerate(zip(presets, labels, notes)):
            bx  = cx + i * (btn_w + 8)
            by  = wy0 + 18
            sel = (settings["win_w"] == pw and settings["win_h"] == ph)
            hov = pygame.Rect(bx, by, btn_w, 34).collidepoint(mx, my)
            col_bg = (50,100,190,180) if sel else ((45,45,72,140) if hov else (28,28,46,120))
            col_bd = (80,140,255) if sel else ((70,70,110) if hov else (45,45,70))
            bs = pygame.Surface((btn_w, 34), pygame.SRCALPHA)
            bs.fill(col_bg)
            screen.blit(bs, (bx, by))
            pygame.draw.rect(screen, col_bd, (bx, by, btn_w, 34), 1, border_radius=5)
            lt = font_tiny.render(lbl,  True, (255,255,255) if sel else (190,190,215))
            nt = font_tiny.render(note, True, (80,200,120)  if "✓" in note else (110,110,140))
            screen.blit(lt, (bx + (btn_w - lt.get_width())//2, by + 3))
            screen.blit(nt, (bx + (btn_w - nt.get_width())//2, by + 18))

    def _draw_mixer(self,p,cy,mx,my):
        if not PYCAW_OK:
            screen.blit(font_small.render("pip install pycaw comtypes",True,(200,90,90)),(p.x+20,cy+20)); return
        vis = self._visible()
        sx    = p.x + 195        # сдвинуто для кнопки заглушки (fix #3)
        sw    = self.PW - 260    # короче (fix #3)
        row_h = 40
        if not vis:
            screen.blit(font_small.render("Нет активных сессий",True,(130,130,155)),(p.x+20,cy+20)); return
        for i, s in enumerate(vis[self.mix_scroll:self.mix_scroll+7]):
            ry   = cy + i * row_h
            name = s["name"]
            is_muted = name in self.muted_vols

            # кнопка заглушки (fix #4)
            btn_rect = pygame.Rect(p.x + 20, ry + 9, 22, 22)
            btn_hov  = btn_rect.collidepoint(mx, my)
            btn_col  = (200, 55, 55) if is_muted else ((70, 160, 70) if btn_hov else (55, 120, 55))
            pygame.draw.rect(screen, btn_col, btn_rect, border_radius=4)
            # иконка: X (заглушено) или маленькие полоски (активно)
            bx, by = btn_rect.x, btn_rect.y
            if is_muted:
                pygame.draw.line(screen, (255,255,255), (bx+5, by+5), (bx+17, by+17), 2)
                pygame.draw.line(screen, (255,255,255), (bx+17, by+5), (bx+5, by+17), 2)
            else:
                for lx, lh in [(bx+5,8),(bx+9,12),(bx+13,8),(bx+17,5)]:
                    pygame.draw.line(screen,(255,255,255),(lx, by+11-lh//2),(lx, by+11+lh//2),2)

            # название приложения
            screen.blit(font_small.render(name[:16], True, (195,195,220)), (p.x+48, ry+12))

            # слайдер (используем текущий volume)
            vol = 0.0 if is_muted else s["volume"]
            self._slider(sx, ry+18, sw, vol, mx, my, f"{int(s['volume']*100)}%")

        tot = len(vis)
        if tot > 7:
            bh = max(20, 7/tot*(7*row_h)); by = self.mix_scroll/(tot-7)*(7*row_h-bh)
            pygame.draw.rect(screen,(55,55,80),(p.right-12,cy,4,7*row_h),border_radius=2)
            pygame.draw.rect(screen,(120,120,180),(p.right-12,cy+by,4,bh),border_radius=2)

    def _slider(self,x,y,w,frac,mx,my,label=""):
        pygame.draw.rect(screen,(45,45,68),(x,y-2,w,4),border_radius=2)
        pygame.draw.rect(screen,(80,140,255),(x,y-2,int(w*frac),4),border_radius=2)
        kx=x+int(w*frac); hov=abs(mx-kx)<14 and abs(my-y)<12
        pygame.draw.circle(screen,(255,255,255),(kx,y),7 if hov else 5)
        if label: screen.blit(font_tiny.render(label,True,(150,150,175)),(x+w+8,y-7))

    def _toggle(self,x,y,val):
        pygame.draw.rect(screen,(55,135,75) if val else (55,55,80),(x,y,44,22),border_radius=11)
        pygame.draw.circle(screen,(235,235,235),(x+(32 if val else 12),y+11),8)

    def _label(self,x,y,text):
        screen.blit(font_tiny.render(text,True,(90,90,130)),(x,y))


# ===== STATE =====
# radius пересчитывается в main loop при каждом изменении размера окна (fix #5)
radius       = 120
cover_radius = radius - 3

heights       = np.zeros(BARS)
rotation      = 0.0
pulse_value   = 1.0
volume_smooth = 0.1

cached_cover     = None
cached_track     = None
cached_bg        = None
cached_bloom     = None
cached_raw_cover = None
main_color       = (255, 255, 255)

is_transitioning    = False
transition_progress = 1.0
old_cover           = None

list_offset       = 0.0
is_list_animating = False

# Стабильные «следующие треки» — обновляются только при смене трека (fix #9.1)
_stable_next_tracks: list = []

pending_refresh = False
refresh_after   = 0.0
is_playing      = True
_prog_bar_rect  = None

settings_ui    = SettingsUI()
album_carousel = AlbumCarousel()

# ===== AUDIO PROCESS =====
def process_audio():
    global heights, pulse_value, volume_smooth

    buf = np.nan_to_num(audio_buffer, nan=0.0, posinf=0.0, neginf=0.0)

    fft       = np.fft.rfft(buf)
    magnitude = np.abs(fft)
    magnitude = np.nan_to_num(magnitude, nan=0.0, posinf=0.0, neginf=0.0)

    bins   = np.array_split(magnitude, BARS//2)
    values = np.array([np.mean(b) for b in bins])
    values = np.concatenate((values, values[::-1]))
    values = np.log1p(values) * 200 * settings["sensitivity"]
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

    cur           = float(np.mean(values))
    volume_smooth = volume_smooth * 0.9 + cur * 0.1

    if volume_smooth > 1e-6:
        values  = values / volume_smooth * 10
        heights = heights * 0.7 + values * 0.3

    heights = np.nan_to_num(heights, nan=0.0)

    bass        = float(np.mean(values[:5])) * 0.004
    bass        = min(bass, 0.25)
    new_pulse   = pulse_value * 0.8 + (1 + bass) * 0.2

    # NaN guard — главная защита от краша
    if math.isnan(new_pulse) or math.isinf(new_pulse):
        pulse_value = 1.0
    else:
        pulse_value = new_pulse

    return values

# ===== TRACK UPDATE =====
def update_track(info, force=False):
    global cached_track, cached_cover, cached_bg, cached_bloom, main_color
    global is_transitioning, transition_progress, old_cover
    global is_list_animating, list_offset, cached_raw_cover
    global _stable_next_tracks

    name  = info["name"]
    cover = info["cover"]
    if cover is None or (name == cached_track and not force):
        return

    old_cover        = cached_cover
    cached_track     = name
    cached_raw_cover = cover

    small      = pygame.transform.smoothscale(cover, (1, 1))
    main_color = small.get_at((0, 0))[:3]

    cached_bg    = rebuild_bg(cover)
    cached_cover = make_circle_cover(cover, cover_radius)
    cached_bloom = create_bloom(radius, main_color)

    # Обновляем стабильный список следующих треков (fix #9.1)
    _stable_next_tracks = list(info.get("next", []))

    transition_progress = 0.0
    is_transitioning    = True
    list_offset         = float(ROW_HEIGHT)
    is_list_animating   = True

# ===== CONTROLS =====
def force_refresh_later():
    global pending_refresh, refresh_after
    pending_refresh = True
    refresh_after   = time.time() + FORCE_REFRESH_DELAY

def on_prev():
    send_media_key(VK_MEDIA_PREV_TRACK); force_refresh_later()

def on_play_pause():
    send_media_key(VK_MEDIA_PLAY_PAUSE)
    force_refresh_later()   # мгновенное обновление UI (fix #9.3)

def on_next():
    send_media_key(VK_MEDIA_NEXT_TRACK); force_refresh_later()

def on_seek(click_x):
    if _prog_bar_rect is None: return
    frac = max(0.0, min(1.0, (click_x-_prog_bar_rect.x)/_prog_bar_rect.width))
    inf  = spotify.get_info()
    dur  = inf.get("duration", 0)
    if dur > 0:
        spotify.seek(int(frac * dur * 1000))

# ===== DRAW =====
def draw(info, values):
    global rotation, transition_progress, is_transitioning
    global list_offset, is_list_animating, cached_bg
    global is_playing, _prog_bar_rect

    W, H = screen.get_size()
    cx   = W // 2
    cy   = H // 2
    sf   = radius / 120.0                         # scale factor (fix #5)
    dyn  = radius * max(0.5, pulse_value)

    name        = info["name"]
    artist      = info["artist"]
    progress    = info["progress"]
    duration    = info["duration"]
    next_tracks = _stable_next_tracks   # стабильный список — без мерцания (fix #9.1)
    is_playing  = info.get("is_playing", is_playing)

    # background
    if cached_bg:
        bw, bh = cached_bg.get_size()
        if bw != W or bh != H:
            if cached_raw_cover:
                cached_bg = rebuild_bg(cached_raw_cover)
        screen.blit(cached_bg, (0,0))
    else:
        screen.fill((10,10,20))

    # cover fade
    if is_transitioning and old_cover is not None:
        transition_progress += 0.05
        clamp = min(transition_progress, 1.0)
        size  = max(4, int(cover_radius*2*max(0.5, pulse_value)))
        old   = pygame.transform.smoothscale(old_cover.copy(),    (size,size))
        new   = pygame.transform.smoothscale(cached_cover.copy(), (size,size))
        old.set_alpha(255-int(255*clamp)); new.set_alpha(int(255*clamp))
        screen.blit(old, (cx-size//2, cy-size//2))
        screen.blit(new, (cx-size//2, cy-size//2))
        if transition_progress >= 1:
            transition_progress=1.0; is_transitioning=False
    elif cached_cover:
        size = max(4, int(cover_radius*2*max(0.5, pulse_value)))
        screen.blit(pygame.transform.smoothscale(cached_cover,(size,size)),(cx-size//2,cy-size//2))

    _draw_visualizer(cx, cy, dyn, W, H)

    # карусель поверх визуализатора
    album_carousel.update()
    album_carousel.draw()

    if settings["hide_ui"]:
        settings_ui.draw()
        return

    # roulette
    if is_list_animating:
        list_offset *= 0.82
        if list_offset < 0.5:
            list_offset=0.0; is_list_animating=False

    track_list  = [("cur",(name,artist))]
    track_list += [("nxt",t) for t in next_tracks[:2]]
    f_cur, f_nxt = _track_fonts(sf)             # масштабированные шрифты (fix #5)
    row_h    = max(ROW_HEIGHT, int(ROW_HEIGHT * sf))
    base_y   = 20
    old_clip = screen.get_clip()
    screen.set_clip(pygame.Rect(0, 0, W//2, base_y + row_h*5))
    for i,(typ,(t,a)) in enumerate(track_list):
        y    = base_y + i*row_h + int(list_offset)
        line = f"{t} — {a}"
        if typ=="cur":
            txt = f_cur.render(line, True, (255,255,255))
        else:
            fade=max(0,130-i*30)
            txt=f_nxt.render(line,True,(200,200,200)); txt.set_alpha(fade)
        screen.blit(txt,(20,y))
    screen.set_clip(old_clip)

    mx, my = pygame.mouse.get_pos()

    # chevron — всегда у вверха экрана, не двигается
    chev_x   = cx
    chev_y   = 14
    chev_hov = abs(mx-chev_x) < 34 and abs(my-chev_y) < 18
    chev_alpha = 255 if chev_hov else 160
    draw_chevron(screen, chev_x, chev_y, 32, 10,
                 flipped=album_carousel.open,
                 color=(200,200,230), alpha=chev_alpha)

    # progress bar — позиция и ширина масштабируются с радиусом (fix #5)
    if duration > 0:
        bar_w = int(radius*2 + 40*sf); bx=cx-bar_w//2; by=cy+radius+int(60*sf)
        frac  = min(1.0, progress/duration)
        _prog_bar_rect = pygame.Rect(bx,by,bar_w,10)
        hover  = _prog_bar_rect.inflate(0,16).collidepoint(mx,my)
        bar_h  = 8 if hover else 5; byd=by+(10-bar_h)//2
        pygame.draw.rect(screen,(60,60,60),   (bx,byd,bar_w,bar_h),border_radius=4)
        pygame.draw.rect(screen,(255,255,255),(bx,byd,int(bar_w*frac),bar_h),border_radius=4)
        if hover: pygame.draw.circle(screen,(255,255,255),(bx+int(bar_w*frac),byd+bar_h//2),6)
        screen.blit(font_small.render(time.strftime('%M:%S',time.gmtime(progress)),True,(170,170,170)),(bx,by+14))
        screen.blit(font_small.render(time.strftime('%M:%S',time.gmtime(duration)),True,(170,170,170)),(bx+bar_w-38,by+14))

    # control buttons — размер и отступы масштабируются (fix #5)
    btn_y   = cy + radius + int(108 * sf)
    btn_sz  = int(9  * max(1.0, sf))
    btn_gap = int(55 * max(1.0, sf))
    bsurf=pygame.Surface((W,H),pygame.SRCALPHA)
    for key,bxb in [("prev",cx-btn_gap),("play",cx),("next",cx+btn_gap)]:
        hov=math.hypot(mx-bxb,my-btn_y)<btn_sz*2.5
        col=(255,255,255) if hov else (180,180,180); alph=255 if hov else 190
        sz=int(btn_sz*(1.2 if hov else 1.0)); rgba=(*col,alph)
        if key=="prev":   draw_icon_prev(bsurf,bxb,btn_y,sz,rgba)
        elif key=="next": draw_icon_next(bsurf,bxb,btn_y,sz,rgba)
        else: (draw_icon_pause if is_playing else draw_icon_play)(bsurf,bxb,btn_y,sz,rgba)
    screen.blit(bsurf,(0,0))

    # fullscreen + gear
    fs_x=W-52; fs_y=22
    fs_hov=math.hypot(mx-fs_x,my-fs_y)<14
    _draw_fullscreen_icon(screen,fs_x,fs_y,9,(200,200,230),220 if fs_hov else 100)
    gear_x=W-26; gear_y=22
    gear_hov=math.hypot(mx-gear_x,my-gear_y)<18
    draw_gear(screen,gear_x,gear_y,12,5,7,(200,200,230),220 if gear_hov else 100)

    settings_ui.draw()


def _draw_visualizer(cx, cy, dyn, W, H):
    if cached_bloom:
        size=max(4,int(radius*4*max(0.5,pulse_value)))
        screen.blit(pygame.transform.smoothscale(cached_bloom,(size,size)),
                    (cx-size//2,cy-size//2),special_flags=pygame.BLEND_ADD)
    glow=pygame.Surface((W,H),pygame.SRCALPHA)
    for i in range(20):
        pygame.draw.circle(glow,(*main_color,max(5,100-i*7)),(cx,cy),int(dyn+i*4))
    screen.blit(glow,(0,0))
    global rotation
    quarter=heights[:BARS//4]; circle_values=np.tile(quarter,4)
    rotation+=0.01
    for i in range(BARS):
        angle=(i/BARS)*2*math.pi+rotation; v=circle_values[i]
        x1=cx+math.cos(angle)*dyn; y1=cy+math.sin(angle)*dyn
        x2=cx+math.cos(angle)*(dyn+v); y2=cy+math.sin(angle)*(dyn+v)
        pygame.draw.line(screen,(120,180,255),(x1,y1),(x2,y2),2)
    bw = W / BARS           # float — бары точно заполняют всю ширину
    for i in range(BARS):
        h = int(heights[i])
        x = int(i * bw)
        w = int((i + 1) * bw) - x
        pygame.draw.rect(screen, (80,140,255), (x, H-h, max(1, w-1), h))


# ===== SPOTIFY =====
spotify = SpotifyClient()
info    = {"name":"","artist":"","progress":0,"duration":0,"cover":None}

# ===== MAIN LOOP =====
running = True
while running:
    mx,my = pygame.mouse.get_pos()
    W,H   = screen.get_size()
    cx,cy = W//2, H//2

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        # --- альбомная карусель (приоритет если открыта) ---
        if not settings_ui.open:
            uri = album_carousel.handle(event, W)
            if uri:
                spotify.play_album(uri)
                album_carousel.toggle()   # закрываем после выбора
                force_refresh_later()
                continue

        # --- глобальные хоткеи ---
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_h:
                settings["hide_ui"] = not settings["hide_ui"]
            elif event.key == pygame.K_ESCAPE:
                if settings_ui.open:
                    settings_ui.open=False; save_settings(settings)
                elif album_carousel.open:
                    album_carousel.toggle()
            elif event.key == pygame.K_F11:
                toggle_fullscreen()   # fix #9.6
            elif event.key == pygame.K_SPACE: on_play_pause()
            elif event.key == pygame.K_RIGHT: on_next()
            elif event.key == pygame.K_LEFT:  on_prev()
            continue

        if settings_ui.handle(event):
            continue

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ex,ey   = event.pos
            sf_ev   = radius / 120.0
            btn_y   = cy + radius + int(108 * sf_ev)   # масштаб (fix #5)
            btn_gap = int(55 * max(1.0, sf_ev))
            btn_sz  = int(9  * max(1.0, sf_ev))
            gear_x  = W-26; gear_y=22
            fs_x    = W-52; fs_y=22
            chev_x  = cx
            chev_y  = 14

            if math.hypot(ex-gear_x,ey-gear_y)<18:
                settings_ui.toggle()
            elif math.hypot(ex-fs_x,ey-fs_y)<14:
                toggle_fullscreen()   # fix #9.6
            elif abs(ex-chev_x)<34 and abs(ey-chev_y)<18:
                album_carousel.toggle()
            elif not settings["hide_ui"]:
                if math.hypot(ex-(cx-btn_gap),ey-btn_y)<btn_sz*2.5:   on_prev()
                elif math.hypot(ex-cx,ey-btn_y)<btn_sz*2.5:            on_play_pause()
                elif math.hypot(ex-(cx+btn_gap),ey-btn_y)<btn_sz*2.5: on_next()
                elif _prog_bar_rect and _prog_bar_rect.inflate(0,20).collidepoint(ex,ey):
                    on_seek(ex)

        elif event.type == pygame.VIDEORESIZE:
            if not is_fullscreen:
                settings["win_w"], settings["win_h"] = event.w, event.h

    # Пересчёт радиуса при смене размера окна (fix #5)
    _new_r = calc_radius(W, H)
    if _new_r != radius:
        radius       = _new_r
        cover_radius = radius - 3
        if cached_raw_cover:
            cached_cover = make_circle_cover(cached_raw_cover, cover_radius)
            cached_bloom = create_bloom(radius, main_color)
            cached_bg    = rebuild_bg(cached_raw_cover)

    # refresh
    if pending_refresh and time.time()>=refresh_after:
        pending_refresh=False
        info=spotify.get_info(); update_track(info,force=True)
    else:
        info=spotify.get_info(); update_track(info)

    # обновляем альбомы если загрузились
    albums = spotify.get_albums()
    if albums:
        album_carousel.set_albums(albums)

    values = process_audio()
    draw(info, values)
    _last_draw_state["info"]   = info     # для мгновенной перерисовки (fix #9.6)
    _last_draw_state["values"] = values

    pygame.display.flip()
    pygame.event.pump()
    clock.tick(120)

save_settings(settings)
if stream_ref[0]:
    try: stream_ref[0].stop()
    except: pass
pygame.quit()