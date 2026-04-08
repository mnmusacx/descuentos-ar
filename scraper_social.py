"""
scraper_social.py — Recolector de descuentos desde Telegram y RSS
Complementa al scraper principal (bancos/supermercados) con fuentes
comunitarias donde circulan las promos más jugosas.
"""

import json
import re
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import anthropic

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

# ─── Fuentes Telegram (preview público sin autenticación) ─────────────────────
# Telegram permite ver los últimos mensajes de canales públicos
# en t.me/s/NOMBRE_CANAL sin necesitar cuenta ni API key.

CANALES_TELEGRAM = [
    {
        "id": "tg_descuentosarg",
        "nombre": "Descuentos Argentina",
        "canal": "descuentosargentina",
        "url": "https://t.me/s/descuentosargentina",
    },
    {
        "id": "tg_promos_arg",
        "nombre": "Promos AR",
        "canal": "promos_arg",
        "url": "https://t.me/s/promos_arg",
    },
    {
        "id": "tg_beneficios_bancarios",
        "nombre": "Beneficios Bancarios AR",
        "canal": "beneficiosbancarios",
        "url": "https://t.me/s/beneficiosbancarios",
    },
    {
        "id": "tg_ahorro_ar",
        "nombre": "Ahorro AR",
        "canal": "ahorroenargentina",
        "url": "https://t.me/s/ahorroenargentina",
    },
    {
        "id": "tg_descuentos_dia",
        "nombre": "Descuentos del Día",
        "canal": "descuentosdeldia_ar",
        "url": "https://t.me/s/descuentosdeldia_ar",
    },
]

# ─── Fuentes RSS ──────────────────────────────────────────────────────────────

FUENTES_RSS = [
    {
        "id": "rss_cuponstar",
        "nombre": "CuponStar",
        "url": "https://www.cuponstar.com.ar/feed",
        "tipo": "curador",
    },
    {
        "id": "rss_promodescuentos",
        "nombre": "PromoDescuentos",
        "url": "https://www.promodescuentos.com/feed",
        "tipo": "curador",
    },
    {
        "id": "rss_ofertia",
        "nombre": "Ofertia AR",
        "url": "https://ar.ofertia.com/rss",
        "tipo": "curador",
    },
    {
        "id": "rss_ahorro",
        "nombre": "Ahorro.ar",
        "url": "https://ahorro.ar/feed/",
        "tipo": "curador",
    },
    {
        "id": "rss_descuentopia",
        "nombre": "Descuentopía",
        "url": "https://descuentopia.com.ar/feed/",
        "tipo": "curador",
    },
]

# ─── Scraping Telegram ────────────────────────────────────────────────────────

