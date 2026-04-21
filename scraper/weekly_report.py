"""
weekly_report.py — Reporte semanal de cartelera de Madrid
Ejecutado automáticamente por cron cada lunes a las 09:00.
También puede lanzarse manualmente:
  python scraper/weekly_report.py
  docker compose exec scraper python scraper/weekly_report.py
"""

import os
import sys
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Optional

# Añadir la raíz del proyecto al path para importar módulos hermanos
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

# Importar scrapers del mismo paquete
from scraper.scraper_imdb      import get_movie_info
from scraper.scraper_cartelera import get_cartelera_madrid

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
# Preferencias de usuario (guardadas en JSON local)
# ---------------------------------------------------------------------------

PREFS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "user_prefs.json")


def load_prefs() -> dict:
    """Carga las preferencias del usuario desde el archivo JSON."""
    default = {
        "min_rating":     0.0,    # nota mínima (0 = sin filtro)
        "genres":         [],     # géneros preferidos, ej. ["Action", "Drama"]
        "directors":      [],     # directores favoritos, ej. ["Nolan"]
        "notify_method":  "telegram",  # "telegram" o "email"
    }
    try:
        if os.path.exists(PREFS_FILE):
            with open(PREFS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
                return {**default, **saved}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("No se pudieron cargar preferencias: %s. Usando valores por defecto.", e)
    return default


def save_prefs(prefs: dict) -> None:
    os.makedirs(os.path.dirname(PREFS_FILE), exist_ok=True)
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Filtrado
# ---------------------------------------------------------------------------

def apply_filters(movies: list[dict], prefs: dict) -> list[dict]:
    """
    Filtra la lista de películas enriquecidas según las preferencias del usuario.
    movies: lista de dicts con datos fusionados de cartelera + OMDb.
    """
    filtered = []
    min_rating = prefs.get("min_rating", 0.0)
    genres     = [g.lower() for g in prefs.get("genres", [])]
    directors  = [d.lower() for d in prefs.get("directors", [])]

    for movie in movies:
        imdb_data = movie.get("imdb", {})
        rating    = imdb_data.get("rating") or 0.0

        # Filtro por nota mínima
        if min_rating > 0 and rating < min_rating:
            continue

        # Filtro por género (OR: basta con que coincida uno)
        if genres:
            movie_genres = imdb_data.get("genre", "").lower()
            if not any(g in movie_genres for g in genres):
                continue

        # Filtro por director (OR)
        if directors:
            movie_director = imdb_data.get("director", "").lower()
            if not any(d in movie_director for d in directors):
                continue

        filtered.append(movie)

    log.info("Películas tras filtrado: %d/%d", len(filtered), len(movies))
    return filtered

# ---------------------------------------------------------------------------
# Construcción del reporte
# ---------------------------------------------------------------------------

def build_report(movies: list[dict], prefs: dict) -> tuple[str, str]:
    """
    Construye el texto del reporte en dos formatos:
      - Markdown (para Telegram)
      - Texto plano (para email)
    Devuelve (markdown, plain_text).
    """
    date_str = datetime.now().strftime("%d/%m/%Y")
    total    = len(movies)

    # Cabecera
    md_lines   = [f"🎬 *Cartelera de Madrid — {date_str}*\n_{total} películas en cartelera_\n"]
    txt_lines  = [f"CARTELERA DE MADRID — {date_str}", f"{total} películas en cartelera", "="*50]

    # Filtros activos
    if prefs.get("min_rating") or prefs.get("genres") or prefs.get("directors"):
        filters_txt = []
        if prefs.get("min_rating"):
            filters_txt.append(f"Nota ≥ {prefs['min_rating']}")
        if prefs.get("genres"):
            filters_txt.append(f"Géneros: {', '.join(prefs['genres'])}")
        if prefs.get("directors"):
            filters_txt.append(f"Directores: {', '.join(prefs['directors'])}")
        filtro_str = " | ".join(filters_txt)
        md_lines.append(f"_Filtros: {filtro_str}_\n")
        txt_lines.append(f"Filtros: {filtro_str}")
        txt_lines.append("="*50)

    if not movies:
        md_lines.append("_No hay películas que coincidan con tus preferencias esta semana._")
        txt_lines.append("\nNo hay películas que coincidan con tus preferencias esta semana.")
        return "\n".join(md_lines), "\n".join(txt_lines)

    # Películas ordenadas por nota IMDB (descendente)
    sorted_movies = sorted(
        movies,
        key=lambda m: m.get("imdb", {}).get("rating") or 0,
        reverse=True,
    )

    for i, movie in enumerate(sorted_movies, 1):
        imdb    = movie.get("imdb", {})
        title   = imdb.get("title") or movie.get("title", "Desconocida")
        year    = imdb.get("year", "")
        rating  = imdb.get("rating")
        director = imdb.get("director", "N/D")
        duration = imdb.get("duration", "N/D")
        genre    = imdb.get("genre", movie.get("genre", "N/D"))
        synopsis = imdb.get("synopsis", "Sin sinopsis disponible")
        cinemas  = movie.get("cinemas", [])

        rating_str = f"⭐ {rating}/10" if rating else "Sin nota"
        cinemas_str = ", ".join(cinemas[:3]) + ("..." if len(cinemas) > 3 else "") if cinemas else "Ver cines en ecartelera.com"

        # Markdown (Telegram)
        md_lines.append(
            f"*{i}. {title}* ({year})\n"
            f"{rating_str} | {duration} | {genre}\n"
            f"Director: {director}\n"
            f"_{synopsis[:200]}{'...' if len(synopsis) > 200 else ''}_\n"
            f"📍 {cinemas_str}\n"
        )

        # Texto plano (email)
        txt_lines.append(
            f"\n{i}. {title} ({year})\n"
            f"   Nota: {rating_str} | Duración: {duration}\n"
            f"   Género: {genre}\n"
            f"   Director: {director}\n"
            f"   Sinopsis: {synopsis[:200]}{'...' if len(synopsis) > 200 else ''}\n"
            f"   Cines: {cinemas_str}"
        )

    md_lines.append("\n_Generado automáticamente por el Agente de Películas_")
    txt_lines.append("\n" + "="*50)
    txt_lines.append("Generado automáticamente por el Agente de Películas")

    return "\n".join(md_lines), "\n".join(txt_lines)

# ---------------------------------------------------------------------------
# Envío por Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> bool:
    token   = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        log.warning("TELEGRAM_TOKEN o TELEGRAM_CHAT_ID no configurados")
        return False

    try:
        import telegram
        import asyncio

        async def _send():
            bot = telegram.Bot(token=token)
            # Telegram limita los mensajes a 4096 caracteres
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await bot.send_message(
                    chat_id=chat_id,
                    text=chunk,
                    parse_mode="Markdown",
                )

        asyncio.run(_send())
        log.info("Reporte enviado por Telegram a chat_id=%s", chat_id)
        return True

    except Exception as e:
        log.error("Error enviando por Telegram: %s", e)
        return False

# ---------------------------------------------------------------------------
# Envío por Email
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> bool:
    smtp_host   = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port   = int(os.getenv("SMTP_PORT", 587))
    smtp_user   = os.getenv("SMTP_USER", "")
    smtp_pass   = os.getenv("SMTP_PASS", "")
    notify_email = os.getenv("NOTIFY_EMAIL", "")

    if not all([smtp_user, smtp_pass, notify_email]):
        log.warning("Credenciales de email no configuradas")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = smtp_user
        msg["To"]      = notify_email
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, notify_email, msg.as_string())

        log.info("Reporte enviado por email a %s", notify_email)
        return True

    except Exception as e:
        log.error("Error enviando email: %s", e)
        return False

