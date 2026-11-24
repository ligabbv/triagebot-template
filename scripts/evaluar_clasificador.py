"""Mide cuánto acierta el clasificador contra el histórico etiquetado.

Datos: data/tickets_etiquetados.json (los 140 tickets de seed_tickets.json, etiquetados a mano).

NO es un test: hace llamadas REALES al LLM (gasta tokens) y necesita OPENROUTER_API_KEY en .env.
Se excluyen los tickets usados como ejemplos en el prompt (ejemplo_prompt=true): si no, el
modelo "se sabría la respuesta" y el acierto saldría inflado.

Uso, desde la raíz del repo y con el venv activado:
    python scripts/evaluar_clasificador.py            # todos (~127 llamadas)
    python scripts/evaluar_clasificador.py --max 20   # solo los 20 primeros, para probar
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402

from app import classifier  # noqa: E402

DATOS = RAIZ / "data" / "tickets_etiquetados.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max", type=int, default=None, help="evaluar solo los N primeros")
    args = parser.parse_args()

    load_dotenv(RAIZ / ".env")
    if not os.getenv(classifier.API_KEY_ENV_VAR, "").strip():
        print(f"Falta {classifier.API_KEY_ENV_VAR}: sin ella todo saldría por fallback.")
        return 1

    tickets = [t for t in json.loads(DATOS.read_text(encoding="utf-8")) if not t["ejemplo_prompt"]]
    tickets = tickets[: args.max] if args.max else tickets

    aciertos_cat = aciertos_pri = aciertos_ambos = 0
    confusion: Counter[tuple[str, str]] = Counter()
    fallos = []
    for n, ticket in enumerate(tickets, 1):
        pred = classifier.classify_ticket(ticket["title"], ticket["description"])
        ok_cat = pred["category"] == ticket["category"]
        ok_pri = pred["priority"] == ticket["priority"]
        aciertos_cat += ok_cat
        aciertos_pri += ok_pri
        aciertos_ambos += ok_cat and ok_pri
        confusion[(ticket["category"], pred["category"])] += 1
        if not (ok_cat and ok_pri):
            fallos.append((ticket, pred))
        print(f"\r{n}/{len(tickets)}", end="", flush=True)

    total = len(tickets)
    print(f"\n\nTickets evaluados: {total}")
    print(f"Categoría correcta:  {aciertos_cat / total:6.1%}")
    print(f"Prioridad correcta:  {aciertos_pri / total:6.1%}")
    print(f"Las dos correctas:   {aciertos_ambos / total:6.1%}")

    print("\nConfusión de categorías (esperada -> predicha: nº):")
    for (esperada, predicha), cuenta in sorted(confusion.items()):
        marca = "" if esperada == predicha else "   <-- fallo"
        print(f"  {esperada:>15} -> {predicha:<15} {cuenta:3}{marca}")

    if fallos:
        print("\nFallos:")
        for ticket, pred in fallos:
            print(
                f"  #{ticket['id']:<3} {ticket['title'][:60]:<60} "
                f"esperado {ticket['category']}/{ticket['priority']} · "
                f"obtenido {pred['category']}/{pred['priority']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
