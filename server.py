#!/usr/bin/env python3
import http.server
import socketserver
import os
from urllib.parse import unquote

class RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        super().end_headers()
    
    def do_GET(self):
        # Handle range requests for better audio streaming
        if 'Range' in self.headers:
            self.handle_range_request()
        else:
            super().do_GET()
    
    def handle_range_request(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) or os.path.isdir(path):
            self.send_error(404, "File not found")
            return
        
        file_size = os.path.getsize(path)
        range_header = self.headers['Range']
        
        # Parse range header
        if not range_header.startswith('bytes='):
            self.send_error(416, "Range not satisfiable")
            return
        
        ranges = range_header[6:].split(',')[0]  # Take first range only
        if '-' not in ranges:
            self.send_error(416, "Range not satisfiable")
            return
        
        start, end = ranges.split('-', 1)
        start = int(start) if start else 0
        end = int(end) if end else file_size - 1
        
        if start >= file_size or end >= file_size or start > end:
            self.send_error(416, "Range not satisfiable")
            return
        
        # Send partial content
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', f'bytes {start}-{end}/{file_size}')
        self.send_header('Content-Length', str(end - start + 1))
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()
        
        with open(path, 'rb') as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk_size = min(8192, remaining)
                data = f.read(chunk_size)
                if not data:
                    break
                self.wfile.write(data)
                remaining -= len(data)

if __name__ == "__main__":
    PORT = 8001
    with socketserver.TCPServer(("", PORT), RangeRequestHandler) as httpd:
        print(f"Server running at http://localhost:{PORT}/")
        httpd.serve_forever()