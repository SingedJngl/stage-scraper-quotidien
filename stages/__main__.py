"""Point d'entree du pipeline : python -m stages [--dry-run] [--since-days N].

Enchainement : config -> collecte -> normalisation -> filtrage -> dedoublonnage
-> ecriture dans la Google Sheet.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

from .config import ConfigError, charger_config, charger_secrets
from .filters import filtrer
from .models import Offer
from .sinks.google_sheet import GoogleSheet, SheetError
from .sources.adzuna import Adzuna
from .sources.base import Source, SourceError
from .sources.france_travail import FranceTravail

log = logging.getLogger("stages")


def configurer_logs(verbeux: bool) -> None:
    # Sur une console Windows, la sortie par defaut n'est pas en UTF-8 et les
    # accents des intitules d'offres ressortent en caracteres illisibles.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbeux else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    # gspread et urllib3 sont tres bavards en DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("google").setLevel(logging.WARNING)


def construire_sources(cfg: dict, secrets) -> list[Source]:
    """Instancie les sources activees et correctement configurees."""
    mots_cles = cfg["recherche"]["mots_cles"]
    sources: list[Source] = []

    def requetes(source_cfg: dict) -> list[str]:
        """Mots-cles de la source, ou la liste commune si elle n'en definit pas.

        Les deux APIs n'interpretent pas une suite de mots de la meme facon :
        une liste unique est forcement mauvaise pour l'une des deux.
        """
        return source_cfg.get("mots_cles") or mots_cles

    ft_cfg = cfg.get("france_travail", {})
    if ft_cfg.get("actif", True):
        if secrets.france_travail_pret:
            sources.append(
                FranceTravail(
                    secrets.ft_client_id,
                    secrets.ft_client_secret,
                    dict(ft_cfg, mots_cles=requetes(ft_cfg)),
                )
            )
        else:
            log.warning("France Travail ignore : FT_CLIENT_ID / FT_CLIENT_SECRET absents.")

    adzuna_cfg = cfg.get("adzuna", {})
    if adzuna_cfg.get("actif", True):
        if secrets.adzuna_pret:
            sources.append(
                Adzuna(
                    secrets.adzuna_app_id,
                    secrets.adzuna_app_key,
                    dict(adzuna_cfg, mots_cles=requetes(adzuna_cfg)),
                )
            )
        else:
            log.warning("Adzuna ignore : ADZUNA_APP_ID / ADZUNA_APP_KEY absents.")

    return sources


def afficher(offres: list[Offer]) -> None:
    """Rendu console utilise par --dry-run."""
    if not offres:
        print("\nAucune offre retenue.")
        return
    print(f"\n{len(offres)} offre(s) retenue(s) :\n")
    for offre in offres:
        duree = f"{offre.duree_semaines} sem." if offre.duree_semaines is not None else "duree ?"
        print(f"  [{offre.score:>2}] {offre.intitule}")
        print(f"       {offre.entreprise or 'entreprise non precisee'} - {offre.lieu}")
        print(f"       {offre.source} | {offre.type_contrat} | {duree} | {offre.date_publication}")
        print(f"       {', '.join(offre.mots_cles) or 'aucun mot-cle'}")
        print(f"       {offre.url}\n")


def executer(args: argparse.Namespace) -> int:
    cfg = charger_config(args.config)
    secrets = charger_secrets()
    depuis_jours = args.since_days or cfg["recherche"].get("publiee_depuis_jours", 1)

    sources = construire_sources(cfg, secrets)
    if not sources:
        log.error("Aucune source utilisable : verifie les secrets et config.yaml.")
        return 1

    # La feuille sert de memoire : on la lit AVANT de collecter, et un echec de
    # lecture arrete le run (mieux vaut zero ligne qu'un doublon).
    feuille: GoogleSheet | None = None
    ids_vus: set[str] = set()
    cles_vues: set[str] = set()
    if not args.dry_run:
        if not secrets.sheet_pret:
            log.error("SHEET_ID ou credentials Google absents : impossible d'ecrire.")
            return 1
        feuille = GoogleSheet(secrets.google_credentials, secrets.sheet_id, cfg.get("sortie", {}))
        ids_vus, cles_vues = feuille.deja_vues()

    log.info("Fenetre analysee : %d jour(s)", depuis_jours)

    retenues: list[Offer] = []
    resume: list[dict] = []
    echecs = 0

    for source in sources:
        try:
            brutes = source.collecter(depuis_jours)
        except SourceError as exc:
            # Une source en panne ne doit pas empecher l'autre de livrer.
            echecs += 1
            log.error("Source %s en echec : %s", source.nom, exc)
            resume.append(
                {
                    "libelle": source.libelle,
                    "recuperees": 0,
                    "retenues": 0,
                    "erreur": str(exc)[:200],
                }
            )
            continue

        gardees, stats = filtrer(brutes, cfg.get("filtrage", {}))
        log.info(
            "%s : %d recuperees -> %d retenues (exclusions %d, pas un stage %d, "
            "trop courtes %d, score insuffisant %d, duree inconnue gardee %d)",
            source.nom,
            len(brutes),
            len(gardees),
            stats["rejet_exclusion"],
            stats["rejet_pas_un_stage"],
            stats["rejet_trop_court"],
            stats["rejet_score"],
            stats["duree_inconnue_gardee"],
        )
        retenues.extend(gardees)
        resume.append(
            {
                "libelle": source.libelle,
                "recuperees": len(brutes),
                "retenues": len(gardees),
                "erreur": "",
            }
        )

    if echecs == len(sources):
        log.error("Toutes les sources ont echoue.")
        return 1

    # Dedoublonnage : contre la feuille (offres des runs precedents) et entre
    # sources (meme annonce rediffusee par France Travail et Adzuna).
    nouvelles: list[Offer] = []
    doublons = 0
    for offre in sorted(retenues, key=lambda o: o.score, reverse=True):
        if offre.id in ids_vus or offre.cle_floue in cles_vues:
            doublons += 1
            continue
        ids_vus.add(offre.id)
        cles_vues.add(offre.cle_floue)
        nouvelles.append(offre)

    log.info("%d nouvelle(s) offre(s), %d doublon(s) ecarte(s)", len(nouvelles), doublons)

    if args.dry_run:
        afficher(nouvelles)
        log.info("Mode --dry-run : rien n'a ete ecrit dans la feuille.")
        return 0

    assert feuille is not None
    ajoutees = feuille.ajouter(nouvelles)
    log.info("%d ligne(s) ajoutee(s) dans la feuille", ajoutees)

    # Les offres portent le libelle de leur source : le comptage par source est
    # un simple regroupement.
    ajoutees_par_source: Counter = Counter(offre.source for offre in nouvelles)
    for ligne in resume:
        feuille.journaliser(
            ligne["libelle"],
            ligne["recuperees"],
            ligne["retenues"],
            ajoutees_par_source.get(ligne["libelle"], 0),
            ligne["erreur"],
        )

    return 0


def main(argv: list[str] | None = None) -> int:
    parseur = argparse.ArgumentParser(
        prog="stages", description="Collecte quotidienne d'offres de stage."
    )
    parseur.add_argument(
        "--dry-run",
        action="store_true",
        help="affiche les offres retenues sans rien ecrire dans la feuille",
    )
    parseur.add_argument(
        "--since-days",
        type=int,
        default=None,
        help="fenetre de publication en jours (defaut : config.yaml)",
    )
    parseur.add_argument("--config", default=None, help="chemin d'un autre config.yaml")
    parseur.add_argument("-v", "--verbose", action="store_true", help="logs detailles")
    args = parseur.parse_args(argv)

    configurer_logs(args.verbose)
    try:
        return executer(args)
    except (ConfigError, SheetError) as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        log.warning("Interrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
