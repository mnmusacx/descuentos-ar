"""
scraper_social.py — Recolector de descuentos desde Telegram, RSS y webs de tarjetas
Incluye scraping directo de Visa AR, Mastercard AR, y sitios curadores
que consolidan promos bancarias en HTML estático (sin SPA).
"""

import json
import re
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CANALES_TELEGRAM = [
    {"id": "tg_descuentosarg",      "nombre": "Descuentos Argentina",    "url": "https://t.me/s/descuentosargentina"},
    {"id": "tg_promos_arg",         "nombre": "Promos AR",               "url": "https://t.me/s/promos_arg"},
    {"id": "tg_beneficios_banc",    "nombre": "Beneficios Bancarios AR", "url": "https://t.me/s/beneficiosbancarios"},
    {"id": "tg_ahorro_ar",          "nombre": "Ahorro AR",               "url": "https://t.me/s/ahorroenargentina"},
    {"id": "tg_descuentos_dia",     "nombre": "Descuentos del Día",      "url": "https://t.me/s/descuentosdeldia_ar"},
    {"id": "tg_promos_bancarias",   "nombre": "Promos Bancarias",        "url": "https://t.me/s/promosbancarias"},
    {"id": "tg_cuponstar",          "nombre": "CuponStar Canal",         "url": "https://t.me/s/cuponstar"},
    {"id": "tg_ofertas_arg",        "nombre": "Ofertas Argentina",       "url": "https://t.me/s/ofertasargentina"},
    {"id": "tg_descuentos_galicia", "nombre": "Descuentos Galicia",      "url": "https://t.me/s/descuentosgalicia"},
    {"id": "tg_promo_bancos",       "nombre": "Promo Bancos AR",         "url": "https://t.me/s/promobancosar"},
    {"id": "tg_beneficios_tar",     "nombre": "Beneficios Tarjetas",     "url": "https://t.me/s/beneficiostarjetas"},
    {"id": "tg_descuentos_hoy",     "nombre": "Descuentos Hoy AR",       "url": "https://t.me/s/descuentoshoyar"},
]

FUENTES_RSS = [
    {"id": "rss_cuponstar",    "nombre": "CuponStar",       "url": "https://www.cuponstar.com.ar/feed"},
    {"id": "rss_promodesc",    "nombre": "PromoDescuentos", "url": "https://www.promodescuentos.com/feed"},
    {"id": "rss_ofertia",      "nombre": "Ofertia AR",      "url": "https://ar.ofertia.com/rss"},
    {"id": "rss_ahorro",       "nombre": "Ahorro.ar",       "url": "https://ahorro.ar/feed/"},
    {"id": "rss_descuentopia", "nombre": "Descuentopía",    "url": "https://descuentopia.com.ar/feed/"},
    {"id": "rss_descuentosya", "nombre": "DescuentosYa",    "url": "https://www.descuentosya.com.ar/feed/"},
]

WEBS_TARJETAS = [
    {
        "id": "web_visa_ar",
        "nombre": "Visa Argentina — Beneficios",
        "url": "https://www.visa.com.ar/es_ar/beneficios.html",
        "selectores": [".benefit-card", ".offer-card", ".promo-item", "article", ".card"],
    },
    {
        "id": "web_mastercard_ar",
        "nombre": "Mastercard Argentina — Ofertas",
        "url": "https://www.mastercard.com.ar/es-ar/consumidores/ofertas.html",
        "selectores": [".offer-card", ".promo-card", ".benefit", "article.card"],
    },
    {
        "id": "web_cuponstar",
        "nombre": "CuponStar — Descuentos bancarios",
        "url": "https://www.cuponstar.com.ar/descuentos-bancarios",
        "selectores": [".coupon-card", ".discount-item", ".promo-card", "article"],
    },
    {
        "id": "web_descuentosya",
        "nombre": "DescuentosYa — Bancos",
        "url": "https://www.descuentosya.com.ar/bancos/",
        "selectores": [".promo-card", ".discount-card", ".offer-item", "article"],
    },
    {
        "id": "web_ahorro_bancos",
        "nombre": "Ahorro.ar — Promos bancarias",
        "url": "https://ahorro.ar/categoria/bancos/",
        "selectores": [".post-card", ".promo-item", "article", ".entry"],
    },
    {
        "id": "web_bna",
        "nombre": "Banco Nación — Beneficios",
        "url": "https://www.bna.com.ar/Personas/Beneficios",
        "selectores": [".beneficio", ".promo", ".card", "article", "li"],
    },
    {
        "id": "web_naranjax",
        "nombre": "Naranja X — Beneficios",
        "url": "https://www.naranjax.com/beneficios",
        "selectores": [".benefit-card", ".promo", ".discount", "article"],
    },
    {
        "id": "web_modo",
        "nombre": "Modo — Beneficios",
        "url": "https://www.modo.com.ar/beneficios",
        "selectores": [".promo-item", ".offer-card", ".benefit", "article"],
    },
]


