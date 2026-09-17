"""Source principale : API Offres d'emploi v2 de France Travail.

Documentation : https://francetravail.io/data/api/offres-emploi
Authentification OAuth2 client_credentials, ~300 000 offres en temps reel.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from ..models import Offer
from .base import Source, SourceError

log = logging.getLogger(__name__)

TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
BASE_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2"
SCOPE = "api_offresdemploiv2 o2dsoffre"

# L'API n'accepte que ces valeurs pour publieeDepuis.
PUBLIEE_DEPUIS_VALIDES = (1, 3, 7, 14, 31)
# L'API plafonne a 150 resultats par page et a un index de depart de 3000.
MAX_PAR_PAGE = 150
OFFSET_MAX = 3000
# La documentation impose 10 requetes/seconde : on reste tres en dessous.
DELAI_ENTRE_APPELS = 0.2


def publiee_depuis(jours: int) -> int:
    """Arrondit a la valeur superieure acceptee par l'API."""
    for valeur in PUBLIEE_DEPUIS_VALIDES:
        if jours <= valeur:
            return valeur
    return PUBLIEE_DEPUIS_VALIDES[-1]


class FranceTravail(Source):
    nom = "france_travail"
    libelle = "France Travail"

    def __init__(self, client_id: str, client_secret: str, cfg: dict) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.cfg = cfg
        self.session = requests.Session()
        self._token: str | None = None
        self._token_expire: datetime = datetime.now(timezone.utc)

    # --- authentification -------------------------------------------------

    def obtenir_token(self) -> str:
        """Token client_credentials, mis en cache pour la duree du run."""
        marge = timedelta(seconds=60)
        if self._token and datetime.now(timezone.utc) + marge < self._token_expire:
            return self._token

        reponse = self.session.post(
            TOKEN_URL,
            params={"realm": "/partenaire"},
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        if reponse.status_code != 200:
            raise SourceError(
                "France Travail : authentification refusee "
                f"({reponse.status_code}). Verifie FT_CLIENT_ID / FT_CLIENT_SECRET "
                "et l'abonnement de l'application a l'API Offres d'emploi v2. "
                f"Reponse : {reponse.text[:300]}"
            )
        data = reponse.json()
        self._token = data["access_token"]
        self._token_expire = datetime.now(timezone.utc) + timedelta(
            seconds=int(data.get("expires_in", 1200))
        )
        log.debug("France Travail : token obtenu (expire dans %ss)", data.get("expires_in"))
        return self._token

    # --- appels -----------------------------------------------------------

    def get(self, chemin: str, params: dict | None = None) -> dict | None:
        """GET authentifie. Renvoie None quand l'API n'a aucun resultat (204)."""
        url = f"{BASE_URL}{chemin}"
        for tentative in range(3):
            reponse = self.session.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {self.obtenir_token()}"},
                timeout=60,
            )
            # 206 = pagination partielle : c'est le cas nominal d'une recherche.
            if reponse.status_code in (200, 206):
                return reponse.json()
            if reponse.status_code == 204:
                return None
            if reponse.status_code == 429:
                attente = int(reponse.headers.get("Retry-After", 2 * (tentative + 1)))
                log.warning("France Travail : quota atteint, pause de %ss", attente)
                time.sleep(attente)
                continue
            if reponse.status_code >= 500:
                time.sleep(2 * (tentative + 1))
                continue
            raise SourceError(
                f"France Travail {chemin} : HTTP {reponse.status_code} "
                f"- {reponse.text[:300]}"
            )
        raise SourceError(f"France Travail {chemin} : echec apres 3 tentatives.")

    def referentiel(self, nom: str) -> list[dict]:
        """Liste un referentiel (typesContrats, naturesContrats, metiers...)."""
        return self.get(f"/referentiel/{nom}") or []

    # --- collecte ---------------------------------------------------------

    def params_recherche(self, mots_cles: str, depuis_jours: int) -> dict:
        params: dict[str, str | int] = {
            "motsCles": mots_cles,
            "publieeDepuis": publiee_depuis(depuis_jours),
            "sort": 1,  # 1 = date de creation decroissante
        }
        if self.cfg.get("type_contrat"):
            params["typeContrat"] = self.cfg["type_contrat"]
        if self.cfg.get("nature_contrat"):
            params["natureContrat"] = self.cfg["nature_contrat"]
        codes_rome = self.cfg.get("codes_rome") or []
        if codes_rome:
            params["codeROME"] = ",".join(str(c) for c in codes_rome[:5])
        if self.cfg.get("filtre_duree_api"):
            params["dureeContratMin"] = self.cfg.get("duree_contrat_min_mois", 4)
        return params

    def collecter(self, depuis_jours: int) -> list[Offer]:
        # Dictionnaire indexe par id : les requetes se recouvrent souvent
        # (une offre "drone embarque" ressort sur deux mots-cles).
        offres: dict[str, Offer] = {}
        par_page = min(int(self.cfg.get("resultats_par_page", MAX_PAR_PAGE)), MAX_PAR_PAGE)
        max_pages = int(self.cfg.get("max_pages", 3))

        for mots_cles in self.cfg["mots_cles"]:
            params_base = self.params_recherche(mots_cles, depuis_jours)
            recuperees = 0

            for page in range(max_pages):
                debut = page * par_page
                if debut > OFFSET_MAX:
                    break
                params = dict(params_base, range=f"{debut}-{debut + par_page - 1}")
                data = self.get("/offres/search", params)
                time.sleep(DELAI_ENTRE_APPELS)

                resultats = (data or {}).get("resultats") or []
                for brut in resultats:
                    offre = self.normaliser(brut)
                    if offre:
                        offres[offre.id] = offre
                recuperees += len(resultats)

                if len(resultats) < par_page:
                    break  # derniere page atteinte

            log.info("France Travail : %-32s -> %3d offres", mots_cles, recuperees)

        return list(offres.values())

    @staticmethod
    def normaliser(brut: dict) -> Offer | None:
        identifiant = brut.get("id")
        if not identifiant:
            return None

        origine = brut.get("origineOffre") or {}
        url = origine.get("urlOrigine") or (
            f"https://candidat.francetravail.fr/offres/recherche/detail/{identifiant}"
        )
        # "CDD - 6 Mois" : la source de duree la plus fiable de cette API.
        libelle_contrat = brut.get("typeContratLibelle") or brut.get("typeContrat") or ""

        return Offer(
            id=f"ft:{identifiant}",
            source="France Travail",
            intitule=brut.get("intitule") or "",
            entreprise=(brut.get("entreprise") or {}).get("nom") or "",
            lieu=(brut.get("lieuTravail") or {}).get("libelle") or "",
            type_contrat=libelle_contrat,
            date_publication=(brut.get("dateCreation") or "")[:10],
            url=url,
            description=brut.get("description") or "",
            duree_source=libelle_contrat,
        )
