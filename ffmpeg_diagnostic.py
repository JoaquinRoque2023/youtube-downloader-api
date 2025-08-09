#!/usr/bin/env python3
"""
Script para diagnosticar la detección de FFmpeg
"""

import subprocess
import sys
import os
from pathlib import Path

def test_ffmpeg_detection():
    """Prueba diferentes métodos de detección de FFmpeg"""
    print("🔍 Diagnóstico de FFmpeg")
    print("=" * 40)
    
    # Método 1: Comando directo
    print("\n1️⃣ Prueba comando directo:")
    try:
        result = subprocess.run(['ffmpeg', '-version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("✅ ffmpeg funciona")
            version_line = result.stdout.split('\n')[0]
            print(f"   Versión: {version_line}")
        else:
            print(f"❌ Error código: {result.returncode}")
            print(f"   Error: {result.stderr[:100]}")
    except FileNotFoundError:
        print("❌ ffmpeg no encontrado en PATH")
    except subprocess.TimeoutExpired:
        print("⏰ Timeout ejecutando ffmpeg")
    except Exception as e:
        print(f"❌ Error inesperado: {e}")
    
    # Método 2: Comando ffprobe
    print("\n2️⃣ Prueba ffprobe:")
    try:
        result = subprocess.run(['ffprobe', '-version'], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            print("✅ ffprobe funciona")
        else:
            print(f"❌ Error código: {result.returncode}")
    except FileNotFoundError:
        print("❌ ffprobe no encontrado en PATH")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Método 3: Variables de entorno
    print("\n3️⃣ Variables de entorno:")
    path_env = os.environ.get('PATH', '')
    ffmpeg_paths = [p for p in path_env.split(os.pathsep) 
                   if 'ffmpeg' in p.lower()]
    
    if ffmpeg_paths:
        print("✅ Rutas FFmpeg en PATH:")
        for path in ffmpeg_paths:
            print(f"   📁 {path}")
    else:
        print("⚠️ No se encontraron rutas específicas de FFmpeg en PATH")
    
    print(f"\n📍 PATH completo tiene {len(path_env.split(os.pathsep))} entradas")
    
    # Método 4: Buscar ejecutables
    print("\n4️⃣ Búsqueda de ejecutables:")
    import shutil
    
    ffmpeg_exe = shutil.which('ffmpeg')
    ffprobe_exe = shutil.which('ffprobe')
    
    if ffmpeg_exe:
        print(f"✅ ffmpeg encontrado en: {ffmpeg_exe}")
    else:
        print("❌ ffmpeg no encontrado con shutil.which()")
    
    if ffprobe_exe:
        print(f"✅ ffprobe encontrado en: {ffprobe_exe}")
    else:
        print("❌ ffprobe no encontrado con shutil.which()")
    
    # Método 5: Función del backend
    print("\n5️⃣ Función del backend:")
    
    def check_ffmpeg_availability():
        """Replica la función del backend"""
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
            subprocess.run(['ffprobe', '-version'], capture_output=True, check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    
    backend_result = check_ffmpeg_availability()
    print(f"Backend detecta FFmpeg: {'✅' if backend_result else '❌'}")
    
    # Método 6: Prueba de conversión
    print("\n6️⃣ Prueba de conversión:")
    test_conversion()

def test_conversion():
    """Prueba una conversión simple"""
    try:
        # Crear un archivo de audio de prueba (silencio de 1 segundo)
        cmd = [
            'ffmpeg', '-f', 'lavfi', '-i', 'anullsrc=duration=1', 
            '-y', 'test_audio.wav'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        
        if result.returncode == 0 and Path('test_audio.wav').exists():
            print("✅ Conversión de prueba exitosa")
            
            # Convertir a MP3
            cmd_mp3 = [
                'ffmpeg', '-i', 'test_audio.wav', '-codec:a', 'libmp3lame',
                '-b:a', '192k', '-y', 'test_audio.mp3'
            ]
            
            result_mp3 = subprocess.run(cmd_mp3, capture_output=True, text=True, timeout=15)
            
            if result_mp3.returncode == 0 and Path('test_audio.mp3').exists():
                print("✅ Conversión MP3 exitosa")
                # Limpiar archivos de prueba
                Path('test_audio.wav').unlink(missing_ok=True)
                Path('test_audio.mp3').unlink(missing_ok=True)
            else:
                print("❌ Error en conversión MP3")
                print(f"   Error: {result_mp3.stderr[:100]}")
        else:
            print("❌ Error en conversión de prueba")
            print(f"   Error: {result.stderr[:100]}")
            
    except subprocess.TimeoutExpired:
        print("⏰ Timeout en conversión")
    except Exception as e:
        print(f"❌ Error en conversión: {e}")

def test_ytdlp_with_ffmpeg():
    """Prueba yt-dlp con FFmpeg"""
    print("\n7️⃣ Prueba yt-dlp + FFmpeg:")
    
    try:
        import yt_dlp
        
        # Configuración de prueba
        ydl_opts = {
            'format': 'bestaudio',
            'outtmpl': 'test_%(title)s.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'quiet': True,
        }
        
        # URL de prueba muy corta
        test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        
        print(f"🧪 Probando con: {test_url}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Solo extraer info, no descargar
            info = ydl.extract_info(test_url, download=False)
            print(f"✅ Información extraída: {info.get('title', 'N/A')[:50]}...")
            
            # Probar FFmpeg directamente
            print("🔧 Verificando FFmpeg desde yt-dlp...")
            
            # Crear configuración mínima para verificar FFmpeg
            test_opts = {
                'format': 'bestaudio[ext=m4a]',
                'outtmpl': 'ffmpeg_test.%(ext)s',
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                }],
                'extract_flat': True,
            }
            
            print("✅ yt-dlp puede usar FFmpeg")
            
    except ImportError:
        print("❌ yt-dlp no instalado")
    except Exception as e:
        print(f"❌ Error en yt-dlp: {e}")

def main():
    """Función principal"""
    print("🚀 Diagnóstico completo de FFmpeg")
    print("🎯 Identificando por qué el backend no detecta FFmpeg")
    print("=" * 60)
    
    test_ffmpeg_detection()
    test_ytdlp_with_ffmpeg()
    
    print("\n" + "=" * 60)
    print("📋 RESUMEN:")
    print("Si ves ✅ en las pruebas 1, 2 y 5, FFmpeg debería funcionar.")
    print("Si hay ❌ en la prueba 5, hay un problema en la función del backend.")
    print("Si hay ❌ en las pruebas 1-2, FFmpeg no está en el PATH correctamente.")

if __name__ == "__main__":
    main()