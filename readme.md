# Spotify Library Scraper

Extrae canciones de tu biblioteca de Spotify desde la consola del navegador.

## Ejecutar en local

```bash
# Instalar dependencias del sistema
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Linux
# Windows: descarga de https://ffmpeg.org/download.html y agrega al PATH

# Crear entorno virtual
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# Instalar dependencias y ejecutar
pip install -r requirements.txt
python app.py
```

## Cómo usar

1. Abre Spotify web y ve a **Tu biblioteca → Canciones**
2. Abre la consola del navegador (`F12` → Console)
3. Copia el script y pégalo en la consola
4. El script se ejecuta automáticamente y hace scroll solo
5. Si no detecta nuevas canciones en 3s, aparece un botón verde para copiar

## Comandos disponibles

| Comando | Descripción |
|---------|-------------|
| `spotifyScraper.start()` | Iniciar el scraper |
| `spotifyScraper.stop()` | Detener el scraper |
| `spotifyScraper.reset()` | Reiniciar (limpia todo) |
| `spotifyScraper.copyAndShow()` | Mostrar resultado y abrir selector de copia |

## Notas

- El scraper hace scroll automáticamente cada 0.5 segundos dentro del contenedor de Spotify
- Valida que no haya saltos en la numeración
- Si detecta que faltan canciones, avisa: `⚠️ FALTAN: X, Y, Z`
- El resultado se muestra en formato: `Título - Artista` (uno por línea)
- Al terminar, haz click en el botón verde para copiar al portapapeles