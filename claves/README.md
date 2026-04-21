# Agente Inteligente de Películas

Sistema multicanal que combina **OMDb API**, **Redis**, **Docker**, **Alexa**, **Telegram** y un reporte semanal automático de la cartelera de Madrid.

---

## Estructura de carpetas

Esta es la estructura completa del proyecto. Créala exactamente así:

```
proyecto-agente/
│
├── scraper/                        ← paquete Python principal
│   ├── __init__.py                 ← hace la carpeta importable (no tocar)
│   ├── scraper_imdb.py             ← consulta OMDb API + caché Redis
│   ├── scraper_cartelera.py        ← cartelera de Madrid (ecartelera.com)
│   └── weekly_report.py            ← fusiona ambos scrapers y notifica
│
├── alexa/                          ← código de la Alexa Skill
│   ├── lambda_function.py          ← handler principal de la Lambda
│   └── interactionModel.json       ← intents y utterances del modelo de voz
│
├── telegram_bot/                   ← bot de Telegram
│   └── bot.py                      ← comandos /pelicula, /cartelera, /setprefs
│
├── web/                            ← interfaz web Flask
│   └── app.py                      ← rutas y vistas
│
├── data/                           ← datos persistentes (creada automáticamente)
│   └── user_prefs.json             ← preferencias del usuario (género, director...)
│
├── logs/                           ← logs del sistema (creada automáticamente)
│   └── cron.log                    ← salida del reporte semanal automático
│
├── docker-compose.yml              ← orquestación de contenedores
├── Dockerfile                      ← imagen base compartida por todos los servicios
├── requirements.txt                ← dependencias Python
├── .env.example                    ← plantilla de variables de entorno
├── .env                            ← tu configuración real (NO subir a git)
├── .gitignore                      ← excluye .env, logs/, data/, __pycache__/
└── README.md                       ← este archivo
```

### Dónde va cada archivo entregado

| Archivo recibido | Dónde colocarlo |
|-----------------|-----------------|
| `scraper_imdb.py` | `scraper/scraper_imdb.py` |
| `scraper_cartelera.py` | `scraper/scraper_cartelera.py` |
| `weekly_report.py` | `scraper/weekly_report.py` |
| `__init__.py` (vacío) | `scraper/__init__.py` |
| `docker-compose.yml` | raíz del proyecto |
| `Dockerfile` | raíz del proyecto |
| `requirements.txt` | raíz del proyecto |
| `.env.example` | raíz del proyecto |

---

## Requisitos previos

| Herramienta | Versión mínima | Enlace |
|-------------|---------------|--------|
| Docker Desktop | 24+ | https://docs.docker.com/get-docker/ |
| Python | 3.11+ | Solo para desarrollo sin Docker |
| Cuenta OMDb | Gratuita | https://www.omdbapi.com/apikey.aspx |
| Bot de Telegram | — | @BotFather en Telegram |

---

## Instalación paso a paso

### Paso 1 — Crear la estructura de carpetas

```bash
mkdir proyecto-agente
cd proyecto-agente
mkdir scraper alexa telegram_bot web data logs
touch scraper/__init__.py
```

### Paso 2 — Copiar los archivos en su lugar

Coloca cada archivo según la tabla de arriba.

### Paso 3 — Configurar las variables de entorno

```bash
cp .env.example .env
```

Edita `.env` y rellena al menos estas tres variables:

```env
OMDB_API_KEY=tu_api_key
TELEGRAM_TOKEN=123456:ABCdef...
TELEGRAM_CHAT_ID=123456789
```

**Cómo obtener la API key de OMDb:**
1. Ve a https://www.omdbapi.com/apikey.aspx
2. Elige el plan FREE (1000 req/día)
3. Recibirás la key por email en minutos

**Cómo obtener tu TELEGRAM_CHAT_ID:**
1. Abre Telegram y habla con `@userinfobot`
2. Te responde con tu ID numérico

**Cómo crear el bot de Telegram:**
1. Habla con `@BotFather` en Telegram
2. Escribe `/newbot` y sigue las instrucciones
3. Copia el token y pégalo en `TELEGRAM_TOKEN`

### Paso 4 — Crear el .gitignore

```bash
cat > .gitignore << 'EOF'
.env
logs/
data/
__pycache__/
*.pyc
.DS_Store
EOF
```

### Paso 5 — Arrancar con Docker

```bash
docker compose up --build
```

Servicios disponibles tras el arranque:

| Servicio | Dónde |
|----------|-------|
| Web UI | http://localhost:5000 |
| Redis | localhost:6379 |
| Telegram Bot | activo automáticamente |
| Cron (lunes 09:00) | en background |

Para arrancar en background:

```bash
docker compose up -d --build
```

