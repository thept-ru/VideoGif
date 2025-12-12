import os
import subprocess
from flask import Flask, request, render_template, send_file, jsonify
import yt_dlp
import uuid
from pathlib import Path
import re
import threading
import time

app = Flask(__name__)

TEMP_DIR = Path("temp")
TEMP_DIR.mkdir(exist_ok=True)

progress_store = {}
lock = threading.Lock()

def update_progress(task_id, progress, status, download_percent=None):
    """Thread-safe progress update"""
    with lock:
        if task_id in progress_store:
            progress_store[task_id]['progress'] = progress
            progress_store[task_id]['status'] = status
            if download_percent is not None:
                progress_store[task_id]['download_percent'] = download_percent

def is_direct_video_url(url):
    """Проверка, является ли URL прямой ссылкой на видео файл"""
    video_extensions = ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv', '.m3u8']
    return any(url.lower().endswith(ext) or ext in url.lower() for ext in video_extensions)

def is_vk_video(url):
    """Проверка, является ли URL ссылкой на VK Video"""
    return 'vkvideo.ru' in url.lower() or 'vk.com/video' in url.lower()

def progress_hook(d, task_id):
    """Хук для отслеживания прогресса скачивания"""
    if d['status'] == 'downloading':
        if 'total_bytes' in d:
            download_progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
        elif 'total_bytes_estimate' in d:
            download_progress = (d['downloaded_bytes'] / d['total_bytes_estimate']) * 100
        else:
            download_progress = 50
        
        # Overall progress: 0-60% for download phase
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
        
        update_progress(task_id, overall_progress, status, round(download_progress, 1))
    elif d['status'] == 'finished':
        update_progress(task_id, 60, 'Скачивание завершено (100%)', 100)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/convert', methods=['POST'])
def convert_video():
    """Start video conversion in background and return task ID immediately"""
    try:
        data = request.json
        video_url = data.get('video_url')
        start_time = int(data.get('start_time', 10))
        duration = int(data.get('duration', 3))
        vk_username = data.get('vk_username')
        vk_password = data.get('vk_password')
        
        if duration < 1:
            duration = 1
        if duration > 10:
            duration = 10
        
        if not video_url:
            return jsonify({'error': 'Указаны не все параметры'}), 400
        
        unique_id = str(uuid.uuid4())
        
        # Initialize progress
        progress_store[unique_id] = {'progress': 0, 'status': 'Подготовка...', 'download_percent': 0}
        
        # Start processing in background thread
        thread = threading.Thread(
            target=process_video_task,
            args=(unique_id, video_url, start_time, duration, vk_username, vk_password)
        )
        thread.daemon = True
        thread.start()
        
        # Return task ID immediately
        return jsonify({
            'success': True,
            'gif_id': unique_id,
            'message': 'Обработка началась'
        })
        
    except Exception as e:
        print(f"Ошибка: {str(e)}")
        return jsonify({'error': f'Ошибка: {str(e)}'}), 500

