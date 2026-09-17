"""Affiche les referentiels France Travail pour renseigner config.yaml.

A lancer une fois, apres avoir mis FT_CLIENT_ID / FT_CLIENT_SECRET dans le .env :

    python scripts/discover_referentiels.py
    python scripts/discover_referentiels.py metiers electronique

Le premier appel liste les types et natures de contrat : on y lit le code exact
correspondant au stage, qu'on reporte dans `france_travail.type_contrat` ou
`france_travail.nature_contrat`. Aucun code n'est devine en dur dans le pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stages.config import charger_config, charger_secrets  # noqa: E402
from stages.models import normaliser  # noqa: E402
from stages.sources.france_travail import FranceTravail  # noqa: E402

REFERENTIELS = ["typesContrats", "naturesContrats"]


def afficher(titre: str, entrees: list[dict], filtre: str = "") -> None:
    print(f"\n=== {titre} ({len(entrees)} entrees) ===")
    filtre_normalise = normaliser(filtre)
    for entree in entrees:
        code = entree.get("code", "")
        libelle = entree.get("libelle", "")
        if filtre_normalise and filtre_normalise not in normaliser(f"{code} {libelle}"):
            continue
        print(f"  {code:<10} {libelle}")


def main() -> int:
    cible = sys.argv[1] if len(sys.argv) > 1 else ""
    filtre = sys.argv[2] if len(sys.argv) > 2 else ""

    cfg = charger_config()
    secrets = charger_secrets()
    if not secrets.france_travail_pret:
        print("FT_CLIENT_ID / FT_CLIENT_SECRET absents (voir .env.example).")
        return 1

    client = FranceTravail(
        secrets.ft_client_id,
        secrets.ft_client_secret,
        dict(cfg.get("france_travail", {}), mots_cles=[]),
    )

    for nom in [cible] if cible else REFERENTIELS:
        try:
            afficher(nom, client.referentiel(nom), filtre)
        except Exception as exc:  # referentiel inexistant, droits manquants...
            print(f"\n!! {nom} : {exc}")

    print(
        "\nRepere la ligne correspondant au stage et reporte son code dans "
        "config.yaml (france_travail.type_contrat ou nature_contrat).\n"
        "Astuce : `python scripts/discover_referentiels.py metiers electronique` "
        "liste les codes ROME contenant 'electronique'."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
