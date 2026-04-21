"""
scraper_imdb.py — Scraper de películas usando OMDb API + caché Redis
Uso: python scraper_imdb.py "Nombre de la película" [--json] [--compact]

OMDb API (https://www.omdbapi.com):
  - 1000 peticiones/día gratis, sin bloqueos
  - API key gratuita en https://www.omdbapi.com/apikey.aspx
"""

import sys
import json
import os
import logging
import hashlib
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

OMDB_API_KEY = os.getenv("OMDB_API_KEY", "")
OMDB_BASE    = "https://www.omdbapi.com/"
REDIS_URL    = os.getenv("REDIS_URL", "redis://localhost:6379")
CACHE_TTL    = int(os.getenv("CACHE_TTL", 86400))   # 24h por defecto

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

_memory_cache: dict = {}

# ---------------------------------------------------------------------------
# Caché (Redis con fallback a memoria)
# ---------------------------------------------------------------------------

def _cache_key(title: str) -> str:
    return "movie:" + hashlib.md5(title.lower().strip().encode()).hexdigest()


def cache_get(title: str) -> Optional[dict]:
    key = _cache_key(title)
    if REDIS_AVAILABLE:
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            data = r.get(key)
            if data:
                log.info("Cache HIT (Redis) para '%s'", title)
                return json.loads(data)
        except redis.RedisError as e:
            log.warning("Redis no disponible (%s), usando cache en memoria", e)
    entry = _memory_cache.get(key)
    if entry:
        log.info("Cache HIT (memoria) para '%s'", title)
        return entry
    return None


def cache_set(title: str, data: dict) -> None:
    key = _cache_key(title)
    if REDIS_AVAILABLE:
        try:
            r = redis.from_url(REDIS_URL, decode_responses=True)
            r.setex(key, CACHE_TTL, json.dumps(data, ensure_ascii=False))
            log.info("Guardado en Redis (TTL=%ds)", CACHE_TTL)
            return
        except redis.RedisError as e:
            log.warning("Redis no disponible (%s), guardando en memoria", e)
    _memory_cache[key] = data

# ---------------------------------------------------------------------------
# OMDb API
# ---------------------------------------------------------------------------

def _clean(val: str) -> str:
    """Normaliza campos: devuelve 'No disponible' si OMDb retorna N/A."""
    return val if val and val.strip() not in ("N/A", "", "None") else "No disponible"


