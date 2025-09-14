import atexit
import contextlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import whisper
import yt_dlp
from flask import Flask, Response, jsonify, render_template, request, send_file

os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"


class ProgressReporter:
    def __init__(self, progress_callback=None, task_name="Processing"):
        self.progress_callback = progress_callback
        self.task_name = task_name

    def __call__(self, current, total=None):
        if self.progress_callback and total:
            percent = int((current / total) * 100)
            self.progress_callback(f"{self.task_name}: {percent}%")


def monkey_patch_tqdm(progress_callback=None, task_name="Processing"):
    """Replace tqdm with our custom progress reporter"""
    if not progress_callback:
        return
    import tqdm

    class CustomTqdm:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total", 100)
            self.current = 0
            self.desc = kwargs.get("desc", task_name)
            self.start_time = time.time()
            if progress_callback:
                progress_callback(f"{self.desc}: 0%")

        def update(self, n=1):
            self.current += n
            if self.total and progress_callback and self.current > 0:
                percent = min(int((self.current / self.total) * 100), 100)
                elapsed = time.time() - self.start_time
                if elapsed > 1 and percent > 0:
                    estimated_total_time = elapsed * (100 / percent)
                    eta_seconds = estimated_total_time - elapsed
                    if eta_seconds > 0:
                        eta_minutes = int(eta_seconds // 60)
                        eta_seconds = int(eta_seconds % 60)
                        if eta_minutes > 0:
                            eta_str = f" (ETA: {eta_minutes}m{eta_seconds:02d}s)"
                        else:
                            eta_str = f" (ETA: {eta_seconds}s)"
                        progress_callback(f"{self.desc}: {percent}%{eta_str}")
                    else:
                        progress_callback(f"{self.desc}: {percent}%")
                else:
                    progress_callback(f"{self.desc}: {percent}%")

        def set_description(self, desc):
            self.desc = desc
            if progress_callback:
                percent = int((self.current / self.total) * 100) if self.total else 0
                progress_callback(f"{desc}: {percent}%")

        def close(self):
            if progress_callback:
                progress_callback(f"{self.desc}: 100%")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    original_tqdm = tqdm.tqdm
    tqdm.tqdm = CustomTqdm
    return original_tqdm


def clear_whisper_cache():
    try:
        cache_dir = os.path.join(os.path.expanduser("~"), ".cache", "whisper")
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            return True
    except Exception:
        pass
    return False


app = Flask(__name__)
app.config["SECRET_KEY"] = "your-secret-key-here"
SONGS_DIR = Path("songs")
SONGS_DIR.mkdir(exist_ok=True)
LIBRARY_DB = "library.json"


class VibeAmpProcessor:
    def __init__(self):
        self.whisper_model = None

    def load_whisper_model(self, model_size="large-v3"):
        if self.whisper_model is None:
            try:
                self.whisper_model = whisper.load_model(model_size)
            except Exception as e:
                error_msg = str(e).lower()
                if "sha256" in error_msg or "checksum" in error_msg:
                    clear_whisper_cache()
                if model_size == "large-v3":
                    return self.load_whisper_model("medium")
                elif model_size == "medium":
                    return self.load_whisper_model("base")
                elif model_size == "base":
                    return self.load_whisper_model("tiny")
                else:
                    raise e
        return self.whisper_model

    def extract_video_id(self, url):
        patterns = [
            r"(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)",
            r"youtube\.com.*[?&]v=([^&\n?#]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def download_audio(self, youtube_url, song_id, progress_callback=None):
        song_dir = SONGS_DIR / song_id
        song_dir.mkdir(exist_ok=True)
        audio_path = song_dir / "audio.mp3"
        if audio_path.exists():
            if progress_callback:
                progress_callback("Using existing audio file")
            with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
                try:
                    info = ydl.extract_info(youtube_url, download=False)
                    title = info.get("title", "Unknown")
                    duration = info.get("duration", 0)
                    return str(audio_path), title, duration
                except:
                    return str(audio_path), "Unknown", 0
        if progress_callback:
            progress_callback("Getting video information")

        def progress_hook(d):
            if progress_callback and d["status"] == "downloading":
                percent = 0
                if "total_bytes" in d and d["total_bytes"]:
                    percent = (d["downloaded_bytes"] / d["total_bytes"]) * 100
                elif "total_bytes_estimate" in d and d["total_bytes_estimate"]:
                    percent = (d["downloaded_bytes"] / d["total_bytes_estimate"]) * 100
                eta_str = ""
                if "eta" in d and d["eta"] is not None:
                    eta_str = f" (ETA: {d['eta']}s)"
                elif "_eta_str" in d and d["_eta_str"]:
                    eta_str = f" (ETA: {d['_eta_str']})"
                if percent > 0:
                    progress_callback(f"Downloading audio: {percent:.1f}%{eta_str}")
                else:
                    downloaded_mb = d["downloaded_bytes"] / (1024 * 1024)
                    progress_callback(
                        f"Downloading audio: {downloaded_mb:.1f}MB{eta_str}"
                    )
            elif progress_callback and d["status"] == "finished":
                progress_callback("Converting audio to MP3")

        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(song_dir / "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            title = info.get("title", "Unknown")
            duration = info.get("duration", 0)
            if progress_callback:
                if duration:
                    progress_callback(f"Starting download: {duration}s audio")
                else:
                    progress_callback("Starting audio download")
            ydl.download([youtube_url])
            for file_path in song_dir.glob("*.mp3"):
                if file_path.name != "audio.mp3":
                    file_path.rename(audio_path)
                    break
            if progress_callback:
                progress_callback("Audio download completed")
            return str(audio_path), title, duration

    def transcribe_audio(self, audio_path, force_language=None, progress_callback=None):
        model = self.load_whisper_model()
        if progress_callback:
            progress_callback("Initializing transcription")
        original_tqdm = None
        if progress_callback:
            original_tqdm = monkey_patch_tqdm(progress_callback, "Transcribing")
        try:
            if force_language:
                result = model.transcribe(
                    audio_path,
                    word_timestamps=True,
                    language=force_language,
                    task="transcribe",
                    verbose=True if progress_callback else False,
                )
            else:
                result = model.transcribe(
                    audio_path,
                    word_timestamps=True,
                    language=None,
                    task="transcribe",
                    verbose=True if progress_callback else False,
                )
        finally:
            if original_tqdm:
                import tqdm

                tqdm.tqdm = original_tqdm
        if progress_callback:
            progress_callback("Finalizing transcription")
        segments = []
        for segment in result["segments"]:
            segments.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"].strip(),
                }
            )
        return segments, result.get("language", "unknown")

    def translate_audio_to_english(self, audio_path):
        model = self.load_whisper_model()
        result = model.transcribe(
            audio_path,
            word_timestamps=True,
            language=None,
            task="translate",
            verbose=False,
        )
        segments = []
        for segment in result["segments"]:
            segments.append(
                {
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"].strip(),
                    "translated": segment["text"].strip(),
                }
            )
        return segments, result.get("language", "unknown")

    def translate_lyrics(
        self, segments, target_language="en", audio_path=None, progress_callback=None
    ):
        if not segments or target_language != "en":
            return segments
        if audio_path and Path(audio_path).exists():
            try:
                if progress_callback:
                    progress_callback("Initializing translation")
                model = self.load_whisper_model()
                original_tqdm = None
                if progress_callback:
                    original_tqdm = monkey_patch_tqdm(progress_callback, "Translating")
                try:
                    result = model.transcribe(
                        audio_path,
                        word_timestamps=True,
                        language=None,
                        task="translate",
                        verbose=True if progress_callback else False,
                    )
                finally:
                    if original_tqdm:
                        import tqdm

                        tqdm.tqdm = original_tqdm
                if progress_callback:
                    progress_callback("Aligning translations with original text")
                english_segments = []
                for segment in result["segments"]:
                    english_segments.append(
                        {
                            "start": segment["start"],
                            "end": segment["end"],
                            "english_text": segment["text"].strip(),
                        }
                    )
                translated_segments = []
                for original_segment in segments:
                    closest_english = None
                    min_time_diff = float("inf")
                    for english_segment in english_segments:
                        time_diff = abs(
                            original_segment["start"] - english_segment["start"]
                        )
                        if time_diff < min_time_diff:
                            min_time_diff = time_diff
                            closest_english = english_segment
                    translated_segments.append(
                        {
                            "start": original_segment["start"],
                            "end": original_segment["end"],
                            "text": original_segment["text"],
                            "translated": (
                                closest_english["english_text"]
                                if closest_english
                                else original_segment["text"]
                            ),
                        }
                    )
                return translated_segments
            except Exception as e:
                print(f"Translation failed: {e}")
                for segment in segments:
                    segment["translated"] = segment["text"]
                return segments
        else:
            for segment in segments:
                segment["translated"] = segment["text"]
            return segments

    def save_song_data(
        self, song_id, title, duration, segments, detected_language, youtube_url
    ):
        song_dir = SONGS_DIR / song_id
        lyrics_path = song_dir / "lyrics.json"
        with open(lyrics_path, "w", encoding="utf-8") as f:
            json.dump(segments, f, indent=2, ensure_ascii=False)
        metadata = {
            "song_id": song_id,
            "title": title,
            "duration": duration,
            "detected_language": detected_language,
            "youtube_url": youtube_url,
            "created_at": time.time(),
        }
        metadata_path = song_dir / "metadata.json"
        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        return metadata


processor = VibeAmpProcessor()


def cleanup_resources():
    global processor
    if processor and processor.whisper_model:
        processor.whisper_model = None


def signal_handler(sig, frame):
    cleanup_resources()
    sys.exit(0)


atexit.register(cleanup_resources)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)


