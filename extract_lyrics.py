#!/usr/bin/env python3
"""
Script to extract lyrics with timestamps from audio using Whisper AI
"""

import os
# Set OpenMP environment variable to avoid conflicts
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import whisper
import json
import sys
from pathlib import Path

def extract_lyrics_with_timestamps(audio_file_path, output_format="html"):
    """
    Extract lyrics with timestamps from audio file using Whisper
    
    Args:
        audio_file_path (str): Path to the audio file
        output_format (str): Output format - 'html' or 'json'
    """
    print("Loading Whisper model...")
    # Use the 'large-v3' model for best multilingual accuracy
    # Options: tiny, base, small, medium, large, large-v2, large-v3
    model = whisper.load_model("large-v3")
    
    print("Transcribing audio file...")
    # For Hindi/English mix, let Whisper auto-detect or specify language
    result = model.transcribe(audio_file_path, 
                            word_timestamps=True,
                            language=None,  # Auto-detect language
                            task="transcribe",
                            verbose=True)
    
    lyrics_data = []
    
    # Extract segments with timestamps
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
    """Generate HTML lyrics section for the sync app"""
    html_lines = []
    
    for i, segment in enumerate(lyrics_data):
        start_time = int(segment["start"])
        text = segment["text"]
        
        # Format timestamp for display
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
    """Update the HTML file with extracted lyrics"""
    try:
        with open(html_file_path, 'r') as file:
            content = file.read()
        
        # Find the lyrics container section
        start_marker = '<div class="lyrics-container" id="lyricsContainer">'
        end_marker = '</div>'
        
        start_index = content.find(start_marker)
        if start_index == -1:
            print("Error: Could not find lyrics container in HTML file")
            return False
            
        # Find the end of the container
        start_index += len(start_marker)
        end_index = content.find('    </div>\n\n    <script>', start_index)
        
        if end_index == -1:
            print("Error: Could not find end of lyrics container")
            return False
        
        # Replace the content
        new_content = (content[:start_index] + '\n' + 
                      lyrics_html + '\n        ' + 
                      content[end_index:])
        
        with open(html_file_path, 'w') as file:
            file.write(new_content)
        
        print(f"Successfully updated {html_file_path} with extracted lyrics")
        return True
        
    except Exception as e:
        print(f"Error updating HTML file: {e}")
        return False

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_lyrics.py <audio_file_path> [output_format]")
        print("Output formats: html (default), json")
        sys.exit(1)
    
    audio_file = sys.argv[1]
    output_format = sys.argv[2] if len(sys.argv) > 2 else "html"
    
    if not Path(audio_file).exists():
        print(f"Error: Audio file '{audio_file}' not found")
        sys.exit(1)
    
    try:
        print(f"Processing audio file: {audio_file}")
        result = extract_lyrics_with_timestamps(audio_file, output_format)
        
        if output_format == "html":
            # Update the HTML file directly
            if update_html_file(result):
                print("\nLyrics successfully extracted and added to lyrics-sync-app.html!")
                print("You can now open the HTML file in a browser and load your audio file.")
            else:
                # Fallback: just print the HTML
                print("\nGenerated HTML lyrics:")
                print(result)
        else:
            print("\nExtracted lyrics (JSON format):")
            print(result)
            
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
