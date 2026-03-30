import threading
import time
import requests
import io
import os
import pygame
import spotipy
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID     = "b3a484a1c5c34919b0e56eaf02a28526"
CLIENT_SECRET = "eb939d66fda746a091b094ca56f149a7"
REDIRECT_URI  = "http://127.0.0.1:8888/callback"

SCOPE = (
    "user-read-playback-state "
    "user-read-currently-playing "
    "user-modify-playback-state"
)

CACHE_PATH    = os.path.join(os.path.dirname(__file__), ".spotifycache")
POLL_INTERVAL = 1.0

# Сколько следующих треков предзагружаем (обложки)
PRELOAD_COUNT = 2


class SpotifyClient(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True)

        self.lock = threading.Lock()

        self.name       = ""
        self.artist     = ""
        self.cover      = None
        self._cover_url = None

        self.progress_ms = 0
        self.duration_ms = 1
        self.is_playing  = False

        self.prev_tracks = []
        self.next_tracks = []       # [(name, artist), ...]
        self.next_covers = []       # [(name, artist, cover_surface), ...]

        # кэш URL → surface чтобы не грузить одну обложку дважды
        self._cover_url_cache: dict[str, pygame.Surface] = {}

        self.last_poll = time.time()

        self.sp = spotipy.Spotify(
            auth_manager=SpotifyOAuth(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                scope=SCOPE,
                cache_path=CACHE_PATH,
                open_browser=not os.path.exists(CACHE_PATH)
            )
        )

        self.start()

    # ===== INTERNAL =====

    def _fetch_cover(self, url: str) -> pygame.Surface | None:
        """Загружает обложку по URL. Использует внутренний кэш по URL."""
        with self.lock:
            cached = self._cover_url_cache.get(url)
        if cached is not None:
            return cached

        try:
            r    = requests.get(url, timeout=5)
            surf = pygame.image.load(io.BytesIO(r.content)).convert_alpha()
            with self.lock:
                self._cover_url_cache[url] = surf
            return surf
        except Exception as e:
            print("Cover fetch error:", e)
            return None

    def _evict_cover_cache(self, keep_urls: set[str]):
        """Удаляем из кэша обложки треков которых уже нет в очереди."""
        with self.lock:
            for url in list(self._cover_url_cache):
                if url not in keep_urls:
                    del self._cover_url_cache[url]

    # ===== POLL LOOP =====

    def run(self):
        prev_memory = []

        while True:
            try:
                playback = self.sp.current_playback()

                if playback and playback["item"]:
                    item = playback["item"]

                    name     = item["name"]
                    artist   = item["artists"][0]["name"]
                    progress = playback["progress_ms"]
                    duration = item["duration_ms"]

                    images    = item["album"]["images"]
                    cover_url = images[0]["url"] if images else None

                    # текущая обложка — берём из кэша или грузим
                    cover_surface = self._fetch_cover(cover_url) if cover_url else None

                    # очередь
                    queue_data = self.sp.queue()

                    next_tracks = []
                    next_covers = []
                    active_urls = {cover_url} if cover_url else set()

                    for tr in queue_data["queue"][:PRELOAD_COUNT + 2]:
                        t_name   = tr["name"]
                        t_artist = tr["artists"][0]["name"]
                        t_images = tr["album"]["images"]
                        t_url    = t_images[0]["url"] if t_images else None

                        if t_url:
                            active_urls.add(t_url)

                        if len(next_tracks) < 4:
                            next_tracks.append((t_name, t_artist))

                        # предзагружаем обложки только для первых PRELOAD_COUNT треков
                        if len(next_covers) < PRELOAD_COUNT:
                            t_cover = self._fetch_cover(t_url) if t_url else None
                            next_covers.append((t_name, t_artist, t_cover))

                    # чистим кэш от старых URL
                    self._evict_cover_cache(active_urls)

                    # история предыдущих
                    if not prev_memory or prev_memory[-1][0] != name:
                        prev_memory.append((name, artist))
                    prev_tracks = prev_memory[-3:-1]

                    with self.lock:
                        self.name        = name
                        self.artist      = artist
                        self.cover       = cover_surface
                        self._cover_url  = cover_url

                        self.progress_ms = progress
                        self.duration_ms = duration
                        self.is_playing  = playback["is_playing"]

                        self.prev_tracks = prev_tracks
                        self.next_tracks = next_tracks
                        self.next_covers = next_covers

                        self.last_poll = time.time()

            except Exception as e:
                print("Spotify poll error:", e)

            time.sleep(POLL_INTERVAL)

    # ===== READ =====

    def get_info(self):
        with self.lock:
            progress = self.progress_ms / 1000
            duration = self.duration_ms / 1000

            if self.is_playing:
                progress += time.time() - self.last_poll

            return {
                "name":        self.name,
                "artist":      self.artist,
                "cover":       self.cover,
                "progress":    progress,
                "duration":    duration,
                "is_playing":  self.is_playing,
                "prev":        list(self.prev_tracks),
                "next":        list(self.next_tracks),
                "next_covers": list(self.next_covers),   # [(name, artist, surface), ...]
            }

    # ===== CONTROLS =====

    def pause(self):
        try:
            self.sp.pause_playback()
            with self.lock:
                self.is_playing = False
        except Exception as e:
            print("pause error:", e)

    def resume(self):
        try:
            self.sp.start_playback()
            with self.lock:
                self.is_playing = True
        except Exception as e:
            print("resume error:", e)

    def next(self):
        try:
            self.sp.next_track()
        except Exception as e:
            print("next error:", e)

    def previous(self):
        try:
            self.sp.previous_track()
        except Exception as e:
            print("previous error:", e)

    def seek(self, position_ms: int):
        try:
            self.sp.seek_track(position_ms)
            with self.lock:
                self.progress_ms = position_ms
                self.last_poll   = time.time()
        except Exception as e:
            print("seek error:", e)