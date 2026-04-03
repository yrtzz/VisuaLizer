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

try:
    from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume
    PYCAW_OK = True
except ImportError:
    PYCAW_OK = False
    print("Микшер недоступен. Установи: pip install pycaw comtypes")


# ===== CONFIG =====
BARS       = 64
SAMPLERATE = 44100
BLOCKSIZE  = 1024
ROW_HEIGHT = 30
FORCE_REFRESH_DELAY = 0.4

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
KEYEVENTF_KEYUP     = 0x0002

SETTINGS_FILE = os.path.join(os.path.dirname(__file__), "settings.json")
DEFAULT_SETTINGS = {
    "device":       18,
    "sensitivity":  1.0,
    "always_on_top": False,
    "hide_ui":      False,
    "hide_silent":  True,
    "win_w":        900,
    "win_h":        600,
}


# ===== SETTINGS LOAD/SAVE =====
def load_settings():
    try:
        with open(SETTINGS_FILE) as f:
            return {**DEFAULT_SETTINGS, **json.load(f)}
    except:
        return dict(DEFAULT_SETTINGS)

def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump({k: v for k, v in s.items() if k != "device" or True}, f, indent=2)
    except:
        pass

settings = load_settings()


# ===== INIT =====
pygame.init()
screen = pygame.display.set_mode(
    (settings["win_w"], settings["win_h"]),
    pygame.RESIZABLE | pygame.DOUBLEBUF
)
pygame.display.set_caption("Music Visualizer")
clock = pygame.time.Clock()

font        = pygame.font.SysFont("Segoe UI", 18)
font_small  = pygame.font.SysFont("Segoe UI", 14)
font_tiny   = pygame.font.SysFont("Segoe UI", 12)
font_bold   = pygame.font.SysFont("Segoe UI", 16, bold=True)


# ===== SYSTEM =====
def set_always_on_top(enable: bool):
    try:
        hwnd = pygame.display.get_wm_info()["window"]
        flag = -1 if enable else -2   # HWND_TOPMOST / HWND_NOTOPMOST
        ctypes.windll.user32.SetWindowPos(hwnd, flag, 0, 0, 0, 0, 0x0001 | 0x0002)
    except:
        pass

def send_media_key(vk):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)

# применяем always_on_top из сохранённых настроек
if settings["always_on_top"]:
    set_always_on_top(True)


# ===== AUDIO =====
audio_buffer = np.zeros(BLOCKSIZE)

def audio_callback(indata, frames, time_, status):
    global audio_buffer
    audio_buffer = indata[:, 0] if indata.ndim > 1 else indata

def make_stream(device_id):
    try:
        dev = sd.query_devices(device_id)
        ch  = min(2, int(dev["max_input_channels"]))
        s   = sd.InputStream(device=device_id, channels=ch,
                             samplerate=SAMPLERATE, blocksize=BLOCKSIZE,
                             callback=audio_callback)
        s.start()
        return s
    except Exception as e:
        print("Stream error:", e)
        # fallback — дефолтное устройство
        s = sd.InputStream(channels=1, samplerate=SAMPLERATE,
                           blocksize=BLOCKSIZE, callback=audio_callback)
        s.start()
        return s

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
    center = size // 2
    pygame.draw.circle(surf, (*color, 255), (center, center), radius)
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
    g = pygame.Surface((r_out * 2 + 4, r_out * 2 + 4), pygame.SRCALPHA)
    gc = r_out + 2
    n  = teeth * 4
    pts = []
    for i in range(n):
        angle = (i / n) * 2 * math.pi - math.pi / 2
        r = r_out if (i % 4 < 2) else r_out - 4
        pts.append((gc + math.cos(angle) * r, gc + math.sin(angle) * r))
    pygame.draw.polygon(g, (*color, alpha), pts)
    pygame.draw.circle(g, (0, 0, 0, 0), (gc, gc), r_in)
    surf.blit(g, (cx - gc, cy - gc))

def _draw_fullscreen_icon(surf, cx, cy, r, color, alpha=200):
    """Рисует иконку ⛶ (четыре угловые стрелки) для кнопки полного экрана."""
    g = pygame.Surface((r*2+4, r*2+4), pygame.SRCALPHA)
    c = r + 2
    col = (*color, alpha)
    # четыре угловых уголка
    for sx, sy in [(-1,-1),(1,-1),(1,1),(-1,1)]:
        ax, ay = c + sx*r, c + sy*r
        bx, by = c + sx*(r-4), c + sy*r
        dx, dy = c + sx*r,     c + sy*(r-4)
        pygame.draw.line(g, col, (ax, ay), (bx, by), 2)
        pygame.draw.line(g, col, (ax, ay), (dx, dy), 2)
    surf.blit(g, (cx - c, cy - c))


