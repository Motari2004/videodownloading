import os
import sys
import yt_dlp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle
import json
import base64
import threading
import time
from pathlib import Path
import webbrowser
import tempfile

# Try to import Flask, fallback to CLI mode if not available
try:
    from flask import Flask, request, render_template_string, jsonify, Response, stream_with_context
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    print("⚠️ Flask not installed. Running in CLI mode.")
    print("   Install Flask with: pip install flask")

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# Global variables
app = None
if FLASK_AVAILABLE:
    app = Flask(__name__)

class VideoToDrive:
    def __init__(self):
        self.download_folder = "downloads"
        os.makedirs(self.download_folder, exist_ok=True)
        self.drive_service = self.authenticate_drive()
        self.is_render = os.environ.get('RENDER') == 'true'
    
    def authenticate_drive(self):
        """Authenticate with Google Drive API - works everywhere"""
        creds = None
        
        # 1. Try environment variable (Render deployment)
        token_json = os.environ.get('GOOGLE_DRIVE_TOKEN')
        if token_json:
            try:
                token_data = json.loads(base64.b64decode(token_json).decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
                print("✅ Authenticated using GOOGLE_DRIVE_TOKEN from env")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                print(f"⚠️ Failed to use GOOGLE_DRIVE_TOKEN: {e}")
        
        # 2. Try local token file (Desktop/Local)
        if os.path.exists('token.pickle'):
            try:
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)
                print("✅ Authenticated using token.pickle")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                print(f"⚠️ Failed to load token.pickle: {e}")
        
        # 3. Try credentials.json for interactive auth (Desktop/Local)
        if os.path.exists('credentials.json'):
            try:
                flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)
                print("✅ Authenticated using credentials.json")
                
                # Save token for future use
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
                print("💾 Token saved to token.pickle for future use")
                return build('drive', 'v3', credentials=creds)
            except Exception as e:
                print(f"⚠️ Failed to authenticate with credentials.json: {e}")
        
        # 4. Try environment credentials (Render)
        credentials_json = os.environ.get('GOOGLE_CREDENTIALS')
        if credentials_json:
            try:
                credentials_data = json.loads(base64.b64decode(credentials_json).decode('utf-8'))
                if 'client_email' in credentials_data:  # Service account
                    from google.oauth2 import service_account
                    creds = service_account.Credentials.from_service_account_info(
                        credentials_data, scopes=SCOPES
                    )
                    print("✅ Authenticated using service account from env")
                    return build('drive', 'v3', credentials=creds)
                else:  # OAuth2
                    flow = InstalledAppFlow.from_client_config(credentials_data, SCOPES)
                    creds = flow.run_local_server(port=0, open_browser=False)
                    print("✅ Authenticated using OAuth2 from env")
                    return build('drive', 'v3', credentials=creds)
            except Exception as e:
                print(f"⚠️ Failed to authenticate with GOOGLE_CREDENTIALS: {e}")
        
        # 5. If nothing works, ask for credentials.json
        print("\n❌ No valid credentials found!")
        print("Options:")
        print("1. Place credentials.json in the app folder")
        print("2. Set GOOGLE_CREDENTIALS and GOOGLE_DRIVE_TOKEN environment variables")
        print("3. Run: python generate_token.py to create token.pickle")
        print("\nPress Enter to exit...")
        input()
        sys.exit(1)
    
    def download_video(self, url, quality="best", progress_callback=None):
        """Download video using yt-dlp with progress tracking"""
        if progress_callback:
            progress_callback('status', f"Starting download: {url}")
        
        # Determine output template based on environment
        if self.is_render:
            # Use temp directory on Render
            outtmpl = os.path.join(tempfile.gettempdir(), '%(title)s.%(ext)s')
        else:
            outtmpl = os.path.join(self.download_folder, '%(title)s.%(ext)s')
        
        ydl_opts = {
            'outtmpl': outtmpl,
            'format': quality,
            'quiet': True,
            'no_warnings': True,
            'progress_hooks': [lambda d: self.progress_hook(d, progress_callback)],
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                return filename
        except Exception as e:
            raise Exception(f"Download error: {str(e)}")
    
    def progress_hook(self, d, callback=None):
        """Show download progress with callback"""
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                percent = (d['downloaded_bytes'] / d['total_bytes']) * 100
                speed = d.get('speed', 0)
                if speed:
                    speed_mb = speed / 1024 / 1024
                    status_text = f"Downloading: {percent:.1f}% | Speed: {speed_mb:.2f} MB/s"
                else:
                    status_text = f"Downloading: {percent:.1f}%"
                
                if callback:
                    callback('progress', status_text, percent)
        elif d['status'] == 'finished':
            if callback:
                callback('status', "Download finished, processing...")
    
    def upload_to_drive(self, file_path, folder_id=None, progress_callback=None):
        """Upload file to Google Drive"""
        if not file_path or not os.path.exists(file_path):
            raise Exception("File not found")
        
        if progress_callback:
            progress_callback('status', f"Uploading: {os.path.basename(file_path)}")
        
        file_metadata = {
            'name': os.path.basename(file_path)
        }
        
        if folder_id and folder_id.strip():
            file_metadata['parents'] = [folder_id.strip()]
        
        media = MediaFileUpload(
            file_path,
            mimetype='video/mp4',
            resumable=True
        )
        
        try:
            file = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id, name, webViewLink'
            ).execute()
            return file
        except Exception as e:
            raise Exception(f"Upload error: {str(e)}")
    
    def cleanup(self, file_path):
        """Delete local file after upload"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                print(f"🗑️ Deleted: {os.path.basename(file_path)}")
        except:
            pass
    
    def process_video(self, url, quality="best", folder_id=None, callback=None):
        """Complete workflow: download -> upload -> cleanup"""
        video_path = None
        try:
            # Step 1: Download
            video_path = self.download_video(url, quality, callback)
            
            # Step 2: Upload to Drive
            file = self.upload_to_drive(video_path, folder_id, callback)
            
            # Step 3: Cleanup
            self.cleanup(video_path)
            
            return {
                'success': True,
                'file_name': file.get('name'),
                'file_id': file.get('id'),
                'link': file.get('webViewLink')
            }
        except Exception as e:
            # Cleanup on error
            if video_path:
                self.cleanup(video_path)
            raise e

# ==================== CLI MODE ====================
def run_cli():
    """Run in command-line mode (for desktop/local use)"""
    print("🎬 Video to Google Drive Downloader (CLI Mode)")
    print("="*50)
    
    try:
        downloader = VideoToDrive()
        
        url = input("Enter video URL: ").strip()
        if not url:
            print("❌ No URL provided")
            return
        
        print("\nQuality options:")
        print("1. Best Quality (1080p+)")
        print("2. 720p")
        print("3. 480p")
        print("4. Audio Only (MP3)")
        
        choice = input("Choose quality (1-4): ").strip()
        
        quality_map = {
            "1": "bestvideo+bestaudio/best",
            "2": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]",
            "3": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]",
            "4": "bestaudio/best",
        }
        quality = quality_map.get(choice, "bestvideo+bestaudio/best")
        
        def progress_callback(callback_type, message, percent=0):
            if callback_type == 'progress':
                print(f"\r{message}", end='')
            elif callback_type == 'status':
                print(f"\n{message}")
        
        print("\n📥 Starting download...")
        result = downloader.process_video(url, quality, None, progress_callback)
        
        print("\n" + "="*50)
        print(f"✅ Upload complete: {result['file_name']}")
        print(f"🔗 View in Drive: {result['link']}")
        print("="*50)
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
    
    input("\nPress Enter to exit...")

# ==================== WEB MODE ====================
if FLASK_AVAILABLE:
    HTML_TEMPLATE = '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>Video to Google Drive Downloader</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 800px;
                margin: 50px auto;
                padding: 20px;
                background: #f5f5f5;
            }
            .container {
                background: white;
                padding: 30px;
                border-radius: 10px;
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #333; text-align: center; }
            .form-group { margin-bottom: 20px; }
            label { display: block; margin-bottom: 5px; font-weight: bold; color: #555; }
            input, select {
                width: 100%;
                padding: 10px;
                border: 1px solid #ddd;
                border-radius: 5px;
                font-size: 16px;
                box-sizing: border-box;
            }
            button {
                background: #007bff;
                color: white;
                padding: 12px 30px;
                border: none;
                border-radius: 5px;
                font-size: 16px;
                cursor: pointer;
                width: 100%;
            }
            button:hover { background: #0056b3; }
            button:disabled { background: #ccc; cursor: not-allowed; }
            .status {
                margin-top: 20px;
                padding: 15px;
                border-radius: 5px;
                display: none;
            }
            .status.active { display: block; }
            .status.loading { background: #e3f2fd; border: 1px solid #2196f3; }
            .status.success { background: #e8f5e9; border: 1px solid #4caf50; }
            .status.error { background: #ffebee; border: 1px solid #f44336; }
            .link { color: #007bff; text-decoration: none; }
            .link:hover { text-decoration: underline; }
            .spinner {
                display: inline-block;
                width: 20px;
                height: 20px;
                border: 3px solid #f3f3f3;
                border-top: 3px solid #007bff;
                border-radius: 50%;
                animation: spin 1s linear infinite;
                margin-right: 10px;
            }
            @keyframes spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            .progress-bar {
                width: 100%;
                background: #f0f0f0;
                border-radius: 5px;
                margin: 10px 0;
                overflow: hidden;
            }
            .progress-fill {
                height: 20px;
                background: #007bff;
                transition: width 0.5s;
                width: 0%;
            }
            .mode-badge {
                display: inline-block;
                padding: 4px 12px;
                border-radius: 20px;
                font-size: 12px;
                color: white;
                margin-left: 10px;
            }
            .badge-render { background: #28a745; }
            .badge-local { background: #6c757d; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🎬 Video to Google Drive Downloader
                <span class="mode-badge {% if is_render %}badge-render{% else %}badge-local{% endif %}">
                    {{ 'Render' if is_render else 'Local' }}
                </span>
            </h1>
            <p style="text-align:center;color:#666;">Download videos and upload directly to Google Drive</p>
            
            <form id="downloadForm">
                <div class="form-group">
                    <label for="url">Video URL</label>
                    <input type="text" id="url" name="url" placeholder="https://www.youtube.com/watch?v=..." required>
                </div>
                
                <div class="form-group">
                    <label for="quality">Quality</label>
                    <select id="quality" name="quality">
                        <option value="bestvideo+bestaudio/best">Best Quality (1080p+)</option>
                        <option value="bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]">720p</option>
                        <option value="bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]">480p</option>
                        <option value="bestaudio/best">Audio Only (MP3)</option>
                    </select>
                </div>
                
                <div class="form-group">
                    <label for="folder_id">Google Drive Folder ID (Optional)</label>
                    <input type="text" id="folder_id" name="folder_id" placeholder="Leave empty for root folder">
                </div>
                
                <button type="submit" id="submitBtn">Download & Upload to Drive</button>
            </form>
            
            <div id="status" class="status">
                <div id="statusContent"></div>
            </div>
            
            <div id="progressContainer" style="display: none;">
                <div class="progress-bar">
                    <div id="progressFill" class="progress-fill"></div>
                </div>
                <div id="progressText">0%</div>
            </div>
        </div>

        <script>
            document.getElementById('downloadForm').addEventListener('submit', async function(e) {
                e.preventDefault();
                
                const submitBtn = document.getElementById('submitBtn');
                const statusDiv = document.getElementById('status');
                const statusContent = document.getElementById('statusContent');
                const progressContainer = document.getElementById('progressContainer');
                const progressFill = document.getElementById('progressFill');
                const progressText = document.getElementById('progressText');
                
                statusDiv.className = 'status';
                statusDiv.style.display = 'none';
                progressContainer.style.display = 'none';
                submitBtn.disabled = true;
                submitBtn.textContent = 'Processing...';
                
                const formData = new FormData(this);
                
                try {
                    const response = await fetch('/download', {
                        method: 'POST',
                        body: formData
                    });
                    
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    
                    while (true) {
                        const {done, value} = await reader.read();
                        if (done) break;
                        
                        const chunk = decoder.decode(value);
                        const lines = chunk.split('\\n');
                        
                        for (const line of lines) {
                            if (line.startsWith('data: ')) {
                                const data = JSON.parse(line.substring(6));
                                
                                if (data.type === 'progress') {
                                    progressContainer.style.display = 'block';
                                    progressFill.style.width = data.percent + '%';
                                    progressText.textContent = data.percent + '% - ' + data.status;
                                } else if (data.type === 'status') {
                                    statusDiv.className = 'status active loading';
                                    statusDiv.style.display = 'block';
                                    statusContent.innerHTML = '<span class="spinner"></span>' + data.message;
                                } else if (data.type === 'complete') {
                                    statusDiv.className = 'status active success';
                                    statusDiv.style.display = 'block';
                                    statusContent.innerHTML = '✅ ' + data.message + 
                                        '<br>🔗 <a href="' + data.link + '" target="_blank" class="link">View in Google Drive</a>';
                                    progressContainer.style.display = 'none';
                                } else if (data.type === 'error') {
                                    statusDiv.className = 'status active error';
                                    statusDiv.style.display = 'block';
                                    statusContent.innerHTML = '❌ ' + data.message;
                                    progressContainer.style.display = 'none';
                                }
                            }
                        }
                    }
                } catch (error) {
                    statusDiv.className = 'status active error';
                    statusDiv.style.display = 'block';
                    statusContent.innerHTML = '❌ Error: ' + error.message;
                } finally {
                    submitBtn.disabled = false;
                    submitBtn.textContent = 'Download & Upload to Drive';
                }
            });
        </script>
    </body>
    </html>
    '''

    @app.route('/')
    def index():
        is_render = os.environ.get('RENDER') == 'true'
        return render_template_string(HTML_TEMPLATE, is_render=is_render)

    @app.route('/download', methods=['POST'])
    def download():
        # Get form data first - THIS IS CRITICAL
        url = request.form.get('url')
        quality = request.form.get('quality', 'bestvideo+bestaudio/best')
        folder_id = request.form.get('folder_id', '')
        
        print(f"🔍 Received URL: {url}")
        print(f"📊 Quality: {quality}")
        print(f"📁 Folder: {folder_id}")
        
        if not url:
            def error_gen():
                yield f"data: {json.dumps({'type': 'error', 'message': 'No URL provided. Please enter a video URL.'})}\n\n"
            return Response(error_gen(), mimetype='text/event-stream')
        
        @stream_with_context
        def generate():
            try:
                downloader = VideoToDrive()
                result = downloader.process_video(url, quality, folder_id, None)
                yield f"data: {json.dumps({'type': 'complete', 'message': f'Upload complete: {result["file_name"]}', 'link': result["link"]})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        
        return Response(generate(), mimetype='text/event-stream')

# ==================== MAIN ====================
def main():
    # Determine which mode to run
    if len(sys.argv) > 1:
        if sys.argv[1] == '--cli' or sys.argv[1] == '-c':
            run_cli()
            return
        elif sys.argv[1] == '--web' or sys.argv[1] == '-w':
            if not FLASK_AVAILABLE:
                print("❌ Flask not installed. Install with: pip install flask")
                return
            port = int(os.environ.get('PORT', 5000))
            app.run(host='0.0.0.0', port=port, debug=True)
            return
    
    # Auto-detect mode
    if os.environ.get('RENDER') == 'true' or os.environ.get('PORT'):
        # Running on Render - web mode
        if FLASK_AVAILABLE:
            port = int(os.environ.get('PORT', 5000))
            app.run(host='0.0.0.0', port=port, debug=False)
        else:
            print("❌ Flask not installed on Render! Install with: pip install flask")
    elif FLASK_AVAILABLE:
        # Local - check if we have a browser
        try:
            # Try to open web interface
            port = 5000
            print("🌐 Starting web interface...")
            print(f"📱 Open in browser: http://localhost:{port}")
            webbrowser.open(f"http://localhost:{port}")
            app.run(host='0.0.0.0', port=port, debug=True)
        except:
            # Fallback to CLI
            run_cli()
    else:
        # No Flask - CLI only
        run_cli()

if __name__ == "__main__":
    main()