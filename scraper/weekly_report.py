"""
weekly_report.py — Reporte semanal de cartelera de Madrid
Se ejecuta automáticamente cada lunes a las 09:00 via cron.
También se puede lanzar manualmente:
  python scraper/weekly_report.py
  docker compose exec scraper python scraper/weekly_report.py

CÓMO FUNCIONA EL FILTRADO:
  Las preferencias se guardan en data/user_prefs.json.
  Campos disponibles:
    min_rating  (float) — nota mínima de IMDB, ej: 7.0
    genres      (list)  — géneros preferidos, ej: ["Drama", "Action"]
    directors   (list)  — directores favoritos, ej: ["Nolan", "Villeneuve"]
    notify_method (str) — "telegram" o "email"

  Una película pasa el filtro si cumple TODAS las condiciones activas.
  Si un campo está vacío o es 0, ese filtro no se aplica.

  Ejemplo: min_rating=7.0, genres=["Drama"]
    → Solo películas con nota >= 7 Y de género Drama.
"""

import os
import sys
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from scraper.scraper_imdb      import get_movie_info
from scraper.scraper_cartelera import get_cartelera_madrid

# ---------------------------------------------------------------------------
# Logging con salida a consola y archivo
# ---------------------------------------------------------------------------

os.makedirs(os.path.join(os.path.dirname(__file__), "..", "logs"), exist_ok=True)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            os.path.join(os.path.dirname(__file__), "..", "logs", "cron.log"),
            encoding="utf-8",
        ),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Preferencias de usuario
# ---------------------------------------------------------------------------

PREFS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "user_prefs.json")

DEFAULT_PREFS = {
    "min_rating":    0.0,    # 0 = sin filtro de nota
    "genres":        [],     # [] = todos los géneros
    "directors":     [],     # [] = todos los directores
    "notify_method": "telegram",
}


