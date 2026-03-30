import pygame
import numpy as np
import sounddevice as sd
import math
import time
import cv2
import ctypes
import threading

from spotify_client import SpotifyClient


# ===== CONFIG =====
WIDTH, HEIGHT = 900, 600
BARS = 64

SAMPLERATE = 44100
BLOCKSIZE = 1024

ROW_HEIGHT = 30
LIST_MAX_VISIBLE = 3

FORCE_REFRESH_DELAY = 0.4

VK_MEDIA_PLAY_PAUSE = 0xB3
VK_MEDIA_NEXT_TRACK = 0xB0
VK_MEDIA_PREV_TRACK = 0xB1
KEYEVENTF_KEYUP     = 0x0002


# ===== INIT =====
pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE | pygame.DOUBLEBUF)
pygame.display.set_caption("Music Visualizer")

clock = pygame.time.Clock()

font       = pygame.font.SysFont("Segoe UI", 18)
font_small = pygame.font.SysFont("Segoe UI", 14)


# ===== AUDIO =====
audio_buffer = np.zeros(BLOCKSIZE)

def audio_callback(indata, frames, time_, status):
    global audio_buffer
    audio_buffer = indata[:, 0] if indata.ndim > 1 else indata


# ===== UTILS =====
def send_media_key(vk):
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def blur_surface(surface, ksize=35):
    arr = pygame.surfarray.array3d(surface)
    arr = np.transpose(arr, (1, 0, 2))
    arr = cv2.GaussianBlur(arr, (ksize, ksize), 0)
    arr = np.transpose(arr, (1, 0, 2))
    return pygame.surfarray.make_surface(arr)


def create_bloom(radius, color):
    size = radius * 4
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    center = size // 2
    pygame.draw.circle(surf, (*color, 255), (center, center), radius)
    arr = pygame.surfarray.array3d(surf)
    arr = np.transpose(arr, (1, 0, 2))
    arr = cv2.GaussianBlur(arr, (101, 101), 0)
    arr = np.transpose(arr, (1, 0, 2))
    return pygame.surfarray.make_surface(arr)


def rebuild_bg(cover_surface):
    w, h = screen.get_size()
    bg = pygame.transform.smoothscale(cover_surface, (w, h))
    bg = blur_surface(bg, 35)
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


def build_assets_from_raw(raw_cover):
    """Строит все тяжёлые ассеты из сырой обложки. Вызывается из фонового потока."""
    small = pygame.transform.smoothscale(raw_cover, (1, 1))
    color = small.get_at((0, 0))[:3]
    return {
        "raw":   raw_cover,
        "bg":    rebuild_bg(raw_cover),
        "cover": make_circle_cover(raw_cover, cover_radius),
        "bloom": create_bloom(radius, color),
        "color": color,
    }