# ===== BUTTON ICONS =====
def draw_icon_prev(surf, cx, cy, size, color):
    for off in (size // 3, -size // 3 + 2):
        pts = [(cx+off, cy), (cx+off+size, cy-size), (cx+off+size, cy+size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_next(surf, cx, cy, size, color):
    for off in (-size // 3, size // 3 - 2):
        pts = [(cx+off, cy), (cx+off-size, cy-size), (cx+off-size, cy+size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_play(surf, cx, cy, size, color):
    pygame.draw.polygon(surf, color,
        [(cx+size, cy), (cx-size//2, cy-size), (cx-size//2, cy+size)])

def draw_icon_pause(surf, cx, cy, size, color):
    bw = max(3, size // 3)
    g  = size // 2
    pygame.draw.rect(surf, color, (cx-g-bw, cy-size, bw, size*2))
    pygame.draw.rect(surf, color, (cx+g,    cy-size, bw, size*2))


# ===== SETTINGS UI =====
class SettingsUI:
    PW, PH = 580, 440
    TAB_AUDIO, TAB_MIXER = 0, 1

    def __init__(self):
        self.open         = False
        self.tab          = self.TAB_AUDIO
        self.devices      = []
        self.dev_scroll   = 0
        self.sens_drag    = False
        self.mix_sessions = []
        self.mix_scroll   = 0
        self.mix_drag     = None   # (abs_idx, slider_x, slider_w)
        self.mix_last_ref = 0
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
                    result.append({
                        "name":    s.Process.name().replace(".exe", ""),
                        "volume":  ctl.GetMasterVolume(),
                        "_ctl":    ctl,
                    })
            self.mix_sessions = result
        except Exception as e:
            print("Mixer refresh:", e)

    def _visible(self):
        if settings.get("hide_silent"):
            return [s for s in self.mix_sessions if s["volume"] > 0.005]
        return self.mix_sessions

    def toggle(self):
        self.open = not self.open
        if self.open:
            self._scan_devices()

    def _panel(self):
        W, H = screen.get_size()
        return pygame.Rect((W - self.PW) // 2, (H - self.PH) // 2, self.PW, self.PH)

    # ---- event handling ----
    def handle(self, event):
        if not self.open:
            return False
        p  = self._panel()
        mx, my = pygame.mouse.get_pos()

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ex, ey = event.pos
            # close X
            if pygame.Rect(p.right-30, p.top+8, 22, 22).collidepoint(ex, ey):
                self.open = False
                save_settings(settings)
                return True
            # outside → close
            if not p.collidepoint(ex, ey):
                self.open = False
                save_settings(settings)
                return True
            # tabs
            tab_y = p.top + 40
            tw    = self.PW // 2
            if pygame.Rect(p.x,    tab_y, tw, 28).collidepoint(ex, ey): self.tab = self.TAB_AUDIO
            if pygame.Rect(p.x+tw, tab_y, tw, 28).collidepoint(ex, ey): self.tab = self.TAB_MIXER
            # content
            cy = tab_y + 38
            if self.tab == self.TAB_AUDIO:
                self._click_audio(ex, ey, p, cy)
            else:
                self._click_mixer(ex, ey, p, cy)
            return True

        elif event.type == pygame.MOUSEBUTTONUP:
            self.sens_drag = False
            self.mix_drag  = None

        elif event.type == pygame.MOUSEMOTION:
            if self.sens_drag:
                self._drag_sens(mx, self._panel())
            if self.mix_drag is not None:
                self._drag_mix(mx)

        elif event.type == pygame.MOUSEWHEEL:
            if p.collidepoint(*pygame.mouse.get_pos()):
                if self.tab == self.TAB_AUDIO:
                    self.dev_scroll = max(0, min(len(self.devices)-4, self.dev_scroll - event.y))
                else:
                    vis = self._visible()
                    self.mix_scroll = max(0, min(max(0, len(vis)-7), self.mix_scroll - event.y))
                return True

        return self.open   # поглощаем все события когда открыты

    def _click_audio(self, ex, ey, p, cy):
        cx = p.x + 20
        # device rows
        for i, (did, dname) in enumerate(self.devices[self.dev_scroll:self.dev_scroll+4]):
            row = pygame.Rect(cx, cy+22+i*26, self.PW-40, 22)
            if row.collidepoint(ex, ey):
                settings["device"] = did
                stream_ref[0].stop()
                stream_ref[0].close()
                stream_ref[0] = make_stream(did)
                return
        # sensitivity slider
        sy = cy + 140
        sx = cx + 110; sw = self.PW - 150
        if pygame.Rect(sx-5, sy, sw+10, 20).collidepoint(ex, ey):
            self.sens_drag = True
            self._drag_sens(ex, self._panel())
            return
        # toggles
        ty0 = sy + 42
        for i, key in enumerate(["always_on_top", "hide_ui", "hide_silent"]):
            tog = pygame.Rect(p.x+self.PW-62, ty0+i*36, 44, 22)
            if tog.collidepoint(ex, ey):
                settings[key] = not settings[key]
                if key == "always_on_top":
                    set_always_on_top(settings["always_on_top"])
                return

    def _drag_sens(self, mx, p):
        cx = p.x + 20
        sx = cx + 110; sw = self.PW - 150
        frac = max(0.0, min(1.0, (mx - sx) / sw))
        settings["sensitivity"] = round(0.3 + frac * 2.7, 2)

    def _click_mixer(self, ex, ey, p, cy):
        vis     = self._visible()
        sx      = p.x + 150; sw = self.PW - 170
        row_h   = 40
        for i, s in enumerate(vis[self.mix_scroll:self.mix_scroll+7]):
            abs_i = i + self.mix_scroll
            sr    = pygame.Rect(sx, cy+i*row_h+8, sw, 18)
            if sr.inflate(0, 8).collidepoint(ex, ey):
                frac = max(0.0, min(1.0, (ex - sx) / sw))
                try:
                    s["_ctl"].SetMasterVolume(frac, None)
                    s["volume"] = frac
                except: pass
                self.mix_drag = (abs_i, sx, sw)
                return

    def _drag_mix(self, mx):
        if self.mix_drag is None: return
        abs_i, sx, sw = self.mix_drag
        vis = self._visible()
        if abs_i < len(vis):
            frac = max(0.0, min(1.0, (mx - sx) / sw))
            try:
                vis[abs_i]["_ctl"].SetMasterVolume(frac, None)
                vis[abs_i]["volume"] = frac
            except: pass

    # ---- drawing ----
    def draw(self):
        if not self.open: return
        if self.tab == self.TAB_MIXER:
            self._refresh_mixer()

        W, H = screen.get_size()
        p    = self._panel()
        mx, my = pygame.mouse.get_pos()

        # dim
        dim = pygame.Surface((W, H), pygame.SRCALPHA)
        dim.fill((0, 0, 0, 150))
        screen.blit(dim, (0, 0))

        # panel
        ps = pygame.Surface((self.PW, self.PH), pygame.SRCALPHA)
        ps.fill((16, 16, 26, 235))
        screen.blit(ps, (p.x, p.y))
        pygame.draw.rect(screen, (55, 55, 80), p, 1, border_radius=10)

        # title
        screen.blit(font_bold.render("Settings", True, (210, 210, 230)), (p.x+20, p.y+12))

        # close ×
        cr    = pygame.Rect(p.right-30, p.top+8, 22, 22)
        cc    = (220, 70, 70) if cr.collidepoint(mx, my) else (130, 130, 150)
        pygame.draw.line(screen, cc, (cr.left+5, cr.top+5),  (cr.right-5, cr.bottom-5), 2)
        pygame.draw.line(screen, cc, (cr.right-5, cr.top+5), (cr.left+5,  cr.bottom-5), 2)

        # tabs
        tab_y = p.top + 40; tw = self.PW // 2
        for i, lbl in enumerate(["Audio & Display", "Volume Mixer"]):
            tx  = p.x + i * tw
            act = (i == self.tab)
            ts  = pygame.Surface((tw, 28), pygame.SRCALPHA)
            ts.fill((38, 38, 60, 200) if act else (22, 22, 35, 120))
            screen.blit(ts, (tx, tab_y))
            tc = font_small.render(lbl, True, (255,255,255) if act else (110,110,140))
            screen.blit(tc, (tx + (tw - tc.get_width())//2, tab_y+6))
            if act:
                pygame.draw.line(screen, (80,140,255), (tx,tab_y+27), (tx+tw,tab_y+27), 2)

        pygame.draw.line(screen, (50,50,75), (p.x, tab_y+28), (p.right, tab_y+28))

        cy = tab_y + 38
        if self.tab == self.TAB_AUDIO:
            self._draw_audio(p, cy, mx, my)
        else:
            self._draw_mixer(p, cy, mx, my)

    def _draw_audio(self, p, cy, mx, my):
        cx = p.x + 20

        # --- Input Device ---
        self._label(cx, cy, "INPUT DEVICE")
        list_y  = cy + 20
        visible = 4
        for i, (did, dname) in enumerate(self.devices[self.dev_scroll:self.dev_scroll+visible]):
            row = pygame.Rect(cx, list_y+i*26, self.PW-44, 22)
            sel = (did == settings["device"])
            hov = row.collidepoint(mx, my)
            rs  = pygame.Surface((row.w, row.h), pygame.SRCALPHA)
            rs.fill((50,100,190,160) if sel else ((45,45,68,90) if hov else (0,0,0,0)))
            screen.blit(rs, (row.x, row.y))
            if sel:
                pygame.draw.rect(screen, (80,140,255), row, 1, border_radius=3)
            name = dname[:48]+"…" if len(dname)>48 else dname
            screen.blit(font_tiny.render(name, True, (255,255,255) if sel else (170,170,200)),
                        (row.x+6, row.y+4))
        # scrollbar
        tot = len(self.devices)
        if tot > visible:
            bh = max(18, visible/tot*(visible*26))
            by = list_y + self.dev_scroll/(tot-visible)*(visible*26-bh)
            pygame.draw.rect(screen,(55,55,80),(p.right-12, list_y, 4, visible*26),border_radius=2)
            pygame.draw.rect(screen,(120,120,180),(p.right-12, list_y+by, 4, bh),border_radius=2)

        # --- Sensitivity ---
        sy = cy + 130
        self._label(cx, sy, "SENSITIVITY")
        ssx = cx+110; ssw = self.PW-155
        frac = (settings["sensitivity"]-0.3)/2.7
        self._slider(ssx, sy+14, ssw, frac, mx, my, f"{settings['sensitivity']:.1f}×")

        # --- Toggles ---
        ty0 = sy + 46
        for i, (key, lbl) in enumerate([
            ("always_on_top", "Always on Top"),
            ("hide_ui",       "Hide UI  (H)"),
            ("hide_silent",   "Hide silent in Mixer"),
        ]):
            ty = ty0 + i*36
            screen.blit(font_small.render(lbl, True, (190,190,215)), (cx, ty+3))
            self._toggle(p.x+self.PW-62, ty, settings[key])

    def _draw_mixer(self, p, cy, mx, my):
        if not PYCAW_OK:
            screen.blit(font_small.render("Установи: pip install pycaw comtypes",
                                          True, (200,90,90)), (p.x+20, cy+20))
            return
        vis   = self._visible()
        sx    = p.x+150; sw = self.PW-170; row_h = 40
        if not vis:
            screen.blit(font_small.render("Нет активных сессий", True, (130,130,155)),
                        (p.x+20, cy+20))
            return
        for i, s in enumerate(vis[self.mix_scroll:self.mix_scroll+7]):
            ry = cy + i*row_h
            # name
            screen.blit(font_small.render(s["name"][:20], True, (195,195,220)),
                        (p.x+20, ry+12))
            # slider
            self._slider(sx, ry+18, sw, s["volume"], mx, my, f"{int(s['volume']*100)}%")
        # scrollbar
        tot = len(vis)
        if tot > 7:
            bh = max(20, 7/tot*(7*row_h))
            by = self.mix_scroll/(tot-7)*(7*row_h-bh)
            pygame.draw.rect(screen,(55,55,80),(p.right-12,cy,4,7*row_h),border_radius=2)
            pygame.draw.rect(screen,(120,120,180),(p.right-12,cy+by,4,bh),border_radius=2)

    def _slider(self, x, y, w, frac, mx, my, label=""):
        pygame.draw.rect(screen, (45,45,68), (x, y-2, w, 4), border_radius=2)
        pygame.draw.rect(screen, (80,140,255), (x, y-2, int(w*frac), 4), border_radius=2)
        kx  = x + int(w * frac)
        hov = abs(mx-kx) < 14 and abs(my-y) < 12
        pygame.draw.circle(screen, (255,255,255), (kx, y), 7 if hov else 5)
        if label:
            screen.blit(font_tiny.render(label, True, (150,150,175)), (x+w+8, y-7))

    def _toggle(self, x, y, val):
        pygame.draw.rect(screen, (55,135,75) if val else (55,55,80), (x,y,44,22), border_radius=11)
        pygame.draw.circle(screen, (235,235,235), (x+(32 if val else 12), y+11), 8)

    def _label(self, x, y, text):
        screen.blit(font_tiny.render(text, True, (90,90,130)), (x, y))


settings_ui = SettingsUI()


# ===== VISUALIZER STATE =====
radius       = 120
cover_radius = radius - 3

heights       = np.zeros(BARS)
rotation      = 0
pulse_value   = 1.0
volume_smooth = 0.1

cached_cover      = None
cached_track      = None
cached_bg         = None
cached_bloom      = None
cached_raw_cover  = None
main_color        = (255, 255, 255)

is_transitioning    = False
transition_progress = 1.0
old_cover           = None

list_offset       = 0.0
is_list_animating = False

pending_refresh = False
refresh_after   = 0.0
is_playing      = True
_prog_bar_rect  = None
is_fullscreen   = False


# ===== AUDIO PROCESS =====
def process_audio():
    global heights, pulse_value, volume_smooth
    fft       = np.fft.rfft(audio_buffer)
    magnitude = np.abs(fft)
    bins      = np.array_split(magnitude, BARS // 2)
    values    = np.array([np.mean(b) for b in bins])
    values    = np.concatenate((values, values[::-1]))
    values    = np.log1p(values) * 200 * settings["sensitivity"]

    cur            = np.mean(values)
    volume_smooth  = volume_smooth * 0.9 + cur * 0.1
    if volume_smooth > 0:
        values  = values / volume_smooth * 10
        heights = heights * 0.7 + values * 0.3

    bass        = np.mean(values[:5]) * 0.004
    pulse_value = pulse_value * 0.8 + (1 + min(bass, 0.25)) * 0.2
    return values


# ===== TRACK UPDATE =====
def update_track(info, force=False):
    global cached_track, cached_cover, cached_bg, cached_bloom, main_color
    global is_transitioning, transition_progress, old_cover
    global is_list_animating, list_offset, cached_raw_cover

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
    send_media_key(VK_MEDIA_PREV_TRACK)
    force_refresh_later()

def on_play_pause():
    send_media_key(VK_MEDIA_PLAY_PAUSE)

def on_next():
    send_media_key(VK_MEDIA_NEXT_TRACK)
    force_refresh_later()

def on_seek(click_x):
    if _prog_bar_rect is None: return
    frac = max(0.0, min(1.0, (click_x - _prog_bar_rect.x) / _prog_bar_rect.width))
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
    dyn  = radius * pulse_value

    name        = info["name"]
    artist      = info["artist"]
    progress    = info["progress"]
    duration    = info["duration"]
    next_tracks = info.get("next", [])
    is_playing  = info.get("is_playing", is_playing)

    # background
    if cached_bg:
        bw, bh = cached_bg.get_size()
        if bw != W or bh != H:
            if cached_raw_cover:
                cached_bg = rebuild_bg(cached_raw_cover)
        screen.blit(cached_bg, (0, 0))
    else:
        screen.fill((10, 10, 20))

    # cover fade — всегда рисуем (даже в hide_ui режиме)
    if is_transitioning and old_cover is not None:
        transition_progress += 0.05
        clamp = min(transition_progress, 1.0)
        size  = int(cover_radius * 2 * pulse_value)
        old   = pygame.transform.smoothscale(old_cover.copy(),    (size, size))
        new   = pygame.transform.smoothscale(cached_cover.copy(), (size, size))
        old.set_alpha(255 - int(255 * clamp))
        new.set_alpha(int(255 * clamp))
        screen.blit(old, (cx - size//2, cy - size//2))
        screen.blit(new, (cx - size//2, cy - size//2))
        if transition_progress >= 1:
            transition_progress = 1.0
            is_transitioning    = False
    elif cached_cover:
        size = int(cover_radius * 2 * pulse_value)
        screen.blit(pygame.transform.smoothscale(cached_cover, (size, size)),
                    (cx - size//2, cy - size//2))

    # визуализатор — тоже всегда
    _draw_visualizer(cx, cy, dyn)

    if settings["hide_ui"]:
        return  # только фон + обложка + кольца, UI скрыт

    # roulette list
    if is_list_animating:
        list_offset *= 0.82
        if list_offset < 0.5:
            list_offset       = 0.0
            is_list_animating = False

    track_list  = [("cur", (name, artist))]
    track_list += [("nxt", t) for t in next_tracks[:2]]
    base_y   = 20
    old_clip = screen.get_clip()
    screen.set_clip(pygame.Rect(0, 0, W//2, base_y + ROW_HEIGHT * 4))
    for i, (typ, (t, a)) in enumerate(track_list):
        y    = base_y + i * ROW_HEIGHT + int(list_offset)
        line = f"{t} — {a}"
        if typ == "cur":
            txt = font.render(line, True, (255, 255, 255))
        else:
            fade = max(0, 130 - i * 30)
            txt  = font_small.render(line, True, (200, 200, 200))
            txt.set_alpha(fade)
        screen.blit(txt, (20, y))
    screen.set_clip(old_clip)

    mx, my = pygame.mouse.get_pos()

    # progress bar
    if duration > 0:
        bar_w = radius * 2 + 40
        bx    = cx - bar_w // 2
        by    = cy + radius + 60
        frac  = min(1.0, progress / duration)

        _prog_bar_rect = pygame.Rect(bx, by, bar_w, 10)
        hover   = _prog_bar_rect.inflate(0, 16).collidepoint(mx, my)
        bar_h   = 8 if hover else 5
        byd     = by + (10 - bar_h) // 2

        pygame.draw.rect(screen, (60,60,60),    (bx, byd, bar_w, bar_h), border_radius=4)
        pygame.draw.rect(screen, (255,255,255), (bx, byd, int(bar_w*frac), bar_h), border_radius=4)
        if hover:
            pygame.draw.circle(screen, (255,255,255), (bx+int(bar_w*frac), byd+bar_h//2), 6)

        screen.blit(font_small.render(time.strftime('%M:%S', time.gmtime(progress)),
                                      True, (170,170,170)), (bx, by+14))
        screen.blit(font_small.render(time.strftime('%M:%S', time.gmtime(duration)),
                                      True, (170,170,170)), (bx+bar_w-38, by+14))

    # control buttons
    btn_y  = cy + radius + 108
    btn_sz = 9; btn_gap = 55
    bsurf  = pygame.Surface((W, H), pygame.SRCALPHA)
    for key, bxb in [("prev", cx-btn_gap), ("play", cx), ("next", cx+btn_gap)]:
        hov   = math.hypot(mx-bxb, my-btn_y) < btn_sz*2.5
        col   = (255,255,255) if hov else (180,180,180)
        alph  = 255 if hov else 190
        sz    = int(btn_sz * (1.2 if hov else 1.0))
        rgba  = (*col, alph)
        if key == "prev":   draw_icon_prev(bsurf, bxb, btn_y, sz, rgba)
        elif key == "next": draw_icon_next(bsurf, bxb, btn_y, sz, rgba)
        else:
            (draw_icon_pause if is_playing else draw_icon_play)(bsurf, bxb, btn_y, sz, rgba)
    screen.blit(bsurf, (0, 0))

    # fullscreen button (top-right, левее шестерёнки)
    fs_x = W - 52; fs_y = 22
    fs_hov   = math.hypot(mx-fs_x, my-fs_y) < 14
    fs_alpha = 220 if fs_hov else 100
    _draw_fullscreen_icon(screen, fs_x, fs_y, 9, (200, 200, 230), fs_alpha)

    # gear icon (top-right)
    gear_x = W - 26; gear_y = 22
    gear_hov = math.hypot(mx-gear_x, my-gear_y) < 18
    gear_alpha = 220 if gear_hov else 100
    draw_gear(screen, gear_x, gear_y, 12, 5, 7, (200,200,230), gear_alpha)

    # settings overlay (рисуется поверх всего)
    settings_ui.draw()


def _draw_visualizer(cx, cy, dyn):
    # bloom
    if cached_bloom:
        size = int(radius * 4 * pulse_value)
        screen.blit(pygame.transform.smoothscale(cached_bloom, (size, size)),
                    (cx-size//2, cy-size//2), special_flags=pygame.BLEND_ADD)
    # glow
    W, H = screen.get_size()
    glow = pygame.Surface((W, H), pygame.SRCALPHA)
    for i in range(20):
        pygame.draw.circle(glow, (*main_color, max(5, 100-i*7)),
                           (cx, cy), int(dyn + i*4))
    screen.blit(glow, (0, 0))
    # circle lines
    global rotation
    quarter       = heights[:BARS//4]
    circle_values = np.tile(quarter, 4)
    rotation     += 0.01
    for i in range(BARS):
        angle = (i/BARS)*2*math.pi + rotation
        v     = circle_values[i]
        x1 = cx + math.cos(angle)*dyn;   y1 = cy + math.sin(angle)*dyn
        x2 = cx + math.cos(angle)*(dyn+v); y2 = cy + math.sin(angle)*(dyn+v)
        pygame.draw.line(screen, (120,180,255), (x1,y1), (x2,y2), 2)
    # bars
    bw = W // BARS
    for i in range(BARS):
        h = int(heights[i])
        pygame.draw.rect(screen, (80,140,255), (i*bw, H-h, bw-2, h))


# ===== SPOTIFY =====
spotify = SpotifyClient()
info    = {"name":"","artist":"","progress":0,"duration":0,"cover":None}


# ===== MAIN LOOP =====
running = True
while running:

    mx, my = pygame.mouse.get_pos()
    W, H   = screen.get_size()
    cx, cy = W//2, H//2

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        # --- глобальные хоткеи — обрабатываем ДО settings_ui ---
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_h:
                settings["hide_ui"] = not settings["hide_ui"]
            elif event.key == pygame.K_ESCAPE:
                if settings_ui.open:
                    settings_ui.open = False
                    save_settings(settings)
            elif event.key == pygame.K_F11:
                is_fullscreen = not is_fullscreen
                if is_fullscreen:
                    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.DOUBLEBUF)
                else:
                    screen = pygame.display.set_mode(
                        (settings["win_w"], settings["win_h"]),
                        pygame.RESIZABLE | pygame.DOUBLEBUF)
                if settings["always_on_top"]:
                    set_always_on_top(True)
            elif event.key == pygame.K_SPACE:
                on_play_pause()
            elif event.key == pygame.K_RIGHT:
                on_next()
            elif event.key == pygame.K_LEFT:
                on_prev()
            continue  # остальные KEYDOWN до settings_ui не доходят

        # остальные события — сначала настройки
        if settings_ui.handle(event):
            continue

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            ex, ey  = event.pos
            btn_y   = cy + radius + 108
            btn_gap = 55; btn_sz = 9
            gear_x  = W - 26; gear_y = 22
            fs_x    = W - 52; fs_y   = 22

            if math.hypot(ex-fs_x, ey-fs_y) < 14:
                # кнопка полного экрана
                is_fullscreen = not is_fullscreen
                if is_fullscreen:
                    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.DOUBLEBUF)
                else:
                    screen = pygame.display.set_mode(
                        (settings["win_w"], settings["win_h"]),
                        pygame.RESIZABLE | pygame.DOUBLEBUF)
                if settings["always_on_top"]:
                    set_always_on_top(True)
            elif math.hypot(ex-gear_x, ey-gear_y) < 18:
                settings_ui.toggle()
            elif not settings["hide_ui"]:
                if math.hypot(ex-(cx-btn_gap), ey-btn_y) < btn_sz*2.5:
                    on_prev()
                elif math.hypot(ex-cx, ey-btn_y) < btn_sz*2.5:
                    on_play_pause()
                elif math.hypot(ex-(cx+btn_gap), ey-btn_y) < btn_sz*2.5:
                    on_next()
                elif _prog_bar_rect and _prog_bar_rect.inflate(0,20).collidepoint(ex,ey):
                    on_seek(ex)

        elif event.type == pygame.VIDEORESIZE:
            if not is_fullscreen:
                settings["win_w"], settings["win_h"] = event.w, event.h

    # refresh
    if pending_refresh and time.time() >= refresh_after:
        pending_refresh = False
        info = spotify.get_info()
        update_track(info, force=True)
    else:
        info = spotify.get_info()
        update_track(info)

    values = process_audio()
    draw(info, values)

    pygame.display.flip()
    pygame.event.pump()
    clock.tick(120)

save_settings(settings)
stream_ref[0].stop()
pygame.quit()