def scrapear_telegram(canal: dict, max_mensajes: int = 40) -> str:
    print(f"  Telegram: {canal['url'].split('/')[-1]}...")
    try:
        resp = requests.get(canal["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Error: {e}")
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    mensajes = soup.select(".tgme_widget_message_text") or soup.select(".js-message_text")
    textos = [m.get_text(separator=" ", strip=True) for m in mensajes[-max_mensajes:] if len(m.get_text(strip=True)) > 20]
    return (" ||| ".join(textos))[:5000]


def scrapear_rss(fuente: dict, max_items: int = 25) -> str:
    print(f"  RSS: {fuente['nombre']}...")
    try:
        resp = requests.get(fuente["url"], headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Error: {e}")
        return ""
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return ""
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)
    textos = []
    for item in items[:max_items]:
        titulo = item.findtext("title") or item.findtext("atom:title", namespaces=ns) or ""
        desc = item.findtext("description") or item.findtext("atom:summary", namespaces=ns) or ""
        if desc:
            desc = BeautifulSoup(desc, "html.parser").get_text(separator=" ", strip=True)
        texto = f"{titulo} — {desc}".strip(" —")
        if len(texto) > 15:
            textos.append(texto)
    return (" ||| ".join(textos))[:5000]


def scrapear_web(fuente: dict) -> str:
    print(f"  Web: {fuente['nombre']}...")
    try:
        resp = requests.get(fuente["url"], headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [!] Error: {e}")
        return ""
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "meta"]):
        tag.decompose()
    for selector in fuente.get("selectores", []):
        items = soup.select(selector)
        if len(items) >= 3:
            texto = " ||| ".join(
                item.get_text(separator=" ", strip=True)
                for item in items
                if len(item.get_text(strip=True)) > 20
            )
            if len(texto) > 200:
                print(f"      → {len(items)} items con selector '{selector}'")
                return re.sub(r"\s+", " ", texto).strip()[:5000]
    texto = re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()
    return texto[:4000]


PROMPT_SOCIAL = """Eres un extractor de datos de promociones y descuentos para Argentina.
Dado texto de Telegram, RSS o webs de tarjetas/bancos, extrae ÚNICAMENTE las promociones
concretas y retorna SOLO un array JSON válido. Nada más — ningún texto adicional.

Criterios de inclusión:
- Debe mencionar un % de descuento, reintegro, cuotas sin interés, o beneficio concreto
- Debe ser aplicable en Argentina
- Descartá memes, opiniones, noticias sin promo, spam

Cada objeto del array debe tener exactamente estos campos:
{
  "entidad": "nombre del banco, app o comercio",
  "tipo_entidad": "banco|fintech|app_pago|supermercado|combustible|farmacia|gastronomia|otro",
  "categoria": "supermercado|gastronomia|combustible|farmacia|viajes|entretenimiento|indumentaria|electronica|servicios|otro",
  "descripcion": "descripción clara de la promo en español (máx 80 chars)",
  "descuento_pct": número o null,
  "reintegro_pct": número o null,
  "cuotas_sin_interes": número o null,
  "tope_ars": número o null,
  "medio_pago": ["tarjetas/apps que aplican"],
  "dias_semana": ["días que aplica"] o ["todos"],
  "vigencia_hasta": "YYYY-MM-DD" o null,
  "como_obtener": "instrucción breve para activar el beneficio",
  "url_origen": null,
  "score_conveniencia": número del 1 al 10
}

Si no hay ninguna promo concreta, retorna [].
No inventes datos que no estén en el texto."""


def normalizar_con_claude(fuente_id: str, fuente_nombre: str, texto: str) -> list[dict]:
    if not texto.strip():
        return []
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=PROMPT_SOCIAL,
            messages=[{"role": "user", "content": f"Fuente: {fuente_nombre}\n\n{texto}"}],
        )
        raw = msg.content[0].text.strip()
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        promos = json.loads(raw)
        for p in promos:
            p["fuente_id"] = fuente_id
            p["fuente_tipo"] = "social"
        return promos
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [!] Error Claude para {fuente_nombre}: {e}")
        return []


def deduplicar(promos: list[dict]) -> list[dict]:
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


def correr_scraper_social() -> list[dict]:
    print(f"\n=== Scraper Social AR — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")
    todas = []

    print("[WEBS TARJETAS / CURADORES]")
    for fuente in WEBS_TARJETAS:
        texto = scrapear_web(fuente)
        if texto:
            promos = normalizar_con_claude(fuente["id"], fuente["nombre"], texto)
            print(f"  → {len(promos)} promos de {fuente['nombre']}")
            todas.extend(promos)
        time.sleep(1.5)

    print("\n[TELEGRAM]")
    for canal in CANALES_TELEGRAM:
        texto = scrapear_telegram(canal)
        if texto:
            promos = normalizar_con_claude(canal["id"], canal["nombre"], texto)
            print(f"  → {len(promos)} promos")
            todas.extend(promos)
        time.sleep(1.5)

    print("\n[RSS]")
    for fuente in FUENTES_RSS:
        texto = scrapear_rss(fuente)
        if texto:
            promos = normalizar_con_claude(fuente["id"], fuente["nombre"], texto)
            print(f"  → {len(promos)} promos de {fuente['nombre']}")
            todas.extend(promos)
        time.sleep(1)

    todas = deduplicar(todas)
    todas.sort(key=lambda x: x.get("score_conveniencia", 0), reverse=True)
    print(f"\n✓ {len(todas)} promos únicas de fuentes sociales/web")
    return todas


if __name__ == "__main__":
    promos = correr_scraper_social()
    out = {"generado_en": datetime.now().isoformat(), "total_promos": len(promos), "promos": promos}
    path = Path(__file__).parent.parent / "data" / "descuentos_social.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"Guardado en {path}")