# ===== BUTTON ICONS =====
def draw_icon_prev(surf, cx, cy, size, color):
    for offset in (size // 3, -size // 3 + 2):
        pts = [(cx + offset, cy), (cx + offset + size, cy - size), (cx + offset + size, cy + size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_next(surf, cx, cy, size, color):
    for offset in (-size // 3, size // 3 - 2):
        pts = [(cx + offset, cy), (cx + offset - size, cy - size), (cx + offset - size, cy + size)]
        pygame.draw.polygon(surf, color, pts)

def draw_icon_play(surf, cx, cy, size, color):
    pts = [(cx + size, cy), (cx - size // 2, cy - size), (cx - size // 2, cy + size)]
    pygame.draw.polygon(surf, color, pts)

def draw_icon_pause(surf, cx, cy, size, color):
    bar_w = max(3, size // 3)
    gap   = size // 2
    pygame.draw.rect(surf, color, (cx - gap - bar_w, cy - size, bar_w, size * 2))
    pygame.draw.rect(surf, color, (cx + gap,          cy - size, bar_w, size * 2))


# ===== STREAM =====
stream = sd.InputStream(
    device=18,
    channels=2,
    samplerate=SAMPLERATE,
    blocksize=BLOCKSIZE,
    callback=audio_callback
)
stream.start()


# ===== STATE =====
spotify = SpotifyClient()

heights       = np.zeros(BARS)
center_x, center_y = WIDTH // 2, HEIGHT // 2
radius        = 120
cover_radius  = radius - 3

rotation      = 0
pulse_value   = 1.0
volume_smooth = 0.1

_render_lock  = threading.Lock()
cached_track  = None
cached_raw    = None
cached_bg     = None
cached_cover  = None
cached_bloom  = None
main_color    = (255, 255, 255)

is_transitioning    = False
transition_progress = 1.0
old_cover           = None

list_offset       = 0.0
is_list_animating = False

pending_refresh = False
refresh_after   = 0.0

is_playing     = True
_prog_bar_rect = None

# ===== ASSET CACHE =====
# имя_трека -> {raw, bg, cover, bloom, color}
_asset_cache: dict      = {}
_asset_cache_lock       = threading.Lock()
_building_now: set      = set()   # имена треков, сборка которых сейчас идёт


# ===== SEEK STATE =====
seeking   = False
seek_frac = 0.0


# ===== AUDIO PROCESS =====
def process_audio():
    global heights, pulse_value, volume_smooth

    fft       = np.fft.rfft(audio_buffer)
    magnitude = np.abs(fft)
    bins      = np.array_split(magnitude, BARS // 2)
    values    = np.array([np.mean(b) for b in bins])
    values    = np.concatenate((values, values[::-1]))
    values    = np.log1p(values) * 200

    current_volume = np.mean(values)
    volume_smooth  = volume_smooth * 0.9 + current_volume * 0.1

    if volume_smooth > 0:
        values  = values / volume_smooth * 10
        heights = heights * 0.7 + values * 0.3

    bass        = np.mean(values[:5]) * 0.004
    pulse_value = pulse_value * 0.8 + (1 + min(bass, 0.25)) * 0.2

    return values


# ===== ASSET BUILDING =====

def _build_and_cache(name: str, raw_cover: pygame.Surface):
    try:
        assets = build_assets_from_raw(raw_cover)
        with _asset_cache_lock:
            _asset_cache[name] = assets
    except Exception as e:
        print(f"Asset build error '{name}':", e)
    finally:
        _building_now.discard(name)


def _schedule_build(name: str, raw_cover: pygame.Surface):
    """Запускает фоновую сборку ассетов, если ещё не начата и не готова."""
    with _asset_cache_lock:
        if name in _asset_cache:
            return
    if name in _building_now:
        return
    _building_now.add(name)
    threading.Thread(target=_build_and_cache, args=(name, raw_cover), daemon=True).start()


def _apply_assets(assets: dict):
    """Атомарно ставит готовые ассеты как текущие. Только из главного потока."""
    global cached_raw, cached_bg, cached_cover, cached_bloom, main_color
    global is_transitioning, transition_progress, old_cover
    global is_list_animating, list_offset

    with _render_lock:
        old_cover           = cached_cover
        cached_raw          = assets["raw"]
        cached_bg           = assets["bg"]
        cached_cover        = assets["cover"]
        cached_bloom        = assets["bloom"]
        main_color          = assets["color"]
        transition_progress = 0.0
        is_transitioning    = True
        list_offset         = float(ROW_HEIGHT)
        is_list_animating   = True


# ===== TRACK UPDATE =====

def update_track(info, force=False):
    global cached_track

    name        = info["name"]
    cover       = info["cover"]
    next_covers = info.get("next_covers", [])  # [(name, artist, surface), ...]

    if cover is None:
        return

    # --- переключение текущего трека ---
    if name != cached_track or force:
        with _asset_cache_lock:
            assets = _asset_cache.get(name)

        if assets:
            # Ассеты уже готовы (предзагружены) — мгновенное переключение
            _apply_assets(assets)
            cached_track = name
        else:
            # Запускаем сборку и ждём следующего кадра
            _schedule_build(name, cover)
            return

    # --- предзагрузка следующих треков в фоне ---
    for (n, _a, raw) in next_covers:
        if raw is not None:
            _schedule_build(n, raw)


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

def on_seek_start(click_x):
    global seeking, seek_frac
    if _prog_bar_rect is None:
        return
    seeking   = True
    seek_frac = max(0.0, min(1.0, (click_x - _prog_bar_rect.x) / _prog_bar_rect.width))

def on_seek_drag(mouse_x):
    """Только двигаем визуальный ползунок — в API ничего не шлём."""
    global seek_frac
    if not seeking or _prog_bar_rect is None:
        return
    seek_frac = max(0.0, min(1.0, (mouse_x - _prog_bar_rect.x) / _prog_bar_rect.width))

def on_seek_end(duration: float):
    """Отпустили мышь — один вызов seek в Spotify."""
    global seeking
    if not seeking:
        return
    seeking = False
    if duration > 0:
        spotify.seek(int(seek_frac * duration * 1000))


# ===== DRAW =====
def draw(info, values):
    global rotation, transition_progress, is_transitioning
    global list_offset, is_list_animating
    global center_x, center_y, cached_bg
    global is_playing, _prog_bar_rect

    W, H = screen.get_size()
    center_x, center_y = W // 2, H // 2
    dynamic_radius = radius * pulse_value

    name        = info["name"]
    artist      = info["artist"]
    progress    = info["progress"]
    duration    = info["duration"]
    next_tracks = info.get("next", [])
    is_playing  = info.get("is_playing", is_playing)

    with _render_lock:
        cur_bg    = cached_bg
        cur_raw   = cached_raw
        cur_cover = cached_cover
        cur_bloom = cached_bloom
        cur_old   = old_cover
        cur_color = main_color

    # background
    if cur_bg:
        bw, bh = cur_bg.get_size()
        if bw != W or bh != H:
            if cur_raw:
                new_bg = rebuild_bg(cur_raw)
                with _render_lock:
                    cached_bg = new_bg
                cur_bg = new_bg
        screen.blit(cur_bg, (0, 0))
    else:
        screen.fill((10, 10, 20))

    # cover fade
    if is_transitioning and cur_old is not None:
        transition_progress += 0.05
        clamp = min(transition_progress, 1.0)
        size  = int(cover_radius * 2 * pulse_value)
        old_s = pygame.transform.smoothscale(cur_old.copy(),   (size, size))
        new_s = pygame.transform.smoothscale(cur_cover.copy(), (size, size))
        old_s.set_alpha(255 - int(255 * clamp))
        new_s.set_alpha(int(255 * clamp))
        screen.blit(old_s, (center_x - size // 2, center_y - size // 2))
        screen.blit(new_s, (center_x - size // 2, center_y - size // 2))
        if transition_progress >= 1:
            transition_progress = 1.0
            is_transitioning    = False
    elif cur_cover:
        size = int(cover_radius * 2 * pulse_value)
        screen.blit(pygame.transform.smoothscale(cur_cover, (size, size)),
                    (center_x - size // 2, center_y - size // 2))

    # bloom
    if cur_bloom:
        size = int(radius * 4 * pulse_value)
        screen.blit(pygame.transform.smoothscale(cur_bloom, (size, size)),
                    (center_x - size // 2, center_y - size // 2),
                    special_flags=pygame.BLEND_ADD)

    # glow
    glow = pygame.Surface((W, H), pygame.SRCALPHA)
    for i in range(20):
        pygame.draw.circle(glow, (*cur_color, max(5, 100 - i * 7)),
                           (center_x, center_y), int(dynamic_radius + i * 4))
    screen.blit(glow, (0, 0))

    # circle lines
    quarter       = heights[:BARS // 4]
    circle_values = np.tile(quarter, 4)
    rotation     += 0.01
    for i in range(BARS):
        angle = (i / BARS) * 2 * math.pi + rotation
        v     = circle_values[i]
        x1 = center_x + math.cos(angle) * dynamic_radius
        y1 = center_y + math.sin(angle) * dynamic_radius
        x2 = center_x + math.cos(angle) * (dynamic_radius + v)
        y2 = center_y + math.sin(angle) * (dynamic_radius + v)
        pygame.draw.line(screen, (120, 180, 255), (x1, y1), (x2, y2), 2)

    # bars
    bw = W // BARS
    for i in range(BARS):
        h = int(heights[i])
        pygame.draw.rect(screen, (80, 140, 255), (i * bw, H - h, bw - 2, h))

    # roulette list
    if is_list_animating:
        list_offset *= 0.82
        if list_offset < 0.5:
            list_offset       = 0.0
            is_list_animating = False

    track_list = [("current", (name, artist))]
    track_list += [("next", t) for t in next_tracks[:2]]

    base_y   = 20
    clip_r   = pygame.Rect(0, 0, W // 2, base_y + ROW_HEIGHT * (LIST_MAX_VISIBLE + 1))
    old_clip = screen.get_clip()
    screen.set_clip(clip_r)

    for i, (typ, (t, a)) in enumerate(track_list):
        y    = base_y + i * ROW_HEIGHT + int(list_offset)
        line = f"{t} — {a}"
        if typ == "current":
            txt = font.render(line, True, (255, 255, 255))
        else:
            fade = max(0, 130 - i * 30)
            txt  = font_small.render(line, True, (200, 200, 200))
            txt.set_alpha(fade)
        screen.blit(txt, (20, y))

    screen.set_clip(old_clip)

    # progress bar
    mx, my = pygame.mouse.get_pos()

    if duration > 0:
        bar_w = radius * 2 + 40
        bx    = center_x - bar_w // 2
        by    = center_y + radius + 60

        frac = seek_frac if seeking else min(1.0, progress / duration)

        _prog_bar_rect = pygame.Rect(bx, by, bar_w, 10)

        hover   = seeking or _prog_bar_rect.inflate(0, 16).collidepoint(mx, my)
        bar_h   = 8 if hover else 5
        by_draw = by + (10 - bar_h) // 2

        pygame.draw.rect(screen, (60, 60, 60),    (bx, by_draw, bar_w, bar_h),             border_radius=4)
        pygame.draw.rect(screen, (255, 255, 255), (bx, by_draw, int(bar_w * frac), bar_h), border_radius=4)

        if hover:
            dot_x = bx + int(bar_w * frac)
            pygame.draw.circle(screen, (255, 255, 255), (dot_x, by_draw + bar_h // 2), 6)

        shown_progress = seek_frac * duration if seeking else progress
        screen.blit(font_small.render(time.strftime('%M:%S', time.gmtime(shown_progress)), True, (170, 170, 170)),
                    (bx, by + 14))
        screen.blit(font_small.render(time.strftime('%M:%S', time.gmtime(duration)), True, (170, 170, 170)),
                    (bx + bar_w - 38, by + 14))

    # control buttons
    btn_y   = center_y + radius + 108
    btn_sz  = 9
    btn_gap = 55

    btn_surf = pygame.Surface((W, H), pygame.SRCALPHA)

    for key, bx_btn in [("prev", center_x - btn_gap), ("play", center_x), ("next", center_x + btn_gap)]:
        dist  = math.hypot(mx - bx_btn, my - btn_y)
        hover = dist < btn_sz * 2.5
        color = (255, 255, 255) if hover else (180, 180, 180)
        alpha = 255 if hover else 190
        sz    = int(btn_sz * (1.2 if hover else 1.0))
        rgba  = (*color, alpha)

        if key == "prev":
            draw_icon_prev(btn_surf, bx_btn, btn_y, sz, rgba)
        elif key == "next":
            draw_icon_next(btn_surf, bx_btn, btn_y, sz, rgba)
        else:
            if is_playing:
                draw_icon_pause(btn_surf, bx_btn, btn_y, sz, rgba)
            else:
                draw_icon_play(btn_surf, bx_btn, btn_y, sz, rgba)

    screen.blit(btn_surf, (0, 0))


# ===== MAIN LOOP =====
running = True
info    = {"name": "", "artist": "", "progress": 0, "duration": 0, "cover": None}

while running:

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False

        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            W, H    = screen.get_size()
            cx, cy  = W // 2, H // 2
            btn_y   = cy + radius + 108
            btn_gap = 55
            btn_sz  = 9
            ex, ey  = event.pos

            if math.hypot(ex - (cx - btn_gap), ey - btn_y) < btn_sz * 2.5:
                on_prev()
            elif math.hypot(ex - cx, ey - btn_y) < btn_sz * 2.5:
                on_play_pause()
            elif math.hypot(ex - (cx + btn_gap), ey - btn_y) < btn_sz * 2.5:
                on_next()
            elif _prog_bar_rect and _prog_bar_rect.inflate(0, 20).collidepoint(ex, ey):
                on_seek_start(ex)

        elif event.type == pygame.MOUSEMOTION:
            if seeking:
                on_seek_drag(event.pos[0])

        elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            if seeking:
                on_seek_end(info.get("duration", 0))

        elif event.type == pygame.KEYDOWN:
            if event.key == pygame.K_SPACE:
                on_play_pause()
            elif event.key == pygame.K_RIGHT:
                on_next()
            elif event.key == pygame.K_LEFT:
                on_prev()

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


stream.stop()
pygame.quit()