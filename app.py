import os
import subprocess
from flask import Flask, request, render_template, send_file, jsonify
import yt_dlp
import uuid
from pathlib import Path
import re

app = Flask(__name__)

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

progress_store = {}

def is_direct_video_url(url):
    """Проверка, является ли URL прямой ссылкой на видео файл"""
    video_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.m3u8']
    return any(url.lower().endswith(ext) or ext in url.lower() for ext in video_extensions)

def progress_hook(d, task_id):
    """Хук для отслеживания прогресса скачивания"""
    if d['status'] == 'downloading':
        if 'total_bytes' in d:
            download_progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
        elif 'total_bytes_estimate' in d:
            download_progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
        else:
            download_progress = 50
        
        overall_progress = min(download_progress * 0.6, 60)
        
        if download_progress < 20:
            status = 'Скачивание: начало загрузки...'
        elif download_progress < 40:
            status = 'Скачивание: 20% завершено'
        elif download_progress < 60:
            status = 'Скачивание: 40% завершено'
        elif download_progress < 80:
            status = 'Скачивание: 60% завершено'
        elif download_progress < 95:
            status = 'Скачивание: 80% завершено'
        else:
            status = 'Скачивание: завершение...'
        
        progress_store[task_id] = {
            'progress': overall_progress,
            'status': status,
            'download_percent': round(download_progress, 1)
        }
    elif d['status'] == 'finished':
        progress_store[task_id] = {
            'progress': 60,
            'status': 'Скачивание завершено (100%)',
            'download_percent': 100
        }

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert_video():
    unique_id = None
    try:
        data = request.json
        video_url = data.get('video_url')
        start_time = int(data.get('start_time', 10))
        duration = int(data.get('duration', 3))
        
        if duration < 1:
            duration = 1
        if duration > 10:
            duration = 10
        
        if not video_url:
            return jsonify({'error': 'URL видео не указан'}), 400
        
        unique_id = str(uuid.uuid4())
        video_path = TEMP_DIR / f"{unique_id}.mp4"
        gif_path = TEMP_DIR / f"{unique_id}.gif"
        
        progress_store[unique_id] = {'progress': 0, 'status': 'Подготовка...', 'download_percent': 0}
        
        if is_direct_video_url(video_url):
            print(f"Прямая ссылка на видео обнаружена: {video_url}")
                        
            buffer_before = max(0, start_time - 2)
            total_duration = duration + 4
                        
            download_cmd = [
                'ffmpeg',
                '-ss', str(buffer_before),
                '-to', str(buffer_before + total_duration),
                '-i', video_url,
                '-c', 'copy',
                str(video_path),
                '-y'
            ]
            
            progress_store[unique_id] = {'progress': 20, 'status': 'Скачивание: 20% завершено', 'download_percent': 30}
            result = subprocess.run(download_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Ошибка скачивания: {result.stderr}")
                del progress_store[unique_id]
                return jsonify({'error': 'Не удалось скачать видео'}), 500
            
            progress_store[unique_id] = {'progress': 60, 'status': 'Скачивание завершено (100%)', 'download_percent': 100}
        else:
            video_path_template = TEMP_DIR / f"{unique_id}.%(ext)s"
            
            postprocessors = [
                {'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'},
                {'key': 'Exec', 'exec_cmd': f'ffmpeg -ss {max(0, start_time - 2)} -to {start_time - 2 + duration + 4} -i "%(filepath)s" -c copy -y "%(filepath)s.trimmed.mp4" && mv "%(filepath)s.trimmed.mp4" "%(filepath)s"'}
            ]
            
            ydl_opts = {
                'format': 'best[ext=mp4]/best',
                'outtmpl': str(video_path_template),
                'quiet': False,
                'no_warnings': False,
                'geo_bypass': True,
                'nocheckcertificate': True,
                'progress_hooks': [lambda d: progress_hook(d, unique_id)],
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                },
                'postprocessors': postprocessors,
            }
            
            progress_store[unique_id] = {'progress': 2, 'status': 'Подключение к серверу...', 'download_percent': 0}
            print(f"Скачивание видео: {video_url}")
            
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                    print(f"Видео скачано: {info.get('title', 'Unknown')}")
            except Exception as dl_error:
                error_msg = str(dl_error)
                print(f"Ошибка: {error_msg}")
                del progress_store[unique_id]
                return jsonify({'error': f'Не удалось скачать видео: {error_msg}'}), 500
            
            possible_files = list(TEMP_DIR.glob(f"{unique_id}.*"))
            video_files = [f for f in possible_files if f.suffix.lower() in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv']]
            
            if not video_files:
                del progress_store[unique_id]
                return jsonify({'error': 'Файл не найден'}), 500
            
            video_path = video_files[0]
        
        print(f"Используется видео: {video_path}")
        
        progress_store[unique_id] = {'progress': 70, 'status': 'Обработка видео...', 'download_percent': 100}
        
        # Calculate the seek position within the trimmed video
        # The video was trimmed starting at (start_time - 2), so we need to seek 2 seconds into it
        buffer_before = max(0, start_time - 2)
        gif_seek_time = start_time - buffer_before  # This gives us the offset within the trimmed video
        
        # GIF creation
        ffmpeg_cmd = [
            'ffmpeg',
            '-ss', str(gif_seek_time),
            '-t', str(duration),
            '-i', str(video_path),
            '-vf', 'fps=15,scale=480:-1:flags=lanczos',
            '-loop', '0',
            str(gif_path),
            '-y'
        ]
        
        progress_store[unique_id] = {'progress': 80, 'status': 'Конвертация в GIF...', 'download_percent': 100}
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            print(f"Ошибка: {result.stderr}")
            del progress_store[unique_id]
            return jsonify({'error': f'Ошибка создания GIF'}), 500
        
        progress_store[unique_id] = {'progress': 95, 'status': 'Финализация...', 'download_percent': 100}
        video_path.unlink(missing_ok=True)
        
        progress_store[unique_id] = {'progress': 100, 'status': 'Готово! GIF создан', 'download_percent': 100}
        
        return jsonify({
            'success': True,
            'gif_id': unique_id,
            'message': 'GIF успешно создан!'
        })
        
    except Exception as e:
        print(f"Ошибка: {str(e)}")
        if unique_id and unique_id in progress_store:
            del progress_store[unique_id]
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

@app.route('/progress/<task_id>')
def get_progress(task_id):
    if task_id in progress_store:
        return jsonify(progress_store[task_id])
    return jsonify({'progress': 0, 'status': 'Неизвестная задача', 'download_percent': 0})

@app.route('/download/<gif_id>')
def download_gif(gif_id):
    gif_path = TEMP_DIR / f"{gif_id}.gif"
    if not gif_path.exists():
        return "GIF не найден", 404
    if gif_id in progress_store:
        del progress_store[gif_id]
    return send_file(gif_path, as_attachment=True, download_name='video.gif')

@app.route('/cleanup/<gif_id>', methods=['POST'])
def cleanup(gif_id):
    for f in TEMP_DIR.glob(f"{gif_id}*"):
        f.unlink(missing_ok=True)
    if gif_id in progress_store:
        del progress_store[gif_id]
    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5500)