def _omdb_request(params: dict) -> Optional[dict]:
    """Petición genérica a OMDb. Devuelve el JSON o None si hay error."""
    if not OMDB_API_KEY:
        log.error("OMDB_API_KEY no configurada. Añádela al archivo .env")
        return None
    try:
        resp = requests.get(
            OMDB_BASE,
            params={"apikey": OMDB_API_KEY, **params},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("Response") == "False":
            log.warning("OMDb: %s", data.get("Error", "Sin resultados"))
            return None
        return data
    except requests.RequestException as e:
        log.error("Error de red al contactar OMDb: %s", e)
        return None


def _search_omdb(title: str) -> Optional[dict]:
    """
    Búsqueda en dos pasos:
      1. Título exacto (parámetro t=)  → más preciso
      2. Búsqueda libre (parámetro s=) → toma el primer resultado
    """
    data = _omdb_request({"t": title, "plot": "full"})
    if data:
        return data

    log.info("Título exacto no encontrado, probando búsqueda libre...")
    search = _omdb_request({"s": title, "type": "movie"})
    if not search or not search.get("Search"):
        return None

    imdb_id = search["Search"][0].get("imdbID")
    return _omdb_request({"i": imdb_id, "plot": "full"}) if imdb_id else None


def _parse(raw: dict) -> dict:
    """Convierte la respuesta raw de OMDb al formato interno normalizado."""
    rating, votes = None, 0

    try:
        rating = float(raw.get("imdbRating", ""))
    except (ValueError, TypeError):
        pass

    try:
        votes = int(raw.get("imdbVotes", "0").replace(",", ""))
    except (ValueError, TypeError):
        pass

    extra = {r["Source"]: r["Value"] for r in raw.get("Ratings", [])}

    return {
        "title":      _clean(raw.get("Title", "")),
        "year":       _clean(raw.get("Year", "")),
        "rating":     rating,
        "votes":      votes,
        "synopsis":   _clean(raw.get("Plot", "")),
        "director":   _clean(raw.get("Director", "")),
        "duration":   _clean(raw.get("Runtime", "")),
        "genre":      _clean(raw.get("Genre", "")),
        "actors":     _clean(raw.get("Actors", "")),
        "language":   _clean(raw.get("Language", "")),
        "country":    _clean(raw.get("Country", "")),
        "awards":     _clean(raw.get("Awards", "")),
        "imdb_id":    _clean(raw.get("imdbID", "")),
        "poster":     _clean(raw.get("Poster", "")),
        "rated":      _clean(raw.get("Rated", "")),
        "rt_score":   extra.get("Rotten Tomatoes", "No disponible"),
        "metacritic": extra.get("Metacritic", "No disponible"),
        "source":     "omdb",
    }

# ---------------------------------------------------------------------------
# Función pública principal
# ---------------------------------------------------------------------------

def get_movie_info(title: str) -> dict:
    """
    Devuelve información completa de una película dado su título.

    Flujo: caché → OMDb API (exacto → libre) → error

    Retorna un dict con las claves: title, year, rating, votes,
    synopsis, director, duration, genre, actors, imdb_id, poster,
    rt_score, metacritic, source.
    O un dict {"error": "..."} si no se encuentra nada.
    """
    if not title or not title.strip():
        return {"error": "El título no puede estar vacío"}

    title = title.strip()

    cached = cache_get(title)
    if cached:
        return cached

    log.info("Consultando OMDb para: '%s'", title)
    raw = _search_omdb(title)
    if not raw:
        return {"error": f"No se encontró información para '{title}'"}

    data = _parse(raw)
    cache_set(title, data)
    return data


def format_for_display(data: dict, compact: bool = False) -> str:
    """Formatea el resultado como texto para CLI o respuesta de Alexa."""
    if "error" in data:
        return f"Error: {data['error']}"

    rating_str = f"{data['rating']}/10" if data.get("rating") else "No disponible"
    votes_str  = f"{data['votes']:,}".replace(",", ".") if data.get("votes") else "N/D"

    if compact:
        return (
            f"{data['title']} ({data['year']}), dirigida por {data['director']}. "
            f"Duración: {data['duration']}. Nota IMDB: {rating_str}."
        )

    return (
        f"\n{'='*55}\n"
        f"  {data['title']} ({data['year']})\n"
        f"{'='*55}\n"
        f"  Director  : {data['director']}\n"
        f"  Reparto   : {data['actors']}\n"
        f"  Duración  : {data['duration']}\n"
        f"  Género    : {data['genre']}\n"
        f"  Idioma    : {data['language']}\n"
        f"\n"
        f"  IMDB      : {rating_str}  ({votes_str} votos)\n"
        f"  RT        : {data.get('rt_score', 'N/D')}\n"
        f"  Metacritic: {data.get('metacritic', 'N/D')}\n"
        f"\n"
        f"  Sinopsis  :\n"
        f"  {data['synopsis']}\n"
        f"\n"
        f"  Premios   : {data.get('awards', 'N/D')}\n"
        f"  IMDB ID   : {data.get('imdb_id', 'N/D')}\n"
        f"{'='*55}\n"
    )

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Uso:  python scraper_imdb.py "Nombre de la película" [--json] [--compact]')
        print('Ej.:  python scraper_imdb.py "Inception"')
        print('Ej.:  python scraper_imdb.py "El Padrino" --json')
        sys.exit(1)

    movie_title = sys.argv[1]
    as_json     = "--json"    in sys.argv
    as_compact  = "--compact" in sys.argv

    result = get_movie_info(movie_title)

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_for_display(result, compact=as_compact))