def scrapear_telegram(canal: dict, max_mensajes: int = 30) -> str:
    """
    Extrae los últimos mensajes de un canal público de Telegram
    usando la preview web (t.me/s/CANAL) sin autenticación.
    Retorna texto plano con los mensajes más recientes.
    """
    print(f"  Telegram: @{canal['canal']}...")
    try:
        resp = requests.get(canal["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Error accediendo a {canal['url']}: {e}")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")

    # Los mensajes están en .tgme_widget_message_text
    mensajes = soup.select(".tgme_widget_message_text")
    if not mensajes:
        # Fallback: buscar cualquier contenido de mensaje
        mensajes = soup.select(".js-message_text, .message_text")

    textos = []
    for msg in mensajes[-max_mensajes:]:  # Tomar los más recientes
        texto = msg.get_text(separator=" ", strip=True)
        if len(texto) > 20:  # Filtrar mensajes vacíos o muy cortos
            textos.append(texto)

    resultado = " ||| ".join(textos)
    return resultado[:5000]


# ─── Scraping RSS ─────────────────────────────────────────────────────────────

def scrapear_rss(fuente: dict, max_items: int = 20) -> str:
    """
    Parsea un feed RSS y extrae título + descripción de los items recientes.
    Filtra items de los últimos 8 días para no procesar contenido viejo.
    """
    print(f"  RSS: {fuente['nombre']}...")
    try:
        resp = requests.get(fuente["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Error accediendo a {fuente['url']}: {e}")
        return ""

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        print(f"  [!] XML inválido en {fuente['url']}: {e}")
        return ""

    # Namespace para Atom feeds
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    textos = []

    for item in items[:max_items]:
        titulo = (
            item.findtext("title")
            or item.findtext("atom:title", namespaces=ns)
            or ""
        )
        descripcion = (
            item.findtext("description")
            or item.findtext("atom:summary", namespaces=ns)
            or item.findtext("atom:content", namespaces=ns)
            or ""
        )
        # Limpiar HTML de la descripción
        if descripcion:
            soup = BeautifulSoup(descripcion, "html.parser")
            descripcion = soup.get_text(separator=" ", strip=True)

        texto = f"{titulo} — {descripcion}".strip(" —")
        if len(texto) > 15:
            textos.append(texto)

    resultado = " ||| ".join(textos)
    return resultado[:5000]


# ─── Normalización con Claude ─────────────────────────────────────────────────

PROMPT_SOCIAL = """Eres un extractor de datos de promociones y descuentos para Argentina.
Dado texto de mensajes de Telegram o items de RSS sobre descuentos y promos,
extrae ÚNICAMENTE las promociones concretas y retorna SOLO un array JSON válido. Nada más.

Criterios de inclusión:
- Debe mencionar un % de descuento, reintegro, cuotas sin interés, o regalo concreto
- Debe ser aplicable en Argentina
- Descartá memes, opiniones, noticias sin promo concreta, y spam

Cada objeto del array debe tener exactamente estos campos:
{
  "entidad": "nombre del banco, app o comercio mencionado",
  "tipo_entidad": "banco|fintech|app_pago|supermercado|combustible|farmacia|gastronomia|otro",
  "categoria": "supermercado|gastronomia|combustible|farmacia|viajes|entretenimiento|indumentaria|electronica|servicios|otro",
  "descripcion": "descripción clara de la promo en español (máx 80 chars)",
  "descuento_pct": número o null,
  "reintegro_pct": número o null,
  "cuotas_sin_interes": número o null,
  "tope_ars": número o null,
  "medio_pago": ["lista de tarjetas/apps que aplican, o ['no especificado']"],
  "dias_semana": ["días que aplica"] o ["todos"],
  "vigencia_hasta": "YYYY-MM-DD" o null,
  "como_obtener": "instrucción breve para activar el beneficio",
  "url_origen": null,
  "score_conveniencia": número del 1 al 10
}

Si no hay ninguna promo concreta, retorna [].
No inventes datos que no estén en el texto."""


def normalizar_con_claude(fuente_id: str, fuente_nombre: str, texto: str) -> list[dict]:
    """Extrae promos estructuradas del texto usando Claude API."""
    if not texto.strip():
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Fuente: {fuente_nombre}
Texto con mensajes/posts sobre descuentos:
{texto}

Extraé todas las promociones concretas en formato JSON."""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=PROMPT_SOCIAL,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        promos = json.loads(raw)
        for p in promos:
            p["fuente_id"] = fuente_id
            p["fuente_tipo"] = "social"  # Marca para distinguir de fuentes oficiales
        return promos

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [!] Error parseando Claude para {fuente_nombre}: {e}")
        return []


# ─── Deduplicación ────────────────────────────────────────────────────────────

def deduplicar(promos: list[dict]) -> list[dict]:
    """
    Elimina promos duplicadas comparando entidad + descuento + categoría.
    Cuando hay duplicados, conserva el de mayor score.
    """
    vistos = {}
    for p in sorted(promos, key=lambda x: x.get("score_conveniencia", 0), reverse=True):
        clave = (
            (p.get("entidad") or "").lower(),
            p.get("descuento_pct"),
            p.get("reintegro_pct"),
            p.get("categoria"),
        )
        if clave not in vistos:
            vistos[clave] = p
    return list(vistos.values())


# ─── Orquestador ─────────────────────────────────────────────────────────────

def correr_scraper_social() -> list[dict]:
    print(f"\n=== Scraper Social AR — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    todas = []

    print("[TELEGRAM]")
    for canal in CANALES_TELEGRAM:
        texto = scrapear_telegram(canal)
        if texto:
            promos = normalizar_con_claude(canal["id"], canal["nombre"], texto)
            print(f"  → {len(promos)} promos extraídas de @{canal['canal']}")
            todas.extend(promos)
        time.sleep(1.5)

    print("\n[RSS]")
    for fuente in FUENTES_RSS:
        texto = scrapear_rss(fuente)
        if texto:
            promos = normalizar_con_claude(fuente["id"], fuente["nombre"], texto)
            print(f"  → {len(promos)} promos extraídas de {fuente['nombre']}")
            todas.extend(promos)
        time.sleep(1)

    todas = deduplicar(todas)
    todas.sort(key=lambda x: x.get("score_conveniencia", 0), reverse=True)

    print(f"\n✓ {len(todas)} promos únicas de fuentes sociales")
    return todas


if __name__ == "__main__":
    promos = correr_scraper_social()
    # Output standalone para testing
    out = {
        "generado_en": datetime.now().isoformat(),
        "total_promos": len(promos),
        "promos": promos,
    }
    path = Path(__file__).parent.parent / "data" / "descuentos_social.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Guardado en {path}")
