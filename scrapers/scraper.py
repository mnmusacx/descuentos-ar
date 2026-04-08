"""
scraper.py — Recolector semanal de descuentos AR
Raspa HTML de sitios bancarios y supermercados, normaliza con Claude API,
y vuelca a data/descuentos.json para el dashboard.
"""

import json
import re
import time
import os
from datetime import datetime
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import anthropic

# ─── Configuración de fuentes ────────────────────────────────────────────────

FUENTES = [
    {
        "id": "galicia",
        "nombre": "Banco Galicia",
        "tipo": "banco",
        "url": "https://www.bancogalicia.com/personas/tarjetas/beneficios.html",
        "selector_items": ".benefit-card, .promo-item, article.promo",
        "notas": "Requiere JS — usamos requests con headers de browser",
    },
    {
        "id": "santander",
        "nombre": "Santander",
        "tipo": "banco",
        "url": "https://www.santander.com.ar/banco/online/personas/tarjetas/beneficios",
        "selector_items": ".card-benefit, .promo-block",
    },
    {
        "id": "bbva",
        "nombre": "BBVA Argentina",
        "tipo": "banco",
        "url": "https://www.bbva.com.ar/personas/productos/tarjetas/beneficios.html",
        "selector_items": ".benefit-item, .promo-card",
    },
    {
        "id": "nacion",
        "nombre": "Banco Nación",
        "tipo": "banco",
        "url": "https://www.bna.com.ar/Personas/Beneficios",
        "selector_items": ".beneficio-item, .promo",
    },
    {
        "id": "naranja",
        "nombre": "Naranja X",
        "tipo": "fintech",
        "url": "https://www.naranjax.com/beneficios",
        "selector_items": ".benefit-card, .discount-card",
    },
    {
        "id": "modo",
        "nombre": "Modo",
        "tipo": "app_pago",
        "url": "https://www.modo.com.ar/beneficios",
        "selector_items": ".promo-item, .offer-card",
    },
    {
        "id": "coto",
        "nombre": "Coto",
        "tipo": "supermercado",
        "url": "https://www.coto.com.ar/promociones",
        "selector_items": ".promo-card, .oferta-item, .discount",
    },
    {
        "id": "carrefour",
        "nombre": "Carrefour",
        "tipo": "supermercado",
        "url": "https://www.carrefour.com.ar/institucional/promotions",
        "selector_items": ".promo-item, .promotion-card",
    },
    {
        "id": "dia",
        "nombre": "Día",
        "tipo": "supermercado",
        "url": "https://diaonline.supermercadosdia.com.ar/institucional/promos",
        "selector_items": ".promo, .benefit",
    },
    {
        "id": "ypf",
        "nombre": "YPF Serviclub",
        "tipo": "combustible",
        "url": "https://www.ypf.com/serviclub/beneficios",
        "selector_items": ".benefit-item, .promo-card",
    },
    {
        "id": "shell",
        "nombre": "Shell Box",
        "tipo": "combustible",
        "url": "https://www.shell.com.ar/motoristas/shell-box/beneficios.html",
        "selector_items": ".promo, .benefit-card",
    },
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ─── Scraping ────────────────────────────────────────────────────────────────

def fetch_html(url: str, timeout: int = 15) -> str | None:
    """Descarga HTML de una URL con reintentos."""
    for intento in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            print(f"  [!] Intento {intento+1}/3 fallido para {url}: {e}")
            time.sleep(2 ** intento)
    return None


def extraer_texto_fuente(fuente: dict) -> str:
    """
    Descarga y extrae el texto visible de la página de beneficios.
    Retorna un string plano con el contenido relevante (máx ~3000 chars).
    """
    print(f"  Scrapando {fuente['nombre']} ({fuente['url']})...")
    html = fetch_html(fuente["url"])
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # Intentar selector específico de la fuente
    items = soup.select(fuente.get("selector_items", ""))
    if items:
        texto = " | ".join(item.get_text(separator=" ", strip=True) for item in items)
    else:
        # Fallback: extraer todo el texto visible (sin scripts/styles)
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        texto = soup.get_text(separator=" ", strip=True)

    # Limpiar espacios múltiples
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:4000]  # Limitar para no saturar el contexto de Claude


# ─── Normalización con Claude ─────────────────────────────────────────────────

PROMPT_SISTEMA = """Eres un extractor de datos de promociones y descuentos para Argentina.
Dado texto scrapeado de un sitio web bancario o comercial, extrae TODAS las promociones
vigentes y retorna ÚNICAMENTE un array JSON válido. Nada más — ningún texto adicional.

Cada objeto del array debe tener exactamente estos campos:
{
  "entidad": "nombre del banco, app o comercio",
  "tipo_entidad": "banco|fintech|app_pago|supermercado|combustible|farmacia|gastronomia|otro",
  "categoria": "supermercado|gastronomia|combustible|farmacia|viajes|entretenimiento|indumentaria|electronica|servicios|otro",
  "descripcion": "descripción breve de la promo en español claro (máx 80 chars)",
  "descuento_pct": número o null,
  "reintegro_pct": número o null,
  "cuotas_sin_interes": número o null,
  "tope_ars": número o null,
  "medio_pago": ["lista de tarjetas/apps que aplican"],
  "dias_semana": ["lunes","martes",...] o ["todos"],
  "vigencia_hasta": "YYYY-MM-DD" o null,
  "como_obtener": "instrucción breve de cómo activar o usar el beneficio",
  "url_origen": "URL del programa de beneficios",
  "score_conveniencia": número del 1 al 10 (10 = máximo ahorro, considera % + tope + frecuencia)
}

Si no encontrás promociones claras, retorna [].
No inventes datos que no estén en el texto."""


def normalizar_con_claude(fuente: dict, texto_crudo: str) -> list[dict]:
    """Llama a Claude API para extraer promos estructuradas del texto scrapeado."""
    if not texto_crudo.strip():
        return []

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    prompt = f"""Fuente: {fuente['nombre']} (tipo: {fuente['tipo']})
URL: {fuente['url']}

Texto scrapeado:
{texto_crudo}

Extraé todas las promociones vigentes en formato JSON."""

    try:
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=PROMPT_SISTEMA,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()

        # Limpiar posibles backticks de markdown
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        promos = json.loads(raw)
        # Agregar metadata de la corrida
        for p in promos:
            p["url_origen"] = p.get("url_origen") or fuente["url"]
            p["fuente_id"] = fuente["id"]
        return promos

    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"  [!] Error parseando respuesta de Claude para {fuente['nombre']}: {e}")
        return []


# ─── Orquestador principal ────────────────────────────────────────────────────

def correr_scraper():
    print(f"\n=== Scraper de descuentos AR — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===\n")

    todas_las_promos = []

    for fuente in FUENTES:
        print(f"[{fuente['id'].upper()}] {fuente['nombre']}")
        texto = extraer_texto_fuente(fuente)

        if texto:
            promos = normalizar_con_claude(fuente, texto)
            print(f"  → {len(promos)} promos extraídas")
            todas_las_promos.extend(promos)
        else:
            print(f"  → Sin datos (página inaccesible o bloqueada)")

        time.sleep(1)  # Pausa cortés entre requests

    # Ordenar por score de conveniencia descendente
    todas_las_promos.sort(key=lambda x: x.get("score_conveniencia", 0), reverse=True)

    # Guardar JSON
    output = {
        "generado_en": datetime.now().isoformat(),
        "total_promos": len(todas_las_promos),
        "promos": todas_las_promos,
    }

    data_path = Path(__file__).parent.parent / "data" / "descuentos.json"
    data_path.parent.mkdir(exist_ok=True)
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"\n✓ {len(todas_las_promos)} promos guardadas en {data_path}")
    return output


if __name__ == "__main__":
    correr_scraper()


