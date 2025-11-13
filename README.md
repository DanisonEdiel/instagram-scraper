# Scraper de Instagram (Python)

Proyecto monolítico en Python que extrae datos de perfiles de Instagram a partir de una URL de perfil. Usa Instaloader (robusto y optimizado para scraping de Instagram) y Playwright para iniciar sesión vía OAuth de Facebook. El código sigue prácticas de clean code y una estructura estándar.

## Funcionalidades
- Extrae campos principales del perfil: `username`, `full_name`, `biography`, `external_url`, `is_verified`, `is_private`, `profile_pic_url`, `followers`, `following`, `posts_count`.
- Obtiene las últimas N publicaciones (por defecto 5): `shortcode`, `url`, `date`, `caption`.
- Funciona sin login para perfiles públicos; soporta login opcional vía variables de entorno para ampliar datos y reducir bloqueos.
- CLI simple para ejecutar desde la terminal y exportar JSON.

## Requisitos
- Python 3.10+
- Dependencias: `instaloader`, `python-dotenv`, `playwright`, `cryptography`

## Instalación

```bash
# Crear y activar un entorno virtual (recomendado)
python -m venv .venv
# Windows
.\.venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt

# Instalar navegador de Playwright (Chromium)
python -m playwright install chromium
```

Opcional: si prefieres `pyproject.toml`, puedes instalar con:

```bash
pip install -e .
```

> Nota: El modo editable (`-e`) requiere un backend de build; este proyecto incluye un `pyproject.toml` mínimo.

## Uso

### Autenticación con Facebook (OAuth)

1) Define variables en `.env`:

```
FB_EMAIL=your_fb_email
FB_PASSWORD=your_fb_password
FB_2FA_CODE=123456                # optional if you have 2FA
AUTH_SECRET_KEY=base64_fernet_key # optional to encrypt storage
HEADLESS=true                     # or false to see the browser
AUTH_STORAGE_PATH=storage/auth_state.enc
AUTH_STORAGE_PLAIN_PATH=storage/storage_state.json
LOG_LEVEL=INFO
```

2) Ejecuta la autenticación:

```bash
python main.py auth --headless false
```

Esto abre el navegador, hace clic en “Iniciar sesión con Facebook”, completa credenciales, maneja 2FA si `FB_2FA_CODE` está definido y guarda el estado de sesión (cookies y storage). Si defines `AUTH_SECRET_KEY`, el archivo se cifra.

Mejoras recientes:
- Maneja automáticamente las pantallas de Instagram “Continuar” / “Usar otro perfil” y acepta banners de cookies.
- Si se detecta una sesión válida tras “Continuar”, guarda el estado sin iniciar sesión en Facebook.
- En Facebook, acepta banners de cookies, espera visibilidad de inputs, usa selectores alternativos y aplica fallbacks JS cuando overlays bloquean la interacción.

### Scraping con sesión de Playwright

```bash
python main.py scrape --url https://www.instagram.com/<username>/ --posts 5 --output profile.json
```

Usa la sesión autenticada para consultar la API `web_profile_info` y obtener datos del perfil y publicaciones recientes.

### Scraping alternativo con Instaloader

```bash
python main.py legacy --url https://www.instagram.com/<username>/ --posts 5 --output profile.json --login
```

Con `--login` se usarán `IG_USERNAME`/`IG_PASSWORD` (y `IG_2FA_CODE` si aplica) desde `.env`.

Parámetros:
- `--url` (requerido): URL del perfil de Instagram.
- `--posts` (opcional): número de publicaciones recientes a obtener (por defecto 5).
- `--output` (opcional): ruta del archivo de salida para guardar el JSON.
- `--login` (opcional, legacy): intenta login si se proveen credenciales.

### Followers of Followers (Excel)

Scrapea los seguidores de un perfil y, para cada uno, obtiene su cantidad de seguidores. Requiere sesión válida (ejecuta primero `auth`).

```bash
python main.py followers \
  --url https://www.instagram.com/<username>/ \
  --limit 50 \
  --output storage/<username>_followers_counts.xlsx
```

- `--limit`: número máximo de seguidores del perfil a procesar.
- `--page-size`: tamaño de página para la API de followers (por defecto 12).
- `--chunk`: cuántos usuarios se consultan por bloque para conteo (recomendado 1–2 para evitar 429).
- `--delay-ms`: pausa entre páginas/bloques en milisegundos (recomendado 5000–8000 bajo presión).
- `--retry_tries` y `--retry_base_ms`: reintentos y backoff para llamadas web.
- `--output`: si termina en `.xlsx`, exporta Excel con columnas `username`, `seguidores`, `primer_digito`; si falla la escritura, imprime JSON.

