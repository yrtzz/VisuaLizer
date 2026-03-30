import threading
import time
import requests
import io
import os
import pygame
import spotipy
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID = "b3a484a1c5c34919b0e56eaf02a28526"
CLIENT_SECRET = "eb939d66fda746a091b094ca56f149a7"
REDIRECT_URI = "http://127.0.0.1:8888/callback"

SCOPE = "user-read-playback-state user-read-currently-playing"

CACHE_PATH = os.path.join(os.path.dirname(__file__), ".spotifycache")

POLL_INTERVAL = 1.0


class SpotifyClient(threading.Thread):

    def __init__(self):
        super().__init__(daemon=True)

        self.lock = threading.Lock()

        self.name = ""
        self.artist = ""
        self.cover = None

        self.progress_ms = 0
        self.duration_ms = 1
        self.is_playing = False

        self.prev_tracks = []
        self.next_tracks = []

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

    def fetch_cover(self, url):

        try:
            r = requests.get(url)
            img = pygame.image.load(io.BytesIO(r.content)).convert_alpha()
            return img
        except:
            return None

    def run(self):

        prev_memory = []

        while True:

            try:

                playback = self.sp.current_playback()

                if playback and playback["item"]:

                    item = playback["item"]

                    name = item["name"]
                    artist = item["artists"][0]["name"]

                    progress = playback["progress_ms"]
                    duration = item["duration_ms"]

                    images = item["album"]["images"]
                    cover_url = images[0]["url"] if images else None

                    cover_surface = None

                    if cover_url:
                        cover_surface = self.fetch_cover(cover_url)

                    # очередь Spotify
                    queue_data = self.sp.queue()

                    next_tracks = []

                    for tr in queue_data["queue"][:2]:

                        n = tr["name"]
                        a = tr["artists"][0]["name"]

                        next_tracks.append((n, a))

                    # память предыдущих треков
                    if len(prev_memory) == 0 or prev_memory[-1][0] != name:
                        prev_memory.append((name, artist))

                    prev_tracks = prev_memory[-3:-1]

                    with self.lock:

                        self.name = name
                        self.artist = artist
                        self.cover = cover_surface

                        self.progress_ms = progress
                        self.duration_ms = duration
                        self.is_playing = playback["is_playing"]

                        self.prev_tracks = prev_tracks
                        self.next_tracks = next_tracks

                        self.last_poll = time.time()

            except Exception as e:
                print("Spotify error:", e)

            time.sleep(POLL_INTERVAL)

    def get_info(self):

        with self.lock:

            progress = self.progress_ms / 1000
            duration = self.duration_ms / 1000

            if self.is_playing:
                progress += time.time() - self.last_poll

            return {
                "name": self.name,
                "artist": self.artist,
                "cover": self.cover,
                "progress": progress,
                "duration": duration,
                "prev": self.prev_tracks,
                "next": self.next_tracks
            }