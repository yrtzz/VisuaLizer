import pygame
import numpy as np
import sounddevice as sd
import math
import time
import cv2

from spotify_client import SpotifyClient


# ===== CONFIG =====
WIDTH, HEIGHT = 900, 600
BARS = 64

SAMPLERATE = 44100
BLOCKSIZE = 1024

ROW_HEIGHT = 30          # высота одной строки в списке
LIST_MAX_VISIBLE = 5     # текущий + 4 следующих


# ===== INIT =====
pygame.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE | pygame.DOUBLEBUF)
pygame.display.set_caption("Music Visualizer")

clock = pygame.time.Clock()

font = pygame.font.SysFont("Segoe UI", 18)
font_small = pygame.font.SysFont("Segoe UI", 14)


# ===== AUDIO =====
audio_buffer = np.zeros(BLOCKSIZE)

def audio_callback(indata, frames, time_, status):
    global audio_buffer
    audio_buffer = indata[:, 0] if indata.ndim > 1 else indata


# ===== UTILS =====
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
    """Пересчитывает фон под текущий размер экрана."""
    w, h = screen.get_size()
    bg = pygame.transform.smoothscale(cover_surface, (w, h))
    bg = blur_surface(bg, 35)
    dark = pygame.Surface((w, h), pygame.SRCALPHA)
    dark.fill((0, 0, 0, 200))
    bg.blit(dark, (0, 0))
    return bg


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

heights = np.zeros(BARS)

center_x, center_y = WIDTH // 2, HEIGHT // 2
radius = 120
cover_radius = radius - 3

rotation = 0
pulse_value = 1
volume_smooth = 0.1

cached_cover = None
cached_track = None
cached_bg = None
cached_bloom = None
cached_raw_cover = None   # оригинал обложки для rebuild_bg при ресайзе
main_color = (255, 255, 255)

# transition
is_transitioning = False
transition_progress = 1.0

old_cover = None
old_name = ""
old_artist = ""

# ===== ROULETTE STATE =====
# list_offset идёт от ROW_HEIGHT → 0 (элементы едут снизу вверх)
list_offset = 0.0
is_list_animating = False


