import os
import yt_dlp
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import pickle
import json
import base64
from flask import Flask, request, render_template_string, jsonify
import threading
import time
from pathlib import Path

app = Flask(__name__)

# Google Drive API scopes
SCOPES = ['https://www.googleapis.com/auth/drive.file']

# HTML Template for web interface
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
        h1 {
            color: #333;
            text-align: center;
        }
        .form-group {
            margin-bottom: 20px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            font-weight: bold;
            color: #555;
        }
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
        button:hover {
            background: #0056b3;
        }
        button:disabled {
            background: #ccc;
            cursor: not-allowed;
        }
        .status {
            margin-top: 20px;
            padding: 15px;
            border-radius: 5px;
            display: none;
        }
        .status.active {
            display: block;
        }
        .status.loading {
            background: #e3f2fd;
            border: 1px solid #2196f3;
        }
        .status.success {
            background: #e8f5e9;
            border: 1px solid #4caf50;
        }
        .status.error {
            background: #ffebee;
            border: 1px solid #f44336;
        }
        .link {
            color: #007bff;
            text-decoration: none;
        }
        .link:hover {
            text-decoration: underline;
        }
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
    </style>
</head>
<body>
    <div class="container">
        <h1>🎬 Video to Google Drive Downloader</h1>
        <form id="downloadForm">
            <div class="form-group">
                <label for="url">Video URL</label>
                <input type="text" id="url" placeholder="https://www.youtube.com/watch?v=..." required>
            </div>
            
            <div class="form-group">
                <label for="quality">Quality</label>
                <select id="quality">
                    <option value="bestvideo+bestaudio/best">Best Quality (1080p+)</option>
                    <option value="bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]">720p</option>
                    <option value="bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]">480p</option>
                    <option value="bestaudio/best">Audio Only (MP3)</option>
                </select>
            </div>
            
            <div class="form-group">
                <label for="folder_id">Google Drive Folder ID (Optional)</label>
                <input type="text" id="folder_id" placeholder="Leave empty for root folder">
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
            
            // Reset UI
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
                    const lines = chunk.split('\n');
                    
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

class VideoToDrive:
    def __init__(self):
        self.download_folder = "downloads"
        os.makedirs(self.download_folder, exist_ok=True)
        self.drive_service = self.authenticate_drive()
    
    def authenticate_drive(self):
        """Authenticate with Google Drive API for headless deployment"""
        creds = None
        
        # Check for token in environment variable (Render)
        token_json = os.environ.get('GOOGLE_DRIVE_TOKEN')
        if token_json:
            try:
                token_data = json.loads(base64.b64decode(token_json).decode('utf-8'))
                creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            except:
                pass
        
        # Check for local token file
        if not creds and os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)
        
        # If no valid credentials, use environment variables
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                # Use credentials from environment
                credentials_json = os.environ.get('GOOGLE_CREDENTIALS')
                if not credentials_json:
                    raise Exception("GOOGLE_CREDENTIALS environment variable not set")
                
                # Decode base64 credentials
                credentials_data = json.loads(base64.b64decode(credentials_json).decode('utf-8'))
                
                # Use service account or OAuth2
                if 'client_email' in credentials_data:  # Service account
                    from google.oauth2 import service_account
                    creds = service_account.Credentials.from_service_account_info(
                        credentials_data, scopes=SCOPES
                    )
                else:  # OAuth2
                    flow = InstalledAppFlow.from_client_config(credentials_data, SCOPES)
                    # Use headless auth for Render
                    creds = flow.run_local_server(port=0, open_browser=False)
            
            # Save credentials if possible
            try:
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)
            except:
                pass
        
        return build('drive', 'v3', credentials=creds)
    
    def download_video(self, url, quality="best", progress_callback=None):
        """Download video using yt-dlp with progress tracking"""
        if progress_callback:
            progress_callback('status', f"Starting download: {url}")
        
        ydl_opts = {
            'outtmpl': os.path.join(self.download_folder, '%(title)s.%(ext)s'),
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
        except:
            pass
    
    def process_video(self, url, quality="best", folder_id=None, callback=None):
        """Complete workflow: download -> upload -> cleanup"""
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
            if 'video_path' in locals() and video_path:
                self.cleanup(video_path)
            raise e

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/download', methods=['POST'])
def download():
    def generate():
        url = request.form.get('url')
        quality = request.form.get('quality', 'bestvideo+bestaudio/best')
        folder_id = request.form.get('folder_id', '')
        
        if not url:
            yield f"data: {json.dumps({'type': 'error', 'message': 'No URL provided'})}\n\n"
            return
        
        try:
            downloader = VideoToDrive()
            
            def callback(callback_type, message, percent=0):
                if callback_type == 'progress':
                    yield f"data: {json.dumps({'type': 'progress', 'status': message, 'percent': round(percent, 1)})}\n\n"
                elif callback_type == 'status':
                    yield f"data: {json.dumps({'type': 'status', 'message': message})}\n\n"
            
            # Process video with streaming updates
            result = downloader.process_video(url, quality, folder_id, callback)
            
            yield f"data: {json.dumps({'type': 'complete', 'message': f'Upload complete: {result["file_name"]}', 'link': result["link"]})}\n\n"
            
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
    
    return app.response_class(generate(), mimetype='text/event-stream')

if __name__ == "__main__":
    # Get port from environment for Render
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)