# ---------------------------------------------------------------------------
# Orquestador principal
# ---------------------------------------------------------------------------

def run_weekly_report() -> None:
    log.info("=== Iniciando reporte semanal ===")

    # 1. Cargar preferencias
    prefs = load_prefs()
    log.info("Preferencias: %s", prefs)

    # 2. Obtener cartelera de Madrid
    cartelera = get_cartelera_madrid(with_details=False)
    if not cartelera:
        log.error("No se pudo obtener la cartelera. Abortando.")
        return

    # 3. Enriquecer con datos de OMDb (rating, sinopsis, director...)
    log.info("Enriqueciendo %d películas con datos de OMDb...", len(cartelera))
    enriched = []
    for movie in cartelera:
        imdb_data = get_movie_info(movie["title"])
        if "error" not in imdb_data:
            movie["imdb"] = imdb_data
            enriched.append(movie)
        else:
            # Incluir igualmente aunque no tenga datos de OMDb
            movie["imdb"] = {}
            enriched.append(movie)
            log.debug("Sin datos OMDb para '%s'", movie["title"])

    # 4. Filtrar según preferencias del usuario
    filtered = apply_filters(enriched, prefs)

    # 5. Construir el reporte
    md_text, plain_text = build_report(filtered, prefs)

    # 6. Enviar por el método configurado
    method = prefs.get("notify_method", "telegram")
    date_str = datetime.now().strftime("%d/%m/%Y")

    if method == "telegram":
        ok = send_telegram(md_text)
    else:
        ok = send_email(f"Cartelera de Madrid — {date_str}", plain_text)

    if ok:
        log.info("=== Reporte semanal completado con éxito ===")
    else:
        log.error("=== El reporte no pudo enviarse ===")
        # Imprimir en stdout como fallback
        print(plain_text)


# ---------------------------------------------------------------------------
# CLI / Cron
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_weekly_report()