# ===== AUDIO PROCESS =====
def process_audio():
    global heights, pulse_value, volume_smooth

    fft = np.fft.rfft(audio_buffer)
    magnitude = np.abs(fft)

    bins = np.array_split(magnitude, BARS // 2)
    values = np.array([np.mean(b) for b in bins])
    values = np.concatenate((values, values[::-1]))

    values = np.log1p(values) * 200

    current_volume = np.mean(values)
    volume_smooth = volume_smooth * 0.9 + current_volume * 0.1

    if volume_smooth > 0:
        values = values / volume_smooth * 10
        heights = heights * 0.7 + values * 0.3

    bass = np.mean(values[:5]) * 0.004
    pulse_value = pulse_value * 0.8 + (1 + min(bass, 0.25)) * 0.2

    return values


# ===== TRACK UPDATE =====
def update_track(info):
    global cached_track, cached_cover, cached_bg, cached_bloom, main_color
    global is_transitioning, transition_progress
    global old_cover, old_name, old_artist
    global is_list_animating, list_offset
    global cached_raw_cover

    name = info["name"]
    artist = info["artist"]
    cover = info["cover"]

    if cover is None or name == cached_track:
        return

    # FIX: сохраняем СТАРЫЕ данные, а не новые
    old_cover = cached_cover
    old_name = cached_track or ""
    old_artist = info.get("artist", "")   # это уже новый artist — но нам нужен старый

    cached_track = name
    cached_raw_cover = cover  # сохраняем для rebuild при ресайзе

    small = pygame.transform.smoothscale(cover, (1, 1))
    main_color = small.get_at((0, 0))[:3]

    cached_bg = rebuild_bg(cover)

    surf = pygame.transform.smoothscale(cover, (cover_radius * 2, cover_radius * 2))
    mask = pygame.Surface((cover_radius * 2, cover_radius * 2), pygame.SRCALPHA)
    pygame.draw.circle(mask, (255, 255, 255), (cover_radius, cover_radius), cover_radius)
    surf.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

    cached_cover = surf
    cached_bloom = create_bloom(radius, main_color)

    transition_progress = 0.0
    is_transitioning = True

    # ===== РУЛЕТКА: запускаем с нижней позиции =====
    list_offset = float(ROW_HEIGHT)
    is_list_animating = True


# ===== DRAW =====
def draw(info, values):
    global rotation, transition_progress, is_transitioning
    global list_offset, is_list_animating
    global center_x, center_y, cached_bg

    W, H = screen.get_size()
    center_x, center_y = W // 2, H // 2   # FIX: пересчёт при ресайзе
    dynamic_radius = radius * pulse_value

    name = info["name"]
    artist = info["artist"]
    progress = info["progress"]
    duration = info["duration"]
    next_tracks = info.get("next", [])

    # background
    if cached_bg:
        # FIX: масштабируем фон если размер окна изменился
        bg_w, bg_h = cached_bg.get_size()
        if bg_w != W or bg_h != H:
            if cached_raw_cover:
                cached_bg = rebuild_bg(cached_raw_cover)
        screen.blit(cached_bg, (0, 0))
    else:
        screen.fill((10, 10, 20))

    # ===== COVER FADE =====
    if is_transitioning and old_cover is not None:
        transition_progress += 0.05

        alpha_new = int(255 * min(transition_progress, 1.0))
        alpha_old = 255 - alpha_new

        old = old_cover.copy()
        new = cached_cover.copy()
        old.set_alpha(alpha_old)
        new.set_alpha(alpha_new)

        size = int(cover_radius * 2 * pulse_value)
        old = pygame.transform.smoothscale(old, (size, size))
        new = pygame.transform.smoothscale(new, (size, size))

        screen.blit(old, (center_x - size // 2, center_y - size // 2))
        screen.blit(new, (center_x - size // 2, center_y - size // 2))

        if transition_progress >= 1:
            transition_progress = 1.0
            is_transitioning = False

    elif cached_cover:
        size = int(cover_radius * 2 * pulse_value)
        cover_scaled = pygame.transform.smoothscale(cached_cover, (size, size))
        screen.blit(cover_scaled, (center_x - size // 2, center_y - size // 2))

    # circle
    quarter = heights[:BARS // 4]
    circle_values = np.tile(quarter, 4)

    rotation += 0.01

    # bloom
    if cached_bloom:
        size = int(radius * 4 * pulse_value)
        bloom_scaled = pygame.transform.smoothscale(cached_bloom, (size, size))
        screen.blit(
            bloom_scaled,
            (center_x - size // 2, center_y - size // 2),
            special_flags=pygame.BLEND_ADD
        )

    # glow
    glow_surf = pygame.Surface((W, H), pygame.SRCALPHA)
    for i in range(20):
        pygame.draw.circle(
            glow_surf,
            (*main_color, max(5, 100 - i * 7)),
            (center_x, center_y),
            int(dynamic_radius + i * 4)
        )
    screen.blit(glow_surf, (0, 0))

    # circle lines
    for i in range(BARS):
        angle = (i / BARS) * 2 * math.pi + rotation
        value = circle_values[i]

        x1 = center_x + math.cos(angle) * dynamic_radius
        y1 = center_y + math.sin(angle) * dynamic_radius
        x2 = center_x + math.cos(angle) * (dynamic_radius + value)
        y2 = center_y + math.sin(angle) * (dynamic_radius + value)

        pygame.draw.line(screen, (120, 180, 255), (x1, y1), (x2, y2), 2)

    # bars
    bar_width = W // BARS
    for i in range(BARS):
        h = int(heights[i])
        pygame.draw.rect(screen, (80, 140, 255), (i * bar_width, H - h, bar_width - 2, h))

    # ===== РУЛЕТКА: плавно едем снизу вверх =====
    if is_list_animating:
        list_offset *= 0.82          # экспоненциальное торможение (easing out)
        if list_offset < 0.5:
            list_offset = 0.0
            is_list_animating = False

    # progress bar
    if duration > 0:
        bar_w = radius * 2 + 40
        bx = center_x - bar_w // 2
        by = center_y + radius + 70

        frac = min(1.0, progress / duration)

        pygame.draw.rect(screen, (60, 60, 60), (bx, by, bar_w, 6))
        pygame.draw.rect(screen, (255, 255, 255), (bx, by, int(bar_w * frac), 6))

        left = time.strftime('%M:%S', time.gmtime(progress))
        right = time.strftime('%M:%S', time.gmtime(duration))

        screen.blit(font_small.render(left, True, (170, 170, 170)), (bx, by + 10))
        screen.blit(font_small.render(right, True, (170, 170, 170)), (bx + bar_w - 40, by + 10))

    # ===== TRACK LIST (рулетка) =====
    track_list = [("current", (name, artist))]
    track_list += [("next", t) for t in next_tracks[:4]]

    base_y = 20
    list_area_h = ROW_HEIGHT * LIST_MAX_VISIBLE

    # клип-зона чтобы строки не вылезали за пределы списка
    clip_rect = pygame.Rect(0, base_y - ROW_HEIGHT, W // 2, list_area_h + ROW_HEIGHT * 2)
    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)

    for i, (typ, (t, a)) in enumerate(track_list):
        line = f"{t} — {a}"

        # FIX: смещение +list_offset (едем снизу вверх: 30 → 0)
        y = base_y + i * ROW_HEIGHT + int(list_offset)

        # пропускаем невидимые строки
        if y > base_y + list_area_h or y < base_y - ROW_HEIGHT:
            continue

        if typ == "current":
            # текущий трек — яркий
            alpha = 255
            rendered = font.render(line, True, (255, 255, 255))
        else:
            # следующие — затухают по мере удаления
            fade = max(0, 140 - i * 20)
            rendered = font_small.render(line, True, (200, 200, 200))
            rendered.set_alpha(fade)

        screen.blit(rendered, (20, y))

    screen.set_clip(old_clip)


# ===== MAIN LOOP =====
running = True

while running:

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.VIDEORESIZE:
            # pygame RESIZABLE сам обновляет screen, просто помечаем что нужно rebuild_bg
            pass  # rebuild происходит в draw() автоматически

    values = process_audio()

    info = spotify.get_info()
    update_track(info)

    draw(info, values)

    pygame.display.flip()
    pygame.event.pump()
    clock.tick(120)


stream.stop()
pygame.quit()