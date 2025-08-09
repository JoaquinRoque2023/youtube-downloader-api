
import subprocess
import sys
import json
import requests
from pathlib import Path

def run_command(command):
    """Ejecuta un comando y retorna el resultado"""
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        return result.returncode == 0, result.stdout, result.stderr
    except Exception as e:
        return False, "", str(e)

def update_ytdlp():
    """Actualiza yt-dlp a la última versión"""
    print("🔄 Actualizando yt-dlp...")
    
    success, stdout, stderr = run_command("pip install --upgrade yt-dlp")
    
    if success:
        print("✅ yt-dlp actualizado correctamente")
        
        # Verificar versión
        success, version_out, _ = run_command("yt-dlp --version")
        if success:
            print(f"📦 Versión actual: {version_out.strip()}")
    else:
        print(f"❌ Error actualizando yt-dlp: {stderr}")
        return False
    
    return True

def test_youtube_access():
    """Prueba el acceso básico a YouTube"""
    print("\n🧪 Probando acceso a YouTube...")
    
    test_urls = [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",  # Never Gonna Give You Up
        "https://youtu.be/dQw4w9WgXcQ"  # Formato corto
    ]
    
    for url in test_urls:
        print(f"🔍 Probando: {url}")
        
        # Probar solo extracción de información
        cmd = f'yt-dlp --dump-json --no-download "{url}"'
        success, stdout, stderr = run_command(cmd)
        
        if success:
            try:
                info = json.loads(stdout)
                print(f"✅ Título: {info.get('title', 'N/A')}")
                print(f"✅ Duración: {info.get('duration', 0)} segundos")
                return True
            except json.JSONDecodeError:
                print(f"⚠️ Respuesta no válida")
        else:
            print(f"❌ Error: {stderr[:200]}...")
    
    return False

def test_download():
    """Prueba una descarga real"""
    print("\n📥 Probando descarga de audio...")
    
    test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    output_dir = Path("test_download")
    output_dir.mkdir(exist_ok=True)
    
    cmd = f'''yt-dlp -f "bestaudio[ext=m4a]/bestaudio" \
        --extract-audio --audio-format mp3 --audio-quality 192 \
        -o "{output_dir}/test_%(title)s.%(ext)s" \
        "{test_url}"'''
    
    success, stdout, stderr = run_command(cmd)
    
    if success:
        print("✅ Descarga exitosa")
        # Limpiar archivo de prueba
        for file in output_dir.glob("*"):
            file.unlink()
        output_dir.rmdir()
        return True
    else:
        print(f"❌ Error en descarga: {stderr[:300]}...")
        return False

def check_ffmpeg():
    """Verifica que FFmpeg esté instalado"""
    print("\n🔧 Verificando FFmpeg...")
    
    success, stdout, stderr = run_command("ffmpeg -version")
    
    if success:
        version_line = stdout.split('\n')[0]
        print(f"✅ {version_line}")
        return True
    else:
        print("❌ FFmpeg no encontrado")
        print("📝 Instala FFmpeg:")
        print("   - Windows: https://ffmpeg.org/download.html")
        print("   - macOS: brew install ffmpeg")
        print("   - Ubuntu: sudo apt install ffmpeg")
        return False

def get_youtube_status():
    """Verifica el estado de los servicios de YouTube"""
    print("\n🌐 Verificando estado de YouTube...")
    
    try:
        response = requests.get("https://www.youtube.com", timeout=10)
        if response.status_code == 200:
            print("✅ YouTube accesible")
            return True
        else:
            print(f"⚠️ YouTube responde con código: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error accediendo a YouTube: {e}")
        return False

def suggest_solutions():
    """Sugiere soluciones para problemas comunes"""
    print("\n💡 Soluciones recomendadas:")
    
    solutions = [
        "1. 🔄 Usar proxy o VPN si estás en una región bloqueada",
        "2. ⏰ Esperar unos minutos (YouTube puede tener rate limiting temporal)",
        "3. 🔄 Reiniciar el servicio después de las actualizaciones",
        "4. 🏠 Probar desde una IP/red diferente",
        "5. 📱 Verificar que la URL del video sea accesible en el navegador",
        "6. 🔧 Usar cookies de navegador (--cookies-from-browser chrome)",
        "7. 🐌 Reducir velocidad de descarga (--limit-rate 1M)",
    ]
    
    for solution in solutions:
        print(solution)

def main():
    """Función principal del diagnóstico"""
    print("🚀 Diagnóstico y actualización de YouTube Downloader")
    print("=" * 60)
    
    # 1. Actualizar yt-dlp
    if not update_ytdlp():
        return
    
    # 2. Verificar FFmpeg
    ffmpeg_ok = check_ffmpeg()
    
    # 3. Verificar acceso a YouTube
    youtube_ok = get_youtube_status()
    
    # 4. Probar extracción de información
    info_ok = test_youtube_access()
    
    # 5. Probar descarga
    download_ok = test_download()
    
    # Resumen
    print("\n📊 RESUMEN DE DIAGNÓSTICO")
    print("=" * 40)
    print(f"FFmpeg instalado: {'✅' if ffmpeg_ok else '❌'}")
    print(f"YouTube accesible: {'✅' if youtube_ok else '❌'}")
    print(f"Extracción de info: {'✅' if info_ok else '❌'}")
    print(f"Descarga funcional: {'✅' if download_ok else '❌'}")
    
    if not all([ffmpeg_ok, youtube_ok, info_ok, download_ok]):
        suggest_solutions()
    else:
        print("\n🎉 ¡Todo funciona correctamente!")

if __name__ == "__main__":
    main()