---

## Uso del scraper por línea de comandos

### Sin Docker (desarrollo local)

```bash
pip install -r requirements.txt
```

Ejecuta siempre desde la raíz del proyecto (donde está `docker-compose.yml`):

```bash
# Información de una película (salida formateada)
python scraper/scraper_imdb.py "Inception"

# Salida JSON
python scraper/scraper_imdb.py "El Padrino" --json

# Salida compacta (una línea, formato Alexa)
python scraper/scraper_imdb.py "Dune" --compact

# Cartelera de Madrid
python scraper/scraper_cartelera.py

# Cartelera con cines y horarios (más lento)
python scraper/scraper_cartelera.py --detail --json

# Reporte semanal completo (cartelera + OMDb + Telegram)
python scraper/weekly_report.py
```

### Con Docker

```bash
# Información de una película
docker compose exec scraper python scraper/scraper_imdb.py "Inception"

# Cartelera de Madrid
docker compose exec scraper python scraper/scraper_cartelera.py

# Lanzar el reporte semanal ahora (sin esperar al lunes)
docker compose exec scraper python scraper/weekly_report.py
```

---

## Filtrado por preferencias (bonus)

El reporte semanal se filtra según `data/user_prefs.json`. Puedes editarlo a mano o desde el bot con `/setprefs`.

```json
{
  "min_rating": 7.0,
  "genres": ["Drama", "Thriller"],
  "directors": ["Nolan", "Villeneuve"],
  "notify_method": "telegram"
}
```

Con esta configuración el reporte solo incluye películas con nota >= 7, de género Drama o Thriller, o dirigidas por Nolan o Villeneuve.

---

## Alexa Skill

### Despliegue en AWS Lambda

1. Comprime el contenido de `alexa/` en un `.zip`
2. En AWS Lambda, crea una función con runtime Python 3.11
3. Sube el `.zip` como código de la función
4. Añade las variables de entorno: `OMDB_API_KEY` y `REDIS_URL`
5. En la Alexa Developer Console, vincula el ARN de la Lambda como endpoint
6. En la pestaña "JSON Editor" del modelo de voz, pega `interactionModel.json`

### Probar sin Echo físico

La Alexa Developer Console incluye un simulador en la pestaña **Test**. Escribe o habla directamente sin necesidad de ningún dispositivo.

### Intents disponibles

| Intent | Ejemplo de frase |
|--------|-----------------|
| `BuscarPelicula` | "Busca información de Inception" |
| `ObtenerNota` | "¿Qué nota tiene El Padrino?" |
| `ObtenerDirector` | "¿Quién dirigió Dune?" |
| `ObtenerSinopsis` | "Cuéntame de qué va Interstellar" |
| `ObtenerDuracion` | "¿Cuánto dura Oppenheimer?" |
| `VerCartelera` | "¿Qué hay en cartelera esta semana?" |

---

## Telegram Bot

| Comando | Descripción |
|---------|-------------|
| `/pelicula [nombre]` | Información completa de una película |
| `/cartelera` | Cartelera de Madrid esta semana |
| `/setprefs` | Configurar preferencias de filtrado |
| `/help` | Ayuda |

---

## Comandos Docker de referencia

```bash
# Arrancar todo
docker compose up -d --build

# Parar todo
docker compose down

# Logs en tiempo real
docker compose logs -f
docker compose logs -f scraper

# Lanzar reporte ahora
docker compose exec scraper python scraper/weekly_report.py

# Vaciar caché Redis
docker compose exec redis redis-cli FLUSHALL

# Ver claves en Redis
docker compose exec redis redis-cli KEYS "movie:*"

# Reconstruir un solo servicio
docker compose up -d --build web
```

---

## Variables de entorno — referencia

| Variable | Obligatoria | Descripción |
|----------|-------------|-------------|
| `OMDB_API_KEY` | Sí | API key de omdbapi.com |
| `TELEGRAM_TOKEN` | Sí | Token del bot de Telegram |
| `TELEGRAM_CHAT_ID` | Sí | ID del chat para los reportes |
| `REDIS_URL` | No | Por defecto `redis://redis:6379` |
| `CACHE_TTL` | No | TTL del caché en segundos (86400 = 24h) |
| `CRON_SCHEDULE` | No | Expresión crontab del reporte semanal |
| `SMTP_HOST` | No | Servidor SMTP para email |
| `SMTP_USER` | No | Usuario SMTP |
| `SMTP_PASS` | No | Contraseña de aplicación SMTP |
| `NOTIFY_EMAIL` | No | Email destinatario |
| `FLASK_ENV` | No | `development` o `production` |
| `LOG_LEVEL` | No | `DEBUG`, `INFO`, `WARNING` |