# VibeAmp

VibeAmp is a music transcription and translation web app that downloads audio from YouTube URLs, transcribes lyrics using OpenAI's Whisper model, and provides synchronized playback with optional English translation.


## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Run the Flask application:
```bash
python app.py
```

3. Open your browser to `http://localhost:8000`

## Usage

1. Enter a YouTube URL in the web interface
2. Optionally select language (or use auto-detection)
3. Choose whether to include English translation
4. Click "Process Song" to transcribe
5. Play the audio with synchronized lyrics



## Requirements

- Python 3.8+
- FFmpeg (for audio processing)
- Internet connection (for YouTube downloads and Whisper model)
