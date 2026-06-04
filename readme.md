# Liked Songs Downloader

Descarga tus canciones de Spotify como MP3 sin necesidad de Spotify Premium ni OAuth.

---

## Requisito único: Docker Desktop

Instala [Docker Desktop](https://www.docker.com/products/docker-desktop/) según tu sistema y ábrelo al menos una vez. Docker se encarga de instalar Python, FFmpeg y todas las dependencias automáticamente — no necesitas instalar nada más.

| Sistema | Descarga |
|---------|----------|
| Windows | [Docker Desktop para Windows](https://docs.docker.com/desktop/install/windows-install/) |
| macOS | [Docker Desktop para Mac](https://docs.docker.com/desktop/install/mac-install/) |
| Ubuntu | [Docker Engine para Linux](https://docs.docker.com/engine/install/ubuntu/) |

---

## Instalación y uso

```bash
# 1. Clona el repositorio
git clone https://github.com/Duver0/mp3-downloader
cd mp3-downloader

# 2. Levanta la app
docker compose up --build
```

Abre `http://localhost:8080` en el navegador. Las descargas se guardan en la carpeta `downloads/` dentro del proyecto.

```bash
# Detener
docker compose down

# Volver a levantar (ya no necesita --build)
docker compose up
```

> La primera vez tarda unos minutos mientras Docker descarga e instala todo. Las siguientes veces arranca en segundos.

---

## Cómo usar la app

### Paso 1 — Obtener la lista de canciones

Tienes tres opciones:

**Opción A — Scraper del navegador (recomendado para listas largas)**
1. Abre [open.spotify.com](https://open.spotify.com) → **Tu biblioteca → Canciones**
2. Abre la consola del navegador (`F12` → pestaña *Console*)
3. Copia el contenido de `static/spotify-scraper.js` y pégalo en la consola
4. El script hace scroll automático hasta el final de tu biblioteca
5. Al terminar aparece un botón verde — haz clic para copiar la lista

**Opción B — URLs de Spotify**
```
https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC
https://open.spotify.com/track/7qiZfU4dY1lWllzX7mPBI3
```

**Opción C — Texto libre**
```
Bohemian Rhapsody - Queen
Hotel California - Eagles
```

### Paso 2 — Configurar carpeta de destino

Indica en la app dónde guardar los archivos. Por defecto los descarga en `downloads/` dentro del proyecto.

### Paso 3 — Sincronizar

Haz clic en **Sincronizar**. La app muestra el progreso en tiempo real. Las canciones ya descargadas se omiten automáticamente en ejecuciones futuras; si alguna falla se puede reintentar sin afectar las completadas.

---

## Opciones de configuración

| Opción | Descripción | Por defecto |
|--------|-------------|-------------|
| Carpeta de destino | Dónde se guardan los archivos | `downloads/` del proyecto |
| Formato de audio | `mp3`, `ogg` o `opus` | `mp3` |
| Calidad de audio | `0` (mejor) — `9` (menor tamaño) | `2` |

Para cambiar la carpeta de destino a una ruta fuera del proyecto edita `docker-compose.yml`:
```yaml
volumes:
  - /ruta/absoluta/a/tu/musica:/downloads
```

---

## Comandos del scraper

Ejecutados en la consola del navegador mientras el scraper corre:

| Comando | Descripción |
|---------|-------------|
| `spotifyScraper.start()` | Iniciar |
| `spotifyScraper.stop()` | Detener |
| `spotifyScraper.reset()` | Reiniciar y limpiar resultados |
| `spotifyScraper.copyAndShow()` | Copiar lista al portapapeles |

---

## Sincronizar a YouTube (opcional)

Puedes agregar tus canciones a una playlist privada de YouTube sin límite de cuota. Soporta dos métodos de autenticación que se usan en conjunto:

1. **OAuth** (múltiples proyectos Google Cloud) — se rotan automáticamente al agotar cuota
2. **Cookies** — sin cuota, ilimitado

### Método A — OAuth (múltiples proyectos)

1. Ve a [Google Cloud Console](https://console.cloud.google.com) → crea **uno o más** proyectos
2. En cada proyecto: **APIs & Services → Library** → habilita **YouTube Data API v3**
3. **Credentials → Create Credentials → OAuth 2.0 Client ID** (tipo Web application, redirect URI: `http://localhost:8080/auth/callback`)
4. Descarga cada JSON y guárdalo en `static/client_secrets/` como:
   ```
   static/client_secrets/
   ├── client_secret_1.json
   ├── client_secret_2.json
   └── client_secret_3.json   ← (los que quieras)
   ```
5. Abre la app → Dashboard → haz clic en **"Conectar proyecto 1"**, **"Conectar proyecto 2"**, etc.
6. Haz clic en **"📤 Sincronizar a playlist"**
7. La app usa cada proyecto hasta agotar su cuota, luego cambia al siguiente automáticamente

> ⚠️ `static/client_secrets/*.json` está en `.gitignore`. No se sube al repositorio.

### Método B — Cookies de YouTube

Sin límite de cuota. Exporta las cookies de tu sesión de YouTube usando una extensión como:

- [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome)
- [cookies.txt export](https://addons.mozilla.org/es/firefox/addon/cookies-txt/) (Firefox)

Guarda el archivo como `static/cookies/youtube_cookies.txt` en **formato Netscape**:

```
.youtube.com	TRUE	/	TRUE	1712345678	__Secure-3PSAPISID	valor_hash
.youtube.com	TRUE	/	TRUE	1712345678	__Secure-3PSID	valor_hash
...
```

La app lo detecta automáticamente y lo usa como fallback cuando todos los proyectos OAuth agotan su cuota.

### Orden de prioridad

```
1. OAuth Proyecto 1  → quotaExceeded?
2. OAuth Proyecto 2  → quotaExceeded?
   ...
N. Cookies           → sin límite de cuota
```

### Usar

1. Abre la app → Dashboard
2. Conecta al menos un proyecto OAuth **o** agrega cookies
3. Haz clic en **"📤 Sincronizar a playlist"**
4. La app crea una playlist privada **"Spotify to Youtube"** y agrega cada canción

---

## Instalación sin Docker

<details>
<summary>Ver instrucciones para Windows, macOS y Ubuntu</summary>

### Requisitos

- Python 3.10 o superior
- FFmpeg

**Windows:**
```
winget install Gyan.FFmpeg
```
Durante la instalación de Python activa **"Add Python to PATH"**.

**macOS:**
```bash
brew install python ffmpeg
```

**Ubuntu:**
```bash
sudo apt update && sudo apt install python3 python3-venv ffmpeg
```

### Ejecutar

```bash
# Windows CMD
python -m venv .venv && .venv\Scripts\activate.bat && pip install -r requirements.txt && python app.py

# macOS / Ubuntu
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && python app.py
```

En Windows también puedes ejecutar `start.bat` directamente.

</details>
