"""Source complementaire : API Adzuna (agregateur de job boards).

Documentation : https://developer.adzuna.com/overview
Cle developpeur gratuite mais quota journalier limite : on reste volontairement
sur quelques requetes par run (voir `max_pages` dans config.yaml).
"""

from __future__ import annotations

import logging
import time

import requests

from ..models import Offer
from .base import Source, SourceError

log = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs"
MAX_PAR_PAGE = 50
DELAI_ENTRE_APPELS = 0.5


class Adzuna(Source):
    nom = "adzuna"
    libelle = "Adzuna"

    def __init__(self, app_id: str, app_key: str, cfg: dict) -> None:
        self.app_id = app_id
        self.app_key = app_key
        self.cfg = cfg
        self.pays = cfg.get("pays", "fr")
        self.session = requests.Session()

    def get(self, page: int, params: dict) -> dict:
        url = f"{BASE_URL}/{self.pays}/search/{page}"
        for tentative in range(3):
            reponse = self.session.get(url, params=params, timeout=60)
            if reponse.status_code == 200:
                return reponse.json()
            if reponse.status_code in (429, 503):
                attente = 5 * (tentative + 1)
                log.warning("Adzuna : quota/indisponibilite, pause de %ss", attente)
                time.sleep(attente)
                continue
            if reponse.status_code == 401:
                raise SourceError(
                    "Adzuna : cles refusees (401). Verifie ADZUNA_APP_ID / ADZUNA_APP_KEY."
                )
            raise SourceError(
                f"Adzuna : HTTP {reponse.status_code} - {reponse.text[:300]}"
            )
        raise SourceError("Adzuna : echec apres 3 tentatives.")

    def collecter(self, depuis_jours: int) -> list[Offer]:
        offres: dict[str, Offer] = {}
        par_page = min(int(self.cfg.get("resultats_par_page", MAX_PAR_PAGE)), MAX_PAR_PAGE)
        max_pages = int(self.cfg.get("max_pages", 2))
        # Adzuna raisonne en jours entiers : une marge evite de rater les offres
        # publiees juste apres le run de la veille.
        max_jours = max(int(self.cfg.get("max_jours", 2)), depuis_jours + 1)

        for mots_cles in self.cfg["mots_cles"]:
            recuperees = 0
            for page in range(1, max_pages + 1):
                params = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": par_page,
                    "what": mots_cles,
                    "max_days_old": max_jours,
                    "sort_by": "date",
                    "content-type": "application/json",
                }
                data = self.get(page, params)
                time.sleep(DELAI_ENTRE_APPELS)

                resultats = data.get("results") or []
                for brut in resultats:
                    offre = self.normaliser(brut)
                    if offre:
                        offres[offre.id] = offre
                recuperees += len(resultats)

                if len(resultats) < par_page:
                    break

            log.info("Adzuna         : %-32s -> %3d offres", mots_cles, recuperees)

        return list(offres.values())

    @staticmethod
    def normaliser(brut: dict) -> Offer | None:
        identifiant = brut.get("id")
        if not identifiant:
            return None

        # Adzuna ne distingue pas le stage : contract_time/contract_type ne
        # donnent que temps plein/partiel et permanent/contract. Le tri se fait
        # dans filters.py a partir de l'intitule et de la description.
        contrat = " ".join(
            v for v in (brut.get("contract_type"), brut.get("contract_time")) if v
        )
        description = brut.get("description") or ""

        return Offer(
            id=f"adzuna:{identifiant}",
            source="Adzuna",
            intitule=brut.get("title") or "",
            entreprise=(brut.get("company") or {}).get("display_name") or "",
            lieu=(brut.get("location") or {}).get("display_name") or "",
            type_contrat=contrat or "stage (a verifier)",
            date_publication=(brut.get("created") or "")[:10],
            url=brut.get("redirect_url") or "",
            description=description,
            duree_source=description,
        )