def process_video_task(unique_id, video_url, start_time, duration, vk_username=None, vk_password=None):
    """Background task for video processing"""
    try:
        video_path = TEMP_DIR / f"{unique_id}.mp4"
        gif_path = TEMP_DIR / f"{unique_id}.gif"
        
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
            
            update_progress(unique_id, 20, 'Скачивание: 20% завершено', 30)
            result = subprocess.run(download_cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                print(f"Ошибка скачивания: {result.stderr}")
                del progress_store[unique_id]
                return
            
            update_progress(unique_id, 60, 'Скачивание завершено (100%)', 100)
        else:
            video_path_template = TEMP_DIR / f"{unique_id}.%(ext)s"
            
            # Calculate download range with buffer
            buffer_before = max(0, start_time - 2)
            buffer_after = duration + 4
            download_start = buffer_before
            download_end = buffer_before + buffer_after
            
            print(f"Диапазон загрузки: {download_start}s - {download_end}s (всего {download_end - download_start}s вместо полного видео)")
            
            # Use download_ranges to download only needed segment
            from yt_dlp.utils import download_range_func
            
            # Special handling for VK videos
            if is_vk_video(video_url):
                print(f"Обнаружено VK видео, используем специальные настройки")
                ydl_opts = {
                    'format': 'best',
                    'outtmpl': str(video_path_template),
                    'quiet': False,
                    'no_warnings': False,
                    'progress_hooks': [lambda d: progress_hook(d, unique_id)],
                    'nocheckcertificate': True,
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Referer': 'https://vk.com/',
                        'Origin': 'https://vk.com',
                        'Sec-Fetch-Dest': 'document',
                        'Sec-Fetch-Mode': 'navigate',
                        'Sec-Fetch-Site': 'none',
                    },
                    'download_ranges': download_range_func(None, [(download_start, download_end)]),
                    'force_keyframes_at_cuts': True,
                    'extractor_args': {
                        'vk': {
                            'is_authorized': True,
                        }
                    },
                }
                
                # Add VK credentials if provided
                if vk_username and vk_password:
                    print(f"Используем предоставленные данные VK для авторизации")
                    ydl_opts['username'] = vk_username
                    ydl_opts['password'] = vk_password
            else:
                ydl_opts = {
                    'format': 'best[ext=mp4]/best',
                    'outtmpl': str(video_path_template),
                    'quiet': False,
                    'no_warnings': False,
                    'geo_bypass': True,
                    'nocheckcertificate': True,
                    'progress_hooks': [lambda d: progress_hook(d, unique_id)],
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                        'Accept-Encoding': 'gzip, deflate, br',
                        'Referer': 'https://vk.com/',
                    },
                    'download_ranges': download_range_func(None, [(download_start, download_end)]),
                    'force_keyframes_at_cuts': True,
                    'extractor_args': {'vk': {'allow_unplayable_formats': True}},
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
                
                # Check if it's a VK authentication error
                if is_vk_video(video_url) and ('badbrowser' in error_msg.lower() or 'unsupported url' in error_msg.lower() or 'redirect' in error_msg.lower()):
                    if not vk_username or not vk_password:
                        # VK auth is needed
                        progress_store[unique_id] = {
                            'progress': 0,
                            'status': 'Требуется авторизация VK',
                            'download_percent': 0,
                            'error': True,
                            'needs_vk_auth': True
                        }
                        return
                    else:
                        # VK auth failed even with credentials
                        progress_store[unique_id] = {
                            'progress': 0,
                            'status': f'Ошибка авторизации VK: неверный логин или пароль',
                            'download_percent': 0,
                            'error': True
                        }
                        return
                
                del progress_store[unique_id]
                return
            
            possible_files = list(TEMP_DIR.glob(f"{unique_id}.*"))
            video_files = [f for f in possible_files if f.suffix.lower() in ['.mp4', '.webm', '.mkv', '.avi', '.mov', '.flv']]
            
            if not video_files:
                del progress_store[unique_id]
                return
            
            video_path = video_files[0]
        
        print(f"Используется видео: {video_path}")
        
        update_progress(unique_id, 70, 'Обработка видео...', 100)
        
        # Calculate the seek position within the downloaded segment
        # The video segment starts at (start_time - 2), so we need to seek to the offset within it
        buffer_before = max(0, start_time - 2)
        gif_seek_time = start_time - buffer_before  # Offset from segment start (typically 2 seconds)
        
        # High-quality GIF creation using two-pass palette generation
        palette_path = TEMP_DIR / f"{unique_id}_palette.png"
        
        update_progress(unique_id, 75, 'Генерация цветовой палитры...', 100)
        
        # Step 1: Generate optimized color palette
        palette_cmd = [
            'ffmpeg',
            '-ss', str(gif_seek_time),
            '-t', str(duration),
            '-i', str(video_path),
            '-vf', 'fps=20,scale=640:-1:flags=lanczos,palettegen=stats_mode=diff:max_colors=256',
            str(palette_path),
            '-y'
        ]
        
        try:
            palette_result = subprocess.run(
                palette_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            if palette_result.returncode != 0:
                print(f"Ошибка генерации палитры: {palette_result.stderr}")
                del progress_store[unique_id]
                return
        except Exception as e:
            print(f"Ошибка при генерации палитры: {e}")
            del progress_store[unique_id]
            return
        
        update_progress(unique_id, 80, 'Конвертация в GIF...', 100)
        
        # Step 2: Create high-quality GIF using the palette
        ffmpeg_cmd = [
            'ffmpeg',
            '-ss', str(gif_seek_time),
            '-t', str(duration),
            '-i', str(video_path),
            '-i', str(palette_path),
            '-lavfi', 'fps=20,scale=640:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle',
            '-loop', '0',
            str(gif_path),
            '-y'
        ]
        
        # Run FFmpeg with progress monitoring
        try:
            process = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            
            # Monitor FFmpeg output for progress
            import re as regex_module
            frame_pattern = regex_module.compile(r'frame=\s*(\d+)')
            duration_ms = duration * 1000  # Expected duration in milliseconds
            
            for line in process.stderr:
                frame_match = frame_pattern.search(line)
                if frame_match:
                    frame_num = int(frame_match.group(1))
                    # FFmpeg outputs at 20 fps now, estimate progress
                    estimated_progress = min(90, 80 + (frame_num / (duration * 20)) * 10)
                    update_progress(unique_id, estimated_progress, 'Конвертация в GIF...', 100)
            
            process.wait()
            result_returncode = process.returncode
        except Exception as e:
            print(f"Ошибка при запуске FFmpeg: {e}")
            palette_path.unlink(missing_ok=True)
            del progress_store[unique_id]
            return
        
        if result_returncode != 0:
            print(f"Ошибка FFmpeg")
            palette_path.unlink(missing_ok=True)
            del progress_store[unique_id]
            return
        
        # Clean up palette file
        palette_path.unlink(missing_ok=True)
        
        update_progress(unique_id, 95, 'Финализация...', 100)
        video_path.unlink(missing_ok=True)
        
        update_progress(unique_id, 100, 'Готово! GIF создан', 100)
        
    except Exception as e:
        print(f"Ошибка в фоновой задаче: {str(e)}")
        if unique_id in progress_store:
            progress_store[unique_id] = {
                'progress': 0,
                'status': f'Ошибка: {str(e)}',
                'download_percent': 0,
                'error': True
            }

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