def load_library():
    if Path(LIBRARY_DB).exists():
        with open(LIBRARY_DB, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_library(library):
    with open(LIBRARY_DB, "w", encoding="utf-8") as f:
        json.dump(library, f, indent=2, ensure_ascii=False)


def find_existing_song_by_url(youtube_url):
    library = load_library()
    for song in library:
        if song.get("youtube_url") == youtube_url:
            return song
    return None


def process_song_background(song_id, youtube_url, translate_to_english, force_language):
    try:
        processing_status[song_id] = {
            "status": "processing",
            "step": "Verifying video exists",
        }

        def download_progress(msg):
            processing_status[song_id] = {"status": "processing", "step": msg}

        audio_path, title, duration = processor.download_audio(
            youtube_url, song_id, download_progress
        )

        def transcription_progress(msg):
            processing_status[song_id] = {"status": "processing", "step": msg}

        segments, detected_language = processor.transcribe_audio(
            audio_path, force_language, transcription_progress
        )
        if translate_to_english and detected_language != "en":

            def translation_progress(msg):
                processing_status[song_id] = {"status": "processing", "step": msg}

            segments = processor.translate_lyrics(
                segments, "en", audio_path, translation_progress
            )
        processing_status[song_id] = {"status": "processing", "step": "Saving data"}
        metadata = processor.save_song_data(
            song_id, title, duration, segments, detected_language, youtube_url
        )
        library = load_library()
        library.append(metadata)
        save_library(library)
        processing_status[song_id] = {
            "status": "completed",
            "step": "Processing completed",
            "metadata": metadata,
        }
    except Exception as e:
        processing_status[song_id] = {"status": "error", "step": f"Error: {str(e)}"}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/library")
def get_library():
    library = load_library()
    return jsonify(library)


@app.route("/api/process", methods=["POST"])
def process_youtube_url():
    data = request.get_json()
    youtube_url = data.get("url", "").strip()
    translate_to_english = data.get("translate", False)
    force_language = data.get("language", None)
    if not youtube_url:
        return jsonify({"error": "No URL provided"}), 400
    video_id = processor.extract_video_id(youtube_url)
    if not video_id:
        return jsonify({"error": "Invalid YouTube URL"}), 400
    existing_song = find_existing_song_by_url(youtube_url)
    if existing_song:
        return jsonify(
            {
                "success": True,
                "song_id": existing_song["song_id"],
                "metadata": existing_song,
                "already_processed": True,
                "message": "This video has already been processed. Loading existing data...",
            }
        )
    try:
        song_id = str(uuid.uuid4())
        processing_status[song_id] = {
            "status": "processing",
            "step": "Starting processing",
        }
        thread = threading.Thread(
            target=process_song_background,
            args=(song_id, youtube_url, translate_to_english, force_language),
        )
        thread.daemon = True
        thread.start()
        return jsonify({"success": True, "song_id": song_id, "processing": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


processing_status = {}


@app.route("/api/process-status/<song_id>")
def get_process_status(song_id):
    status = processing_status.get(song_id, {"status": "unknown", "step": "unknown"})
    return jsonify(status)


@app.route("/api/song/<song_id>")
def get_song_data(song_id):
    song_dir = SONGS_DIR / song_id
    if not song_dir.exists():
        return jsonify({"error": "Song not found"}), 404
    try:
        metadata_path = song_dir / "metadata.json"
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        lyrics_path = song_dir / "lyrics.json"
        with open(lyrics_path, "r", encoding="utf-8") as f:
            lyrics = json.load(f)
        return jsonify({"metadata": metadata, "lyrics": lyrics})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/audio/<song_id>")
def serve_audio(song_id):
    song_dir = SONGS_DIR / song_id
    audio_path = song_dir / "audio.mp3"
    if not audio_path.exists():
        return jsonify({"error": "Audio file not found"}), 404
    range_header = request.headers.get("Range", None)
    if not range_header:
        return send_file(audio_path)
    byte_start = 0
    byte_end = None
    if range_header:
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            byte_start = int(match.group(1))
            if match.group(2):
                byte_end = int(match.group(2))
    file_size = audio_path.stat().st_size
    if byte_end is None:
        byte_end = file_size - 1
    content_length = byte_end - byte_start + 1

    def generate():
        with open(audio_path, "rb") as audio_file:
            audio_file.seek(byte_start)
            remaining = content_length
            while remaining:
                chunk_size = min(8192, remaining)
                data = audio_file.read(chunk_size)
                if not data:
                    break
                yield data
                remaining -= len(data)

    response = Response(
        generate(),
        206,
        headers={
            "Content-Range": f"bytes {byte_start}-{byte_end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(content_length),
            "Content-Type": "audio/mpeg",
        },
    )
    return response


if __name__ == "__main__":
    app.run(debug=True, port=8000)
