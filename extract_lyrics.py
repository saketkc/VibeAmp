#!/usr/bin/env python3
import os
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'
import whisper
import json
import sys
from pathlib import Path

def extract_lyrics_with_timestamps(audio_file_path, output_format="html"):
    model = whisper.load_model("large-v3")
    result = model.transcribe(audio_file_path, word_timestamps=True, language=None, task="transcribe", verbose=False)
    
    lyrics_data = []
    for segment in result["segments"]:
        lyrics_data.append({
            "start": segment["start"],
            "end": segment["end"],
            "text": segment["text"].strip()
        })
    
    if output_format == "html":
        return generate_html_lyrics(lyrics_data)
    else:
        return json.dumps(lyrics_data, indent=2)

def generate_html_lyrics(lyrics_data):
    html_lines = []
    
    for segment in lyrics_data:
        start_time = int(segment["start"])
        text = segment["text"]
        minutes = start_time // 60
        seconds = start_time % 60
        timestamp_display = f"[{minutes}:{seconds:02d}]"
        
        html_line = f'''            <div class="lyric-line" data-timestamp="{start_time}">
                <span class="timestamp">{timestamp_display}</span>
                <span class="lyric-text">{text}</span>
            </div>'''
        html_lines.append(html_line)
    
    return '\n'.join(html_lines)

def update_html_file(lyrics_html, html_file_path="lyrics-sync-app.html"):
    try:
        with open(html_file_path, 'r') as file:
            content = file.read()
        
        start_marker = '<div class="lyrics-container" id="lyricsContainer">'
        start_index = content.find(start_marker)
        if start_index == -1:
            return False
            
        start_index += len(start_marker)
        end_index = content.find('    </div>\n\n    <script>', start_index)
        
        if end_index == -1:
            return False
        
        new_content = (content[:start_index] + '\n' + 
                      lyrics_html + '\n        ' + 
                      content[end_index:])
        
        with open(html_file_path, 'w') as file:
            file.write(new_content)
        
        return True
        
    except Exception:
        return False

def main():
    if len(sys.argv) < 2:
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else "html"
    
    if not Path(audio_file).exists():
        sys.exit(1)
    
    try:
        result = extract_lyrics_with_timestamps(audio_file, output_format)
        
        if output_format == "html":
            if not update_html_file(result):
                print(result)
        else:
            print(result)
            
    except Exception:
        sys.exit(1)

if __name__ == "__main__":
    main()