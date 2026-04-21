"""
scraper_cartelera.py — Cartelera de cine en Madrid desde ecartelera.com
Uso: python scraper/scraper_cartelera.py [--json]
"""

import os
import json
import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}

BASE_URL = "https://www.ecartelera.com"
MADRID_URL = f"{BASE_URL}/cines/madrid/"


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def fetch_cartelera_madrid() -> list[dict]:
    """
    Obtiene la lista de películas en cartelera en Madrid desde ecartelera.com.
    Devuelve una lista de dicts con: title, url, genre, cinemas.
    """
    log.info("Descargando cartelera de Madrid desde ecartelera.com...")

    try:
        resp = requests.get(MADRID_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.error("Error al descargar la cartelera: %s", e)
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    movies = []

    # ecartelera lista las películas en tarjetas con clase "movie-card" o similar
    # Buscamos los enlaces a páginas de película dentro de la sección de cartelera
    cards = soup.select("div.pelicula, article.movie, div.movie-item, li.pelicula")

    # Fallback: buscar por estructura de enlaces si el selector principal no funciona
    if not cards:
        log.warning("Selector principal sin resultados, usando fallback...")
        cards = soup.select("a[href*='/peliculas/']")
        movies = _parse_links_fallback(cards)
        return movies

    for card in cards:
        movie = _parse_card(card)
        if movie:
            movies.append(movie)

    log.info("Encontradas %d películas en cartelera", len(movies))
    return movies


def _parse_card(card) -> Optional[dict]:
    """Extrae datos de una tarjeta de película."""
    try:
        # Título
        title_el = card.select_one("h2, h3, .title, .titulo, a")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)
        if not title:
            return None

        # URL de detalle
        link_el = card.select_one("a[href]")
        url = BASE_URL + link_el["href"] if link_el and link_el["href"].startswith("/") else ""

        # Género (si aparece en la tarjeta)
        genre_el = card.select_one(".genero, .genre, span.cat")
        genre = genre_el.get_text(strip=True) if genre_el else "No disponible"

        # Cines donde se proyecta
        cinemas = []
        cinema_els = card.select(".cine, .cinema, li.cine")
        for c in cinema_els:
            name = c.get_text(strip=True)
            if name:
                cinemas.append(name)

        return {
            "title":   title,
            "url":     url,
            "genre":   genre,
            "cinemas": cinemas,
        }
    except Exception as e:
        log.debug("Error parseando tarjeta: %s", e)
        return None


def _parse_links_fallback(links) -> list[dict]:
    """Fallback: extrae títulos desde los enlaces /peliculas/ de la página."""
    seen = set()
    movies = []
    for link in links:
        title = link.get_text(strip=True)
        href  = link.get("href", "")
        if not title or title in seen:
            continue
        seen.add(title)
        movies.append({
            "title":   title,
            "url":     BASE_URL + href if href.startswith("/") else href,
            "genre":   "No disponible",
            "cinemas": [],
        })
    return movies


def fetch_movie_detail(url: str) -> dict:
    """
    Visita la página de detalle de una película en ecartelera
    para obtener los cines y horarios de Madrid.
    """
    if not url:
        return {"cinemas": [], "showtimes": []}

    time.sleep(0.5)  # pausa respetuosa entre peticiones
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.debug("Error al obtener detalle de %s: %s", url, e)
        return {"cinemas": [], "showtimes": []}

    soup = BeautifulSoup(resp.text, "html.parser")

    cinemas   = [el.get_text(strip=True) for el in soup.select(".cine-name, .nombre-cine, h3.cine")]
    showtimes = [el.get_text(strip=True) for el in soup.select(".horario, .showtime, span.hora")]

    return {
        "cinemas":   cinemas[:10],    # limitar a 10 cines
        "showtimes": showtimes[:20],  # limitar a 20 horarios
    }


# ---------------------------------------------------------------------------
# Función pública
# ---------------------------------------------------------------------------

def get_cartelera_madrid(with_details: bool = False) -> list[dict]:
    """
    Devuelve la cartelera completa de Madrid.

    Si with_details=True, visita la página de cada película para obtener
    cines y horarios (más lento, ~0.5s por película).
    """
    movies = fetch_cartelera_madrid()

    if with_details:
        log.info("Obteniendo detalles de %d películas...", len(movies))
        for movie in movies:
            detail = fetch_movie_detail(movie.get("url", ""))
            movie["cinemas"]   = detail["cinemas"]   or movie.get("cinemas", [])
            movie["showtimes"] = detail["showtimes"]

    return movies


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level="INFO", format="%(levelname)s: %(message)s")

    as_json = "--json" in sys.argv
    detail  = "--detail" in sys.argv

    cartelera = get_cartelera_madrid(with_details=detail)

    if as_json:
        print(json.dumps(cartelera, ensure_ascii=False, indent=2))
    else:
        print(f"\nCartelera de Madrid — {len(cartelera)} películas\n{'='*45}")
        for m in cartelera:
            print(f"\n  {m['title']}")
            print(f"  Género : {m['genre']}")
            if m.get("cinemas"):
                print(f"  Cines  : {', '.join(m['cinemas'][:3])}")
        print()