"""
main.py — Orquestador principal
Corre ambos scrapers (bancos/supermercados + Telegram/RSS),
combina los resultados, deduplica y genera el JSON final.
"""

import json
import os
from datetime import datetime
from pathlib import Path

# Importar los dos scrapers
from scraper import correr_scraper
from scraper_social import correr_scraper_social, deduplicar


def main():
    print("=" * 60)
    print(f"  DESCUENTOS AR — Corrida completa {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 60)

    # 1. Scraper de fuentes oficiales (bancos, supermercados)
    resultado_oficial = correr_scraper()
    promos_oficiales = resultado_oficial.get("promos", [])

    # 2. Scraper de fuentes sociales (Telegram, RSS)
    promos_sociales = correr_scraper_social()

    # 3. Combinar y deduplicar
    todas = promos_oficiales + promos_sociales
    todas = deduplicar(todas)
    todas.sort(key=lambda x: x.get("score_conveniencia", 0), reverse=True)

    # 4. Estadísticas por categoría
    cats = {}
    for p in todas:
        c = p.get("categoria", "otro")
        cats[c] = cats.get(c, 0) + 1

    # 5. Guardar JSON final
    output = {
        "generado_en": datetime.now().isoformat(),
        "total_promos": len(todas),
        "por_categoria": cats,
        "fuentes": {
            "oficiales": len(promos_oficiales),
            "sociales": len(promos_sociales),
        },
        "promos": todas,
    }

    data_path = Path(__file__).parent.parent / "data" / "descuentos.json"
    data_path.parent.mkdir(exist_ok=True)
    data_path.write_text(json.dumps(output, ensure_ascii=False, indent=2))

    print("\n" + "=" * 60)
    print(f"  TOTAL: {len(todas)} promos únicas")
    print(f"  Oficiales: {len(promos_oficiales)}  |  Sociales: {len(promos_sociales)}")
    print(f"  Por categoría: {cats}")
    print(f"  Guardado en: {data_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

