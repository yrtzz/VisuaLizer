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
    "user-modify-playback-state "
    "user-library-read "
    "playlist-read-private "
    "playlist-read-collaborative"
)

CACHE_PATH    = os.path.join(os.path.dirname(__file__), ".spotifycache")
POLL_INTERVAL = 1.0      # НЕ снижать ниже 1.0 — rate limit Spotify API
PRELOAD_COUNT = 2


class SpotifyClient(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True)

        self.lock = threading.Lock()

        self.name        = ""
        self.artist      = ""
        self.cover       = None
        self._cover_url  = None

        self.progress_ms = 0
        self.duration_ms = 1
        self.is_playing  = False

        self.prev_tracks = []
        self.next_tracks = []
        self.next_covers = []

        self.albums         = []
        self._albums_loaded = False
        self._albums_status = "loading"
        self._albums_error  = ""

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
        with self.lock:
            for url in list(self._cover_url_cache):
                if url not in keep_urls:
                    del self._cover_url_cache[url]

    def _load_library(self):
        """Загружает плейлисты + сохранённые альбомы. Запускается один раз при старте."""
        items = []

        # --- Плейлисты ---
        try:
            offset = 0
            while True:
                res   = self.sp.current_user_playlists(limit=50, offset=offset)
                batch = res.get("items", [])
                for pl in batch:
                    if not pl:
                        continue
                    name      = pl.get("name", "")
                    owner     = pl.get("owner", {}).get("display_name", "")
                    uri       = pl.get("uri", "")
                    images    = pl.get("images") or []
                    cover_url = images[0]["url"] if images else None
                    cover     = self._fetch_cover(cover_url) if cover_url else None
                    items.append({"name": name, "artist": owner,
                                  "uri": uri, "cover": cover, "type": "playlist"})
                offset += len(batch)
                if offset >= res.get("total", 0) or not batch:
                    break
            print(f"Playlists loaded: {sum(1 for i in items if i.get('type')=='playlist')}")
        except Exception as e:
            print("Playlists load error:", e)
            if "scope" in str(e).lower() or "403" in str(e):
                with self.lock:
                    self._albums_status = "error"
                    self._albums_error  = "Удали .spotifycache и перезапусти (новые права)"

        # --- Сохранённые альбомы ---
        try:
            offset = 0
            while True:
                res   = self.sp.current_user_saved_albums(limit=50, offset=offset)
                batch = res.get("items", [])
                for item in batch:
                    album  = item["album"]
                    uri    = album["uri"]
                    images = album.get("images", [])
                    cover_url = images[0]["url"] if images else None
                    cover     = self._fetch_cover(cover_url) if cover_url else None
                    items.append({"name":   album["name"],
                                  "artist": album["artists"][0]["name"],
                                  "uri": uri, "cover": cover, "type": "album"})
                offset += len(batch)
                if offset >= res.get("total", 0) or not batch:
                    break
            print(f"Saved albums loaded: {sum(1 for i in items if i.get('type')=='album')}")
        except Exception as e:
            print("Saved albums load error:", e)

        status = "ok" if items else "empty"
        with self.lock:
            self.albums         = items
            self._albums_loaded = True
            self._albums_status = status

    # ===== POLL LOOP =====

    def run(self):
        prev_memory = []

        # Грузим библиотеку в отдельном потоке чтобы не блокировать poll
        threading.Thread(target=self._load_library, daemon=True).start()

        while True:
            try:
                playback = self.sp.current_playback()

                if playback and playback["item"]:
                    item     = playback["item"]
                    name     = item["name"]
                    artist   = item["artists"][0]["name"]
                    progress = playback["progress_ms"]
                    duration = item["duration_ms"]

                    images    = item["album"]["images"]
                    cover_url = images[0]["url"] if images else None
                    cover_surface = self._fetch_cover(cover_url) if cover_url else None

                    queue_data  = self.sp.queue()
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
                        if len(next_covers) < PRELOAD_COUNT:
                            t_cover = self._fetch_cover(t_url) if t_url else None
                            next_covers.append((t_name, t_artist, t_cover))

                    self._evict_cover_cache(active_urls)

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
                        self.last_poll   = time.time()

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
                "next_covers": list(self.next_covers),
            }

    def get_albums(self):
        with self.lock:
            return list(self.albums)

    def get_albums_status(self):
        with self.lock:
            return self._albums_status, self._albums_error

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

    def play_album(self, uri: str):
        try:
            self.sp.start_playback(context_uri=uri)
        except Exception as e:
            print("play_album error:", e)