Detalles de salida y logs:
- `count`: número total de seguidores del perfil (no los procesados), obtenido desde `web_profile_info` cuando está disponible.
- Logs imprimen líneas del tipo `Items recogidos (API|UI): N` y `username: followers` por cada item, además de `Count (followers del perfil): <num>`.
- Cuando la API limita, el scraper cae a modo UI: abre el diálogo de seguidores, scrollea y extrae usernames; el conteo por usuario se obtiene via `web_profile_info` y, si falla, con lectura del `og:description` del perfil.

### Variables de entorno (completo)
Crea un `.env` en la raíz del proyecto:

```
# Instagram (Instaloader)
IG_USERNAME=your_username
IG_PASSWORD=your_password
IG_2FA_CODE=123456

# Default posts limit
POSTS_LIMIT=5

# Facebook OAuth (Playwright)
FB_EMAIL=your_fb_email
FB_PASSWORD=your_fb_password
FB_2FA_CODE=123456

# Browser and storage
HEADLESS=true
AUTH_STORAGE_PATH=storage/auth_state.enc
AUTH_STORAGE_PLAIN_PATH=storage/storage_state.json
AUTH_SECRET_KEY=base64_fernet_key
LOG_LEVEL=INFO
```

> Los perfiles privados requieren login y permisos de visualización.

## Estructura del proyecto

```
instagram-scraper/
├─ .gitignore
├─ README.md
├─ pyproject.toml
├─ main.py
└─ src/
   └─ instagram_scraper/
      ├─ __init__.py
      ├─ config.py
      ├─ utils.py
      ├─ scraper.py
      ├─ browser_scraper.py
      ├─ auth.py
      └─ cli.py
```

## Librerías y razones
- `Playwright` (Python):
  - Gestiona flujos OAuth interactivos (redirige a Facebook y retorna a Instagram).
  - Soporta navegación multi-página y multi-dominio con contextos aislados.
  - Permite scraping avanzado, incluyendo `fetch` autenticado desde el contexto del navegador.
  - Persiste sesiones guardando `storage_state` (cookies y localStorage), con soporte de cifrado.
- `Instaloader`:
  - Alternativa optimizada para scraping vía APIs internas de Instagram usando credenciales directas.
  - Útil cuando OAuth no es necesario.

## Flujo de implementación
1. `auth`: abre el login de Instagram, maneja “Continuar” / banners de cookies, hace clic en “Iniciar sesión con Facebook”, completa credenciales y 2FA (si aplica), regresa autenticado a Instagram y guarda `storage_state` (cookies y storage). Si `AUTH_SECRET_KEY` está definido, el archivo se cifra.
2. `scrape`: descifra y carga `storage_state` si es necesario, crea un contexto y llama a `https://www.instagram.com/api/v1/users/web_profile_info/?username=<user>` para obtener perfil y publicaciones.
3. Exporta JSON con campos estándar y lista de publicaciones limitada por `--posts` o `POSTS_LIMIT`.

## Manejo de errores y logs
- Errores claros cuando faltan variables requeridas (`FB_EMAIL`, `FB_PASSWORD`).
- Validación de la cookie `sessionid` tras el login para confirmar autenticación.
- Logs detallados con `LOG_LEVEL` configurable.
- Mensajes explícitos cuando se requiere 2FA; ejecuta con `HEADLESS=false` para intervenir manualmente si es necesario.

### Rate Limiting (HTTP 429)
- Si ves 429, espera al menos 30–60 min; a veces 24–48 h.
- Reduce el ritmo: `--chunk 1`, `--page-size 12`, `--delay-ms 5000–8000`.
- No paralelices; procesa en bloques: `--limit 100` por corrida y continúa luego.
- Cambia de IP si es necesario: red móvil/tethering, router con IP dinámica, proxies residenciales/móviles.
- Mantén sesión autenticada para menos fricción; evita cerrar sesión/cookies.

## Seguridad de credenciales
- Credenciales en `.env` (no versionadas por `.gitignore`).
- `AUTH_SECRET_KEY` habilita cifrado Fernet del archivo de estado (`auth_state.enc`).
- Los archivos de sesión en `storage/` están excluidos por `.gitignore`.

## Notas de despliegue
- Requiere instalar el navegador de Playwright en el entorno de despliegue (`python -m playwright install chromium`).
- Configura correctamente las variables de entorno y ejecuta `auth` antes de `scrape`.

## Scraping responsable
- Respeta los Términos de Servicio de Instagram y Facebook.
- Usa login solo en cuentas que controlas.
- Evita tasas altas de solicitudes para prevenir bloqueos.

## Licencia
Sin licencia específica; adapta a tus necesidades.