def load_prefs() -> dict:
    try:
        if os.path.exists(PREFS_FILE):
            with open(PREFS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
                return {**DEFAULT_PREFS, **saved}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("No se cargaron preferencias: %s. Usando valores por defecto.", e)

    # El archivo no existe todavía: crearlo con los valores por defecto
    # para que el usuario pueda editarlo fácilmente
    prefs = DEFAULT_PREFS.copy()
    save_prefs(prefs)
    log.info("Creado data/user_prefs.json con valores por defecto")
    return prefs


def save_prefs(prefs: dict) -> None:
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Filtrado de películas
#
# Cómo funciona:
#   Para cada película ya enriquecida con OMDb, se comprueba:
#     1. Si min_rating > 0  → la nota IMDB debe ser >= min_rating
#     2. Si genres no vacío → al menos uno de esos géneros debe aparecer
#        en el campo "genre" que devuelve OMDb (comparación insensible a mayúsculas)
#     3. Si directors no vacío → al menos uno de esos directores debe aparecer
#        en el campo "director" que devuelve OMDb
#
#   Los filtros son ACUMULATIVOS (AND): una película debe cumplir todos
#   los que estén activos para aparecer en el reporte.
#
# Ejemplo práctico:
#   prefs = {"min_rating": 7.5, "genres": ["Thriller"], "directors": []}
#   → Muestra solo películas con nota >= 7.5 y de género Thriller.
#   → El filtro de director está desactivado (lista vacía).
# ---------------------------------------------------------------------------

def apply_filters(movies: list[dict], prefs: dict) -> list[dict]:
    min_rating = prefs.get("min_rating", 0.0)
    genres     = [g.lower().strip() for g in prefs.get("genres", []) if g.strip()]
    directors  = [d.lower().strip() for d in prefs.get("directors", []) if d.strip()]

    # Mostrar qué filtros están activos
    active = []
    if min_rating > 0:
        active.append(f"nota >= {min_rating}")
    if genres:
        active.append(f"géneros: {genres}")
    if directors:
        active.append(f"directores: {directors}")
    if active:
        log.info("Filtros activos: %s", " | ".join(active))
    else:
        log.info("Sin filtros: se incluyen todas las películas")

    filtered = []
    for movie in movies:
        imdb   = movie.get("imdb", {})
        rating = imdb.get("rating") or 0.0

        # Filtro 1: nota mínima
        if min_rating > 0 and rating < min_rating:
            log.debug("  Descartada '%s' (nota %.1f < %.1f)", movie["title"], rating, min_rating)
            continue

        # Filtro 2: género (basta con que uno coincida)
        if genres:
            movie_genre = imdb.get("genre", "").lower()
            if not any(g in movie_genre for g in genres):
                log.debug("  Descartada '%s' (género '%s' no en %s)", movie["title"], movie_genre, genres)
                continue

        # Filtro 3: director (basta con que uno coincida)
        if directors:
            movie_director = imdb.get("director", "").lower()
            if not any(d in movie_director for d in directors):
                log.debug("  Descartada '%s' (director '%s' no en %s)", movie["title"], movie_director, directors)
                continue

        filtered.append(movie)

    log.info("Películas tras filtrado: %d / %d", len(filtered), len(movies))
    return filtered

# ---------------------------------------------------------------------------
# Construcción del reporte
# ---------------------------------------------------------------------------

def build_report(movies: list[dict], prefs: dict) -> tuple[str, str]:
    """Devuelve (texto_markdown_telegram, texto_plano_email)."""
    date_str = datetime.now().strftime("%d/%m/%Y")

    md_lines  = [f"🎬 *Cartelera Madrid — {date_str}*\n_{len(movies)} películas_\n"]
    txt_lines = [f"CARTELERA MADRID — {date_str}", f"{len(movies)} películas", "="*50]

    # Resumen de filtros aplicados
    filtros = []
    if prefs.get("min_rating"):
        filtros.append(f"Nota ≥ {prefs['min_rating']}")
    if prefs.get("genres"):
        filtros.append(f"Géneros: {', '.join(prefs['genres'])}")
    if prefs.get("directors"):
        filtros.append(f"Directores: {', '.join(prefs['directors'])}")
    if filtros:
        linea = " | ".join(filtros)
        md_lines.append(f"_Filtros: {linea}_\n")
        txt_lines.append(f"Filtros: {linea}")

    if not movies:
        msg = "No hay películas que coincidan con tus preferencias esta semana."
        md_lines.append(f"_{msg}_")
        txt_lines.append(f"\n{msg}")
        return "\n".join(md_lines), "\n".join(txt_lines)

    # Ordenar por nota IMDB descendente
    sorted_movies = sorted(
        movies,
        key=lambda m: m.get("imdb", {}).get("rating") or 0,
        reverse=True,
    )

    for i, movie in enumerate(sorted_movies, 1):
        imdb     = movie.get("imdb", {})
        title    = imdb.get("title") or movie["title"]
        year     = imdb.get("year", "")
        rating   = imdb.get("rating")
        director = imdb.get("director", "N/D")
        duration = imdb.get("duration", "N/D")
        genre    = imdb.get("genre") or movie.get("genre", "N/D")
        synopsis = imdb.get("synopsis", "Sin sinopsis")

        rating_str = f"⭐ {rating}/10" if rating else "Sin nota en IMDB"

        # Truncar sinopsis a 200 caracteres
        synopsis_short = (synopsis[:197] + "...") if len(synopsis) > 200 else synopsis

        # Formato Markdown para Telegram
        md_lines.append(
            f"*{i}. {title}* ({year})\n"
            f"{rating_str} · {duration} · {genre}\n"
            f"🎬 {director}\n"
            f"_{synopsis_short}_\n"
        )

        # Formato texto plano para email
        txt_lines.append(
            f"\n{i}. {title} ({year})\n"
            f"   Nota: {rating_str} | Duración: {duration}\n"
            f"   Género: {genre} | Director: {director}\n"
            f"   Sinopsis: {synopsis_short}"
        )

    md_lines.append("_Generado por el Agente de Películas_")
    txt_lines.append("\n" + "="*50 + "\nGenerado por el Agente de Películas")

    return "\n".join(md_lines), "\n".join(txt_lines)

# ---------------------------------------------------------------------------
# Envío por Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> bool:
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.warning("TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados en .env")
        return False
    try:
        import telegram
        import asyncio

        async def _send():
            bot    = telegram.Bot(token=token)
            # Telegram limita mensajes a 4096 caracteres: dividir si es necesario
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await bot.send_message(chat_id=chat_id, text=chunk, parse_mode="Markdown")

        asyncio.run(_send())
        log.info("Reporte enviado por Telegram (chat_id=%s)", chat_id)
        return True
    except Exception as e:
        log.error("Error enviando Telegram: %s", e)
        return False

# ---------------------------------------------------------------------------
# Envío por Email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> bool:
    host  = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port  = int(os.getenv("SMTP_PORT", 587))
    user  = os.getenv("SMTP_USER", "")
    pwd   = os.getenv("SMTP_PASS", "")
    dest  = os.getenv("NOTIFY_EMAIL", "")
    if not all([user, pwd, dest]):
        log.warning("Credenciales de email no configuradas en .env")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = user
        msg["To"]      = dest
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP(host, port) as s:
            s.ehlo()
            s.starttls()
            s.login(user, pwd)
            s.sendmail(user, dest, msg.as_string())
        log.info("Reporte enviado por email a %s", dest)
        return True
    except Exception as e:
        log.error("Error enviando email: %s", e)
        return False

# ---------------------------------------------------------------------------
# Orquestador principal
# ---------------------------------------------------------------------------

def run_weekly_report() -> None:
    log.info("=== Iniciando reporte semanal ===")

    # 1. Preferencias
    prefs = load_prefs()
    log.info("Preferencias cargadas: %s", prefs)

    # 2. Cartelera de Madrid
    cartelera = get_cartelera_madrid()
    if not cartelera:
        log.error("No se pudo obtener la cartelera. Abortando.")
        return
    log.info("Cartelera obtenida: %d películas", len(cartelera))

    # 3. Enriquecer con OMDb — solo títulos válidos (longitud razonable)
    log.info("Enriqueciendo con OMDb API...")
    enriched = []
    for movie in cartelera:
        title = movie["title"]

        # Guardia extra: descartar si el título sigue pareciendo basura
        if len(title) < 2 or len(title) > 80:
            log.debug("Título ignorado (longitud inválida): '%s'", title)
            continue

        imdb_data = get_movie_info(title)
        if "error" not in imdb_data:
            movie["imdb"] = imdb_data
        else:
            log.debug("Sin datos OMDb para '%s'", title)
            movie["imdb"] = {}
        enriched.append(movie)

    # 4. Filtrar según preferencias
    filtered = apply_filters(enriched, prefs)

    # 5. Construir reporte
    md_text, plain_text = build_report(filtered, prefs)

    # 6. Enviar
    method   = prefs.get("notify_method", "telegram")
    date_str = datetime.now().strftime("%d/%m/%Y")

    if method == "telegram":
        ok = send_telegram(md_text)
    else:
        ok = send_email(f"Cartelera Madrid — {date_str}", plain_text)

    if ok:
        log.info("=== Reporte semanal enviado con éxito ===")
    else:
        log.warning("No se pudo enviar. Mostrando en consola:\n")
        print(plain_text)


if __name__ == "__main__":
    run_weekly_report()