#!/usr/bin/env python3
import os
import json
import uuid
import shutil
from pathlib import Path
import re
import atexit
import signal
import sys
import time
from flask import Flask, request, jsonify, render_template, send_file, Response
import yt_dlp
import whisper

os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

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
app.config['SECRET_KEY'] = 'your-secret-key-here'

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
            r'(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)',
            r'youtube\.com.*[?&]v=([^&\n?#]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def download_audio(self, youtube_url, song_id):
        song_dir = SONGS_DIR / song_id
        song_dir.mkdir(exist_ok=True)
        audio_path = song_dir / "audio.mp3"

        if audio_path.exists():
            return str(audio_path)

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(song_dir / '%(title)s.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            title = info.get('title', 'Unknown')
            duration = info.get('duration', 0)
            ydl.download([youtube_url])
            for file_path in song_dir.glob("*.mp3"):
                if file_path.name != "audio.mp3":
                    file_path.rename(audio_path)
                    break
            return str(audio_path), title, duration

    def transcribe_audio(self, audio_path, force_language=None):
        model = self.load_whisper_model()
        
        if force_language:
            result = model.transcribe(audio_path, word_timestamps=True, language=force_language, task="transcribe", verbose=False)
        else:
            result = model.transcribe(audio_path, word_timestamps=True, language=None, task="transcribe", verbose=False)
            detected_lang = result.get('language', 'unknown')
            
            if detected_lang in ['ta', 'hi'] and len(result['segments']) > 0:
                hindi_result = model.transcribe(audio_path, word_timestamps=True, language='hi', task="transcribe", verbose=False)
                tamil_result = model.transcribe(audio_path, word_timestamps=True, language='ta', task="transcribe", verbose=False)
                
                hindi_text = ' '.join([seg['text'] for seg in hindi_result['segments']])
                tamil_text = ' '.join([seg['text'] for seg in tamil_result['segments']])
                
                hindi_indicators = ['hai', 'hoon', 'mein', 'tum', 'kya', 'aur', 'se', 'ko', 'ka', 'ki', 'ke']
                tamil_indicators = ['tha', 'illa', 'enna', 'naan', 'oru', 'alla', 'irukku']
                
                hindi_score = sum(1 for indicator in hindi_indicators if indicator.lower() in hindi_text.lower())
                tamil_score = sum(1 for indicator in tamil_indicators if indicator.lower() in tamil_text.lower())
                
                if hindi_score > tamil_score:
                    result = hindi_result
                    detected_lang = 'hi'
                elif tamil_score > hindi_score:
                    result = tamil_result
                    detected_lang = 'ta'

        segments = []
        for segment in result["segments"]:
            segments.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"].strip()
            })

        return segments, result.get('language', 'unknown')

    def translate_audio_to_english(self, audio_path):
        model = self.load_whisper_model()
        result = model.transcribe(audio_path, word_timestamps=True, language=None, task="translate", verbose=False)
        
        segments = []
        for segment in result["segments"]:
            segments.append({
                "start": segment["start"],
                "end": segment["end"],
                "text": segment["text"].strip(),
                "translated": segment["text"].strip()
            })
        
        return segments, result.get('language', 'unknown')
    
    def translate_lyrics(self, segments, target_language='en', audio_path=None):
        if not segments or target_language != 'en':
            return segments
        
        if audio_path and Path(audio_path).exists():
            try:
                model = self.load_whisper_model()
                result = model.transcribe(audio_path, word_timestamps=True, language=None, task="translate", verbose=False)
                
                english_segments = []
                for segment in result["segments"]:
                    english_segments.append({
                        "start": segment["start"],
                        "end": segment["end"],
                        "english_text": segment["text"].strip()
                    })
                
                translated_segments = []
                for original_segment in segments:
                    closest_english = None
                    min_time_diff = float('inf')
                    
                    for english_segment in english_segments:
                        time_diff = abs(original_segment['start'] - english_segment['start'])
                        if time_diff < min_time_diff:
                            min_time_diff = time_diff
                            closest_english = english_segment
                    
                    translated_segments.append({
                        "start": original_segment["start"],
                        "end": original_segment["end"],
                        "text": original_segment["text"],
                        "translated": closest_english['english_text'] if closest_english else original_segment["text"]
                    })
                
                return translated_segments
                
            except Exception:
                for segment in segments:
                    segment['translated'] = segment['text']
                return segments
        else:
            for segment in segments:
                segment['translated'] = segment['text']
            return segments

    def save_song_data(self, song_id, title, duration, segments, detected_language, youtube_url):
        song_dir = SONGS_DIR / song_id

        lyrics_path = song_dir / "lyrics.json"
        with open(lyrics_path, 'w', encoding='utf-8') as f:
            json.dump(segments, f, indent=2, ensure_ascii=False)

        metadata = {
            "song_id": song_id,
            "title": title,
            "duration": duration,
            "detected_language": detected_language,
            "youtube_url": youtube_url,
            "created_at": time.time()
        }

        metadata_path = song_dir / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        return metadata

processor = VibeAmpProcessor()

def cleanup_resources():
    global processor
    if processor and processor.whisper_model:
        try:
            processor.whisper_model = None
        except Exception:
            pass

def signal_handler(sig, frame):
    cleanup_resources()
    sys.exit(0)

atexit.register(cleanup_resources)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def load_library():
    if Path(LIBRARY_DB).exists():
        with open(LIBRARY_DB, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []

def save_library(library):
    with open(LIBRARY_DB, 'w', encoding='utf-8') as f:
        json.dump(library, f, indent=2, ensure_ascii=False)

def find_existing_song_by_url(youtube_url):
    library = load_library()
    for song in library:
        if song.get('youtube_url') == youtube_url:
            return song
    return None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/library')
def get_library():
    library = load_library()
    return jsonify(library)

@app.route('/api/process', methods=['POST'])
def process_youtube_url():
    data = request.get_json()
    youtube_url = data.get('url', '').strip()
    translate_to_english = data.get('translate', False)
    force_language = data.get('language', None)

    if not youtube_url:
        return jsonify({"error": "No URL provided"}), 400

    video_id = processor.extract_video_id(youtube_url)
    if not video_id:
        return jsonify({"error": "Invalid YouTube URL"}), 400

    existing_song = find_existing_song_by_url(youtube_url)
    if existing_song:
        return jsonify({
            "success": True,
            "song_id": existing_song["song_id"],
            "metadata": existing_song,
            "already_processed": True,
            "message": "This video has already been processed. Loading existing data..."
        })

    try:
        song_id = str(uuid.uuid4())
        audio_path, title, duration = processor.download_audio(youtube_url, song_id)
        segments, detected_language = processor.transcribe_audio(audio_path, force_language)

        if translate_to_english and detected_language != 'en':
            segments = processor.translate_lyrics(segments, 'en', audio_path)

        metadata = processor.save_song_data(
            song_id, title, duration, segments, detected_language, youtube_url
        )

        library = load_library()
        library.append(metadata)
        save_library(library)

        return jsonify({
            "success": True,
            "song_id": song_id,
            "metadata": metadata
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/process-status/<song_id>')
def get_process_status(song_id):
    return jsonify({"status": "processing", "step": "unknown"})

@app.route('/api/clear-cache', methods=['POST'])
def clear_model_cache():
    try:
        global processor
        processor.whisper_model = None
        success = clear_whisper_cache()
        return jsonify({"success": success, "message": "Cache cleared" if success else "Failed to clear cache"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/api/song/<song_id>')
def get_song_data(song_id):
    song_dir = SONGS_DIR / song_id

    if not song_dir.exists():
        return jsonify({"error": "Song not found"}), 404

    try:
        metadata_path = song_dir / "metadata.json"
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        lyrics_path = song_dir / "lyrics.json"
        with open(lyrics_path, 'r', encoding='utf-8') as f:
            lyrics = json.load(f)

        return jsonify({
            "metadata": metadata,
            "lyrics": lyrics
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/audio/<song_id>')
def serve_audio(song_id):
    song_dir = SONGS_DIR / song_id
    audio_path = song_dir / "audio.mp3"

    if not audio_path.exists():
        return jsonify({"error": "Audio file not found"}), 404

    range_header = request.headers.get('Range', None)
    if not range_header:
        return send_file(audio_path)

    byte_start = 0
    byte_end = None

    if range_header:
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if match:
            byte_start = int(match.group(1))
            if match.group(2):
                byte_end = int(match.group(2))

    file_size = audio_path.stat().st_size

    if byte_end is None:
        byte_end = file_size - 1

    content_length = byte_end - byte_start + 1

    def generate():
        with open(audio_path, 'rb') as audio_file:
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
        }
    )

    return response

if __name__ == '__main__':
    app.run(debug=True, port=8000)