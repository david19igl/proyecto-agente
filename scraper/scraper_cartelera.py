"""
scraper_cartelera.py — Cartelera de cine en Madrid desde ecartelera.com
Uso: python scraper/scraper_cartelera.py [--json]
"""

import os
import re
import json
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BASE_URL      = "https://www.ecartelera.com"
CARTELERA_URL = f"{BASE_URL}/cines/0,30,1.html"   # Madrid ciudad

# ---------------------------------------------------------------------------
# Validación de títulos — evita capturar texto de navegación como título
# ---------------------------------------------------------------------------

_BLACKLIST = {
    "películas", "peliculas", "horarios", "cines", "cartelera",
    "estrenos", "sesiones", "ver más", "ver mas", "más info",
    "comprar", "entradas", "trailer", "tráiler", "sinopsis",
    "género", "genero", "director", "actores", "inicio",
    "contacto", "publicidad", "newsletter", "buscar",
}

# Slugs de categoría que no son películas individuales
_SLUG_BLACKLIST = {
    "comedia", "drama", "accion", "terror", "thriller",
    "aventura", "animacion", "documental", "infantil",
    "romantica", "ciencia-ficcion", "fantasia",
}

_JUNK_RE = re.compile(
    r"^\d+\s*cines?$"   # "38 cines"
    r"|^horarios"       # "Horarios: ..."
    r"|^ver\s+"         # "Ver más"
    r"|^\d+$"           # solo números
    r"|^https?://"      # URLs
    r"|.{81,}",         # más de 80 caracteres
    re.IGNORECASE,
)


def _is_valid_title(text: str) -> bool:
    if not text:
        return False
    t = text.strip()
    if len(t) < 2:
        return False
    if t.lower() in _BLACKLIST:
        return False
    if _JUNK_RE.search(t):
        return False
    if not re.search(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]", t):
        return False
    return True


# ---------------------------------------------------------------------------
# Parser del HTML
# ---------------------------------------------------------------------------

def _parse_html(html: str) -> list[dict]:
    """
    Extrae películas del HTML en tres estrategias:
      1. Selectores CSS de tarjeta
      2. JSON-LD embebido (Schema.org)
      3. Fallback: enlaces /peliculas/ con validación de slug
    """
    soup   = BeautifulSoup(html, "html.parser")
    movies = []

    # Estrategia 1 — selectores de tarjeta conocidos en ecartelera
    CARD_SELECTORS = [
        "div.pelicula-item", "div.movie-card", "article.pelicula",
        "li.pelicula", "div.item-pelicula", "div.pelicula",
    ]
    for sel in CARD_SELECTORS:
        cards = soup.select(sel)
        if not cards:
            continue
        log.info("Selector '%s' encontró %d tarjetas", sel, len(cards))
        for card in cards:
            m = _card_data(card)
            if m:
                movies.append(m)
        if movies:
            return movies

    # Estrategia 2 — JSON-LD embebido
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            data  = json.loads(script.string or "")
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get("@type") in ("Movie", "ScreeningEvent"):
                    title = item.get("name", "")
                    if _is_valid_title(title):
                        movies.append({
                            "title":   title,
                            "url":     item.get("url", ""),
                            "genre":   item.get("genre", "No disponible"),
                            "cinemas": [],
                        })
        except (json.JSONDecodeError, AttributeError):
            continue
    if movies:
        log.info("JSON-LD encontró %d películas", len(movies))
        return movies

    # Estrategia 3 — fallback por enlaces /peliculas/
    # El patrón original exigía números al final del slug (/peliculas/titulo-1234/)
    # pero ecartelera usa URLs limpias (/peliculas/scream-7/) sin números obligatorios.
    # Ahora validamos por estructura de ruta en lugar de por expresión regular rígida.
    log.warning("Usando fallback por enlaces /peliculas/")
    seen_titles: set[str] = set()
    seen_urls:   set[str] = set()

    links = soup.select("a[href*='/peliculas/']")
    for link in links:
        title = link.get_text(strip=True)
        href  = link.get("href", "")
        if href.startswith("/"):
            href = BASE_URL + href

        if not _is_valid_title(title):
            continue

        # Descartar listados generales: la URL debe tener un slug de película real.
        # Estructura esperada: /peliculas/<slug>/  (puede o no tener trailing slash)
        path     = href.replace(BASE_URL, "").strip("/")
        segments = [s for s in path.split("/") if s]

        # Debe ser exactamente ["peliculas", "slug-de-pelicula"]
        if len(segments) != 2 or segments[0] != "peliculas":
            continue

        slug = segments[1]

        # El slug debe tener al menos 3 caracteres y no ser una categoría genérica
        if len(slug) < 3 or slug in _SLUG_BLACKLIST:
            continue

        # Evitar duplicados por título y por URL
        if title in seen_titles or href in seen_urls:
            continue

        seen_titles.add(title)
        seen_urls.add(href)
        movies.append({
            "title":   title,
            "url":     href,
            "genre":   "No disponible",
            "cinemas": [],
        })

    log.info("Fallback encontró %d películas", len(movies))
    return movies


def _card_data(card) -> Optional[dict]:
    """Extrae título, URL y género de una tarjeta HTML."""
    try:
        title = ""
        for sel in ["h2", "h3", "h4", ".titulo", ".title", "a"]:
            el = card.select_one(sel)
            if el:
                candidate = el.get_text(strip=True)
                if _is_valid_title(candidate):
                    title = candidate
                    break
        if not title:
            return None

        link = card.select_one("a[href]")
        href = link["href"] if link else ""
        url  = (BASE_URL + href) if href.startswith("/") else href

        genre_el = card.select_one(".genero, .genre, .categoria, span.cat")
        genre    = genre_el.get_text(strip=True) if genre_el else "No disponible"

        return {"title": title, "url": url, "genre": genre, "cinemas": []}
    except Exception as e:
        log.debug("Error en tarjeta: %s", e)
        return None


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------

def get_cartelera_madrid() -> list[dict]:
    """
    Devuelve la lista de películas en cartelera en Madrid.
    Cada elemento tiene: title, url, genre, cinemas.
    """
    log.info("Descargando cartelera de Madrid desde ecartelera.com...")
    try:
        resp = requests.get(CARTELERA_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Error al descargar la cartelera: %s", e)
        return []

    movies = _parse_html(resp.text)
    log.info("Total películas obtenidas: %d", len(movies))
    return movies


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    as_json = "--json" in sys.argv

    cartelera = get_cartelera_madrid()

    if as_json:
        print(json.dumps(cartelera, ensure_ascii=False, indent=2))
    else:
        print(f"\nCartelera de Madrid — {len(cartelera)} películas\n{'='*45}")
        for m in cartelera:
            print(f"\n  {m['title']}")
            if m["genre"] != "No disponible":
                print(f"  Género : {m['genre']}")
        print()