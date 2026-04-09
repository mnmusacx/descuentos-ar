"""
scraper_websearch.py — Scraper de descuentos AR usando Claude + web search
No usa requests ni BeautifulSoup. Claude busca en la web directamente
y devuelve las promos estructuradas en JSON.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
import anthropic

# ─── Búsquedas a realizar ────────────────────────────────────────────────────
# Cada query es una búsqueda que Claude va a hacer en la web.
# Más queries = más promos encontradas, pero más tokens gastados.

MES_ANIO = datetime.now().strftime("%B %Y")  # ej: "abril 2026"

QUERIES = [
    f"descuentos supermercados bancos Argentina {MES_ANIO} Galicia Santander BBVA Macro Nación Patagonia MODO",
    f"descuentos combustible farmacia gastronomía bancos Argentina {MES_ANIO} reintegro cuotas sin interés",
    f"Cuenta DNI Naranja X Personal Pay Mercado Pago descuentos {MES_ANIO} reintegro supermercados",
    f"cuotas sin interés electrónica indumentaria viajes bancos Argentina {MES_ANIO} Comafi Hipotecario",
]

# ─── Prompt para extracción estructurada ──────────────────────────────────────

SYSTEM_PROMPT = """Sos un extractor experto de datos de promociones bancarias y comerciales para Argentina.
Tu tarea es buscar en la web las promos vigentes del mes actual y retornar ÚNICAMENTE un array JSON válido.
Nada más — sin texto adicional, sin explicaciones, sin markdown.

Para cada búsqueda:
1. Buscá en la web usando el tool disponible
2. Leé los resultados y extraé todas las promociones concretas que encuentres
3. Descartá promos vencidas, sin descuento concreto, o que no sean de Argentina

Cada objeto del array JSON debe tener EXACTAMENTE estos campos:
{
  "entidad": "nombre del banco, fintech o comercio",
  "tipo_entidad": "banco|fintech|app_pago|supermercado|combustible|farmacia|gastronomia|otro",
  "categoria": "supermercado|gastronomia|combustible|farmacia|viajes|entretenimiento|indumentaria|electronica|servicios|otro",
  "descripcion": "descripción clara y concisa de la promo (máx 80 chars)",
  "descuento_pct": número entero o null,
  "reintegro_pct": número entero o null,
  "cuotas_sin_interes": número entero o null,
  "tope_ars": número entero o null,
  "medio_pago": ["lista de tarjetas o apps requeridas"],
  "dias_semana": ["lunes","martes",...] o ["todos"],
  "vigencia_hasta": "YYYY-MM-DD" o null,
  "como_obtener": "instrucción práctica de cómo usar el beneficio",
  "url_origen": "URL de la fuente donde encontraste la promo",
  "score_conveniencia": número del 1 al 10
}

Reglas:
- Solo incluí promos con descuento/reintegro/CSI concreto y verificable
- Si una promo aplica a múltiples días, listá todos en dias_semana
- score_conveniencia: 10 = máximo ahorro (alto % + tope alto + días frecuentes)
- Si no encontrás promos para una búsqueda, retorná []
- Nunca inventes datos que no estén en los resultados de búsqueda"""


def buscar_promos_con_claude(query: str, client: anthropic.Anthropic) -> list[dict]:
    """
    Llama a Claude API con web_search tool activado.
    Claude busca en la web y retorna promos estructuradas.
    """
    print(f"  Buscando: {query[:60]}...")

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": 3,  # Máximo 3 búsquedas por query para controlar costos
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Buscá en la web y extraé todas las promociones vigentes para: {query}\n\nRetorná solo el array JSON con las promos encontradas."
                }
            ],
        )

        # Extraer el texto de la respuesta
        texto = ""
        for block in response.content:
            if block.type == "text":
                texto += block.text

        if not texto.strip():
            return []

        # Limpiar markdown si viene con backticks
        texto = re.sub(r"^```json\s*", "", texto.strip())
        texto = re.sub(r"\s*```$", "", texto)

        # Parsear JSON
        promos = json.loads(texto)
        if not isinstance(promos, list):
            return []

        print(f"  → {len(promos)} promos encontradas")
        return promos

    except json.JSONDecodeError as e:
        print(f"  [!] Error parseando JSON para '{query[:40]}': {e}")
        return []
    except anthropic.APIError as e:
        print(f"  [!] Error API para '{query[:40]}': {e}")
        return []


def deduplicar(promos: list[dict]) -> list[dict]:
    """
    Elimina duplicados por entidad + descuento + categoria.
    Si hay duplicados, conserva el de mayor score.
    """
    vistos = {}
    for p in sorted(promos, key=lambda x: x.get("score_conveniencia", 0), reverse=True):
        clave = (
            (p.get("entidad") or "").lower().strip(),
            p.get("descuento_pct"),
            p.get("reintegro_pct"),
            p.get("cuotas_sin_interes"),
            p.get("categoria"),
        )
        if clave not in vistos:
            vistos[clave] = p
    return list(vistos.values())


def cargar_semilla() -> list[dict]:
    """
    Carga el JSON semilla existente para preservar promos
    que el scraper no encuentre en esta corrida.
    """
    semilla_path = Path(__file__).parent.parent / "data" / "descuentos.json"
    if not semilla_path.exists():
        return []
    try:
        data = json.loads(semilla_path.read_text())
        promos = data.get("promos", [])
        print(f"  Semilla cargada: {len(promos)} promos existentes")
        return promos
    except Exception:
        return []


def main():
    print(f"\n{'='*60}")
    print(f"  SCRAPER WEB SEARCH — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Período: {MES_ANIO}")
    print(f"{'='*60}\n")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    # 1. Cargar semilla
    print("[SEMILLA]")
    promos_semilla = cargar_semilla()

    # 2. Buscar promos nuevas con Claude + web search
    print("\n[WEB SEARCH]")
    promos_nuevas = []

    for i, query in enumerate(QUERIES, 1):
        print(f"\n  Query {i}/{len(QUERIES)}:")
        promos = buscar_promos_con_claude(query, client)
        for p in promos:
            p["fuente_tipo"] = "web_search"
        promos_nuevas.extend(promos)
        time.sleep(1)  # Pausa entre requests

    print(f"\n  Total nuevas encontradas: {len(promos_nuevas)}")

    # 3. Combinar semilla + nuevas y deduplicar
    # Las nuevas tienen prioridad sobre la semilla
    todas = promos_nuevas + promos_semilla
    todas = deduplicar(todas)
    todas.sort(key=lambda x: x.get("score_conveniencia", 0), reverse=True)

    # 4. Estadísticas
    cats = {}
    for p in todas:
        c = p.get("categoria", "otro")
        cats[c] = cats.get(c, 0) + 1

    # 5. Guardar JSON
    output = {
        "generado_en": datetime.now().isoformat(),
        "periodo": MES_ANIO,
        "total_promos": len(todas),
        "por_categoria": cats,
        "fuentes": {
            "web_search_nuevas": len(promos_nuevas),
            "semilla_preservada": len(promos_semilla),
        },
        "promos": todas,
    }

    data_path = Path(__file__).parent.parent / "data" / "descuentos.json"
    data_path.parent.mkdir(exist_ok=True)
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print(f"\n{'='*60}")
    print(f"  RESULTADO FINAL")
    print(f"  Nuevas por web search: {len(promos_nuevas)}")
    print(f"  De semilla:            {len(promos_semilla)}")
    print(f"  Total deduplicado:     {len(todas)}")
    print(f"  Por categoría:         {cats}")
    print(f"  Guardado en:           {data_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
