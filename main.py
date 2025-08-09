"""
YouTube Music Downloader Backend API
Desarrollado con FastAPI y yt-dlp
"""

import os
import asyncio
import uuid
import shutil
import subprocess  # FALTABA ESTA IMPORTACIÓN
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import tempfile
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp
import uvicorn
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuración de la aplicación
app = FastAPI(
    title="YouTube Music Downloader API",
    description="API para descargar música y videos de YouTube",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En producción, especifica dominios exactos
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Directorio para descargas temporales
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Almacén en memoria para tareas (en producción usar Redis)
download_tasks: Dict[str, Dict[str, Any]] = {}

# Modelos Pydantic
class DownloadRequest(BaseModel):
    url: HttpUrl
    format: str = "mp3"  # mp3, mp4, wav, m4a
    quality: str = "192"  # 128, 192, 320 para audio
    
class VideoInfo(BaseModel):
    title: str
    duration: int
    thumbnail: str
    uploader: str
    view_count: int
    
class DownloadResponse(BaseModel):
    task_id: str
    status: str
    message: str
    
class TaskStatus(BaseModel):
    task_id: str
    status: str  # pending, processing, completed, error
    progress: int
    message: str
    file_path: Optional[str] = None
    file_size: Optional[int] = None

# Funciones auxiliares
def clean_filename(filename: str) -> str:
    """Limpia el nombre del archivo para que sea válido en el sistema"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    return filename[:200]  # Limitar longitud

def get_file_size(file_path: Path) -> int:
    """Obtiene el tamaño del archivo en bytes"""
    try:
        return file_path.stat().st_size
    except:
        return 0

def check_ffmpeg_availability() -> bool:
    """Verifica si FFmpeg está disponible en el sistema"""
    try:
        # Método 1: Verificar con subprocess
        result_ffmpeg = subprocess.run(
            ['ffmpeg', '-version'], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        result_ffprobe = subprocess.run(
            ['ffprobe', '-version'], 
            capture_output=True, 
            text=True, 
            timeout=10
        )
        
        if result_ffmpeg.returncode == 0 and result_ffprobe.returncode == 0:
            logger.info("✅ FFmpeg detectado correctamente")
            # Obtener versión
            version_line = result_ffmpeg.stdout.split('\n')[0] if result_ffmpeg.stdout else "versión desconocida"
            logger.info(f"FFmpeg: {version_line}")
            return True
        else:
            logger.warning(f"❌ FFmpeg error codes: ffmpeg={result_ffmpeg.returncode}, ffprobe={result_ffprobe.returncode}")
            
    except subprocess.TimeoutExpired:
        logger.warning("⏰ Timeout verificando FFmpeg")
    except FileNotFoundError:
        logger.warning("❌ FFmpeg no encontrado en PATH")
    except Exception as e:
        logger.warning(f"❌ Error verificando FFmpeg: {e}")
    
    # Método 2: Usar shutil.which como respaldo
    try:
        ffmpeg_path = shutil.which('ffmpeg')
        ffprobe_path = shutil.which('ffprobe')
        
        if ffmpeg_path and ffprobe_path:
            logger.info(f"✅ FFmpeg encontrado con shutil.which: {ffmpeg_path}")
            return True
        else:
            logger.warning(f"❌ shutil.which - ffmpeg: {ffmpeg_path}, ffprobe: {ffprobe_path}")
            
    except Exception as e:
        logger.warning(f"❌ Error con shutil.which: {e}")
    
    logger.error("❌ FFmpeg NO está disponible")
    return False

def get_fallback_formats(format_type: str, quality: str) -> list:
    """Obtiene formatos de respaldo en caso de fallo"""
    if format_type in ['mp3', 'wav', 'm4a']:
        return [
            'bestaudio[ext=m4a]',
            'bestaudio[ext=mp4]', 
            'bestaudio[ext=webm]',
            'bestaudio',
            'best[height<=480]',
            'best'
        ]
    elif format_type == 'mp4':
        quality_map = {
            '480': ['best[height<=480]', 'best[height<=720]', 'best'],
            '720': ['best[height<=720]', 'best[height<=1080]', 'best'],
            '1080': ['best[height<=1080]', 'best']
        }
        return quality_map.get(quality, ['best'])
    return ['best']

async def cleanup_old_files():
    """Limpia archivos más antiguos de 1 hora"""
    cutoff_time = datetime.now() - timedelta(hours=1)
    
    for file_path in DOWNLOAD_DIR.glob("*"):
        if file_path.is_file():
            file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
            if file_time < cutoff_time:
                try:
                    file_path.unlink()
                    logger.info(f"Archivo eliminado: {file_path}")
                except Exception as e:
                    logger.error(f"Error eliminando {file_path}: {e}")

class CustomYTDL:
    """Clase personalizada para manejar yt-dlp con callbacks"""
    
    def __init__(self, task_id: str):
        self.task_id = task_id
        
    def progress_hook(self, d):
        """Hook para actualizar el progreso de descarga"""
        if d['status'] == 'downloading':
            if 'total_bytes' in d and d['total_bytes']:
                progress = int((d['downloaded_bytes'] / d['total_bytes']) * 100)
            elif 'total_bytes_estimate' in d and d['total_bytes_estimate']:
                progress = int((d['downloaded_bytes'] / d['total_bytes_estimate']) * 100)
            else:
                progress = 0
                
            download_tasks[self.task_id].update({
                'progress': progress,
                'status': 'processing',
                'message': f'Descargando... {progress}%'
            })
            
        elif d['status'] == 'finished':
            download_tasks[self.task_id].update({
                'progress': 100,
                'status': 'completed',
                'message': 'Descarga completada',
                'file_path': d['filename']
            })

async def download_youtube_content(task_id: str, url: str, format_type: str, quality: str):
    """Función principal para descargar contenido de YouTube"""
    try:
        download_tasks[task_id].update({
            'status': 'processing',
            'progress': 0,
            'message': 'Iniciando descarga...'
        })
        
        # Crear instancia personalizada
        ytdl_instance = CustomYTDL(task_id)
        
        # Lista de User-Agents rotativos más actualizados
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:109.0) Gecko/20100101 Firefox/121.0'
        ]
        
        # Configuración mejorada de yt-dlp para evitar bloqueos
        ydl_opts = {
            'outtmpl': str(DOWNLOAD_DIR / '%(title)s_%(id)s.%(ext)s'),
            'progress_hooks': [ytdl_instance.progress_hook],
            'extract_flat': False,
            'writethumbnail': False,
            'writeinfojson': False,
            # Configuraciones anti-bloqueo mejoradas
            'extractor_retries': 5,
            'fragment_retries': 5,
            'retries': 5,
            'http_chunk_size': 10485760,  # 10MB chunks
            'nocheckcertificate': True,
            'ignoreerrors': False,
            'no_warnings': False,
            'cookiefile': None,
            # Headers para simular navegador real (rotativo)
            'http_headers': {
                'User-Agent': user_agents[0],  # Se cambiará en cada intento
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9,es;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Cache-Control': 'max-age=0',
            },
            # Configuración de proxy/red mejorada
            'socket_timeout': 60,
            'sleep_interval': 2,
            'max_sleep_interval': 10,
            'sleep_interval_subtitles': 0,
            # Configuraciones adicionales anti-detección
            'age_limit': None,
            'playlistend': 1,
            'noplaylist': True,
            # Configuración específica para YouTube
            'youtube_include_dash_manifest': False,
            'extract_flat': False,
        }
        
        # Verificar si FFmpeg está disponible
        ffmpeg_available = check_ffmpeg_availability()
        logger.info(f"[DEBUG] ffmpeg_available: {ffmpeg_available}, format_type: {format_type}, quality: {quality}")

        # Configuración según el formato con formatos de respaldo
        fallback_formats = get_fallback_formats(format_type, quality)

        if format_type in ['mp3', 'wav', 'm4a']:
            if ffmpeg_available and format_type != 'm4a':
                # Con FFmpeg: convertir a formato deseado
                ydl_opts.update({
                    'format': '/'.join(fallback_formats),
                    'postprocessors': [{
                        'key': 'FFmpegExtractAudio',
                        'preferredcodec': format_type,
                        'preferredquality': quality,
                    }],
                })
                logger.info(f"[DEBUG] ydl_opts para audio con FFmpeg: {ydl_opts}")
            else:
                # Sin FFmpeg: descargar m4a directo (mejor calidad de audio nativo)
                ydl_opts.update({
                    'format': 'bestaudio[ext=m4a]/bestaudio[ext=mp4]/bestaudio',
                })
                logger.info(f"[DEBUG] ydl_opts para audio sin FFmpeg: {ydl_opts}")
                # Actualizar mensaje para el usuario
                download_tasks[task_id].update({
                    'message': f'FFmpeg no disponible. Descargando en formato M4A de alta calidad.'
                })
        elif format_type == 'mp4':
            ydl_opts['format'] = '/'.join(fallback_formats)
            logger.info(f"[DEBUG] ydl_opts para video: {ydl_opts}")
        
        # Intentar descarga con reintentos y estrategias múltiples
        max_attempts = 5  # Aumentamos los intentos
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                download_tasks[task_id].update({
                    'message': f'Intento {attempt + 1} de {max_attempts}... (Estrategia anti-bloqueo activa)'
                })
                
                # Rotar User-Agent en cada intento
                ydl_opts['http_headers']['User-Agent'] = user_agents[attempt % len(user_agents)]
                
                # Estrategias progresivas anti-bloqueo
                if attempt == 0:
                    # Primer intento: configuración estándar
                    pass
                elif attempt == 1:
                    # Segundo intento: agregar cookies simuladas y referer
                    ydl_opts['http_headers']['Referer'] = 'https://www.youtube.com/'
                    ydl_opts['http_headers']['Cookie'] = 'CONSENT=YES+cb; YSC=randomstring; VISITOR_INFO1_LIVE=randomstring'
                elif attempt == 2:
                    # Tercer intento: simular cliente móvil
                    ydl_opts['http_headers']['User-Agent'] = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15'
                    ydl_opts['http_headers']['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
                elif attempt == 3:
                    # Cuarto intento: usar extractor alternativo y formato básico
                    ydl_opts.update({
                        'youtube_include_dash_manifest': True,
                        'extract_flat': True,
                        'format': 'best/worst',  # Formato más básico
                        'socket_timeout': 120,
                    })
                elif attempt == 4:
                    # Último intento: configuración mínima de emergencia
                    ydl_opts = {
                        'outtmpl': str(DOWNLOAD_DIR / '%(title)s_%(id)s.%(ext)s'),
                        'progress_hooks': [ytdl_instance.progress_hook],
                        'format': 'worst',  # Calidad más baja pero más compatible
                        'http_headers': {
                            'User-Agent': 'yt-dlp/2023.12.30',
                        },
                        'socket_timeout': 180,
                        'retries': 10,
                        'ignoreerrors': True,
                        'no_warnings': True,
                    }
                
                # Agregar delay progresivo entre intentos
                if attempt > 0:
                    delay = min(5 * (2 ** (attempt - 1)), 30)  # Backoff exponencial max 30s
                    download_tasks[task_id].update({
                        'message': f'Esperando {delay}s antes del intento {attempt + 1}...'
                    })
                    await asyncio.sleep(delay)
                
                # Descargar con yt-dlp
                logger.info(f"[DEBUG] ydl_opts antes de descarga (intento {attempt+1}): {ydl_opts}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Obtener información del video primero
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            raise Exception("No se pudo extraer información del video")
                    except Exception as info_error:
                        logger.warning(f"Error extrayendo info en intento {attempt + 1}: {info_error}")
                        if attempt < max_attempts - 1:
                            continue
                        raise info_error
                    
                    clean_title = clean_filename(info.get('title', 'unknown'))
                    
                    # Actualizar información de la tarea
                    download_tasks[task_id].update({
                        'video_info': {
                            'title': info.get('title', 'Sin título'),
                            'duration': info.get('duration', 0),
                            'uploader': info.get('uploader', 'Desconocido'),
                            'view_count': info.get('view_count', 0),
                            'thumbnail': info.get('thumbnail', ''),
                        }
                    })
                    
                    # Realizar la descarga
                    download_tasks[task_id].update({
                        'message': f'Descargando: {clean_title[:50]}...'
                    })
                    ydl.download([url])
                
                # Si llegamos aquí, la descarga fue exitosa
                logger.info(f"✅ Descarga exitosa en intento {attempt + 1}")
                break
                
            except yt_dlp.utils.ExtractorError as e:
                last_error = e
                error_msg = str(e).lower()
                logger.warning(f"❌ ExtractorError en intento {attempt + 1}: {str(e)[:200]}...")
                
                # Analizar tipos específicos de error
                if any(keyword in error_msg for keyword in ["403", "forbidden", "blocked", "unavailable"]):
                    if attempt < max_attempts - 1:
                        logger.info(f"🔄 Error de acceso detectado, reintentando con nueva estrategia...")
                        continue
                elif "private" in error_msg or "removed" in error_msg:
                    raise HTTPException(status_code=404, detail="El video es privado, ha sido eliminado o no está disponible")
                elif "not available" in error_msg or "geo" in error_msg:
                    raise HTTPException(status_code=403, detail="El video no está disponible en tu región")
                
                if attempt == max_attempts - 1:
                    raise e
                    
            except Exception as e:
                last_error = e
                logger.warning(f"❌ Error general en intento {attempt + 1}: {str(e)[:200]}...")
                if attempt < max_attempts - 1:
                    continue
                raise e
        
        if last_error:
            # Si todos los intentos fallaron, lanzar el último error
            error_msg = str(last_error)
            if "player response" in error_msg.lower():
                raise HTTPException(
                    status_code=503, 
                    detail="YouTube está bloqueando las descargas temporalmente. Intenta más tarde o actualiza yt-dlp con: pip install --upgrade yt-dlp"
                )
            raise last_error
            

        # Buscar el archivo descargado
        downloaded_files = list(DOWNLOAD_DIR.glob(f"*{info['id']}*"))
        if downloaded_files:
            file_path = downloaded_files[0]
            file_size = get_file_size(file_path)

            # Determinar el tipo de archivo actual
            actual_format = file_path.suffix.lower().replace('.', '')
            logger.info(f"[DEBUG] Archivo generado: {file_path}, tamaño: {file_size}, formato real: {actual_format}")
            success_message = f'Descarga completada exitosamente'

            # Si el usuario pidió audio (mp3, wav, m4a) y el archivo no es del formato solicitado, intentar conversión post-descarga
            if format_type in ['mp3', 'wav', 'm4a'] and actual_format != format_type:
                if ffmpeg_available:
                    # Intentar conversión con FFmpeg
                    converted_path = file_path.with_suffix(f'.{format_type}')
                    ffmpeg_cmd = [
                        'ffmpeg', '-y', '-i', str(file_path),
                        '-vn', '-ab', f'{quality}k', '-ar', '44100', '-f', format_type, str(converted_path)
                    ]
                    try:
                        logger.info(f"[DEBUG] Ejecutando conversión FFmpeg: {' '.join(ffmpeg_cmd)}")
                        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=120)
                        if result.returncode == 0 and converted_path.exists():
                            logger.info(f"[DEBUG] Conversión exitosa: {converted_path}")
                            # Eliminar archivo original si la conversión fue exitosa
                            try:
                                file_path.unlink()
                            except Exception as del_err:
                                logger.warning(f"No se pudo eliminar el archivo original: {del_err}")
                            file_path = converted_path
                            file_size = get_file_size(file_path)
                            actual_format = format_type
                            success_message += f' (convertido a {format_type.upper()} tras la descarga)'
                        else:
                            logger.error(f"[DEBUG] Error en conversión FFmpeg: {result.stderr}")
                            success_message += f' (No se pudo convertir a {format_type.upper()}, se entrega el archivo original: {file_path.suffix})'
                    except Exception as ffmpeg_err:
                        logger.error(f"[DEBUG] Excepción en conversión FFmpeg: {ffmpeg_err}")
                        success_message += f' (No se pudo convertir a {format_type.upper()}, se entrega el archivo original: {file_path.suffix})'
                else:
                    success_message += f' (formato: {actual_format.upper()} - instala FFmpeg para conversión a {format_type.upper()})'

            download_tasks[task_id].update({
                'status': 'completed',
                'progress': 100,
                'message': success_message,
                'file_path': str(file_path),
                'file_size': file_size,
                'actual_format': actual_format
            })
        else:
            logger.error("[DEBUG] No se encontró el archivo descargado tras la descarga")
            raise Exception("No se encontró el archivo descargado")
            
    except Exception as e:
        logger.error(f"Error en descarga {task_id}: {str(e)}")
        download_tasks[task_id].update({
            'status': 'error',
            'progress': 0,
            'message': f'Error: {str(e)}'
        })

# Endpoints de la API

@app.get("/")
async def root():
    """Endpoint raíz con información de la API"""
    return {
        "message": "YouTube Music Downloader API",
        "version": "1.0.0",
        "endpoints": {
            "info": "/info?url=<youtube_url>",
            "download": "/download (POST)",
            "status": "/status/{task_id}",
            "file": "/file/{task_id}"
        }
    }

@app.get("/health")
async def health_check():
    """Endpoint de health check"""
    ffmpeg_status = check_ffmpeg_availability()
    
    # Verificar versión de yt-dlp
    try:
        import yt_dlp
        ytdl_version = yt_dlp.version.__version__
    except:
        ytdl_version = "desconocida"
    
    return {
        "status": "healthy", 
        "timestamp": datetime.now().isoformat(),
        "ffmpeg_available": ffmpeg_status,
        "yt_dlp_version": ytdl_version,
        "supported_conversions": ["mp3", "wav", "m4a"] if ffmpeg_status else ["m4a (nativo)"],
        "recommendations": {
            "update_ytdlp": "pip install --upgrade yt-dlp",
            "install_ffmpeg": "https://ffmpeg.org/download.html"
        }
    }

@app.post("/update-ytdlp")
async def update_ytdlp():
    """Actualiza yt-dlp a la última versión"""
    try:
        import subprocess
        result = subprocess.run(
            ['pip', 'install', '--upgrade', 'yt-dlp'], 
            capture_output=True, 
            text=True, 
            timeout=60
        )
        
        if result.returncode == 0:
            return {
                "success": True,
                "message": "yt-dlp actualizado exitosamente",
                "output": result.stdout
            }
        else:
            return {
                "success": False,
                "message": "Error actualizando yt-dlp",
                "error": result.stderr
            }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error ejecutando actualización: {str(e)}"
        }

@app.get("/info")
@limiter.limit("10/minute")
async def get_video_info(request: Request, url: HttpUrl) -> VideoInfo:
    """Obtiene información del video sin descargarlo"""
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': False,
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(url), download=False)
            
        return VideoInfo(
            title=info.get('title', 'Sin título'),
            duration=info.get('duration', 0),
            thumbnail=info.get('thumbnail', ''),
            uploader=info.get('uploader', 'Desconocido'),
            view_count=info.get('view_count', 0)
        )
        
    except Exception as e:
        logger.error(f"Error obteniendo info: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Error obteniendo información: {str(e)}")

@app.post("/download")
@limiter.limit("5/minute")
async def start_download(
    request: Request, 
    download_req: DownloadRequest, 
    background_tasks: BackgroundTasks
) -> DownloadResponse:
    """Inicia una descarga en segundo plano"""
    
    # Validar formato
    valid_formats = ['mp3', 'mp4', 'wav', 'm4a']
    if download_req.format not in valid_formats:
        raise HTTPException(
            status_code=400, 
            detail=f"Formato no válido. Use: {', '.join(valid_formats)}"
        )
    
    # Generar ID único para la tarea
    task_id = str(uuid.uuid4())
    
    # Inicializar tarea
    download_tasks[task_id] = {
        'status': 'pending',
        'progress': 0,
        'message': 'Tarea creada, iniciando...',
        'created_at': datetime.now(),
        'url': str(download_req.url),
        'format': download_req.format,
        'quality': download_req.quality
    }
    
    # Agregar tarea en segundo plano
    background_tasks.add_task(
        download_youtube_content,
        task_id,
        str(download_req.url),
        download_req.format,
        download_req.quality
    )
    
    # Agregar limpieza de archivos antiguos
    background_tasks.add_task(cleanup_old_files)
    
    return DownloadResponse(
        task_id=task_id,
        status="pending",
        message="Descarga iniciada. Use /status/{task_id} para verificar el progreso."
    )

@app.get("/status/{task_id}")
async def get_download_status(task_id: str) -> TaskStatus:
    """Obtiene el estado de una descarga"""
    if task_id not in download_tasks:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    
    task = download_tasks[task_id]
    
    return TaskStatus(
        task_id=task_id,
        status=task['status'],
        progress=task['progress'],
        message=task['message'],
        file_path=task.get('file_path'),
        file_size=task.get('file_size')
    )

@app.get("/file/{task_id}")
async def download_file(task_id: str):
    """Descarga el archivo completado"""
    if task_id not in download_tasks:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    
    task = download_tasks[task_id]
    
    if task['status'] != 'completed':
        raise HTTPException(status_code=400, detail="La descarga no está completada")
    
    file_path = Path(task['file_path'])
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    
    # Obtener nombre del archivo limpio
    filename = file_path.name
    
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type='application/octet-stream'
    )

@app.delete("/task/{task_id}")
async def delete_task(task_id: str):
    """Elimina una tarea y su archivo asociado"""
    if task_id not in download_tasks:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    
    task = download_tasks[task_id]
    
    # Eliminar archivo si existe
    if 'file_path' in task:
        file_path = Path(task['file_path'])
        if file_path.exists():
            try:
                file_path.unlink()
            except Exception as e:
                logger.error(f"Error eliminando archivo: {e}")
    
    # Eliminar tarea de memoria
    del download_tasks[task_id]
    
    return {"message": "Tarea eliminada exitosamente"}

@app.get("/tasks")
async def list_tasks():
    """Lista todas las tareas activas"""
    return {
        "total_tasks": len(download_tasks),
        "tasks": [
            {
                "task_id": tid,
                "status": task['status'],
                "progress": task['progress'],
                "created_at": task['created_at'].isoformat(),
                "format": task['format']
            }
            for tid, task in download_tasks.items()
        ]
    }

# Manejo de errores global
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Error no manejado: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Error interno del servidor",
            "message": "Ha ocurrido un error inesperado"
        }
    )

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        access_log=True
    )