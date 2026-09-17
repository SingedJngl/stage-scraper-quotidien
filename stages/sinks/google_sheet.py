"""Destination : une Google Sheet servant de tableau de bord de candidature.

Le pipeline n'ajoute que des lignes (`append_rows`) et ne modifie jamais
l'existant : les colonnes statut / date_candidature / notes appartiennent a
l'utilisateur et doivent survivre a tous les runs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import gspread
from google.oauth2.service_account import Credentials

from ..models import (
    COL_ENTREPRISE,
    COL_ID,
    COL_INTITULE,
    COLONNES,
    Offer,
    cle_intitule,
    normaliser,
)

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
ENTETE_RUNS = ["horodatage", "source", "recuperees", "retenues", "ajoutees", "details"]

# Google Sheets interprete comme formule toute cellule commencant par l'un de
# ces caracteres. Les intitules viennent d'APIs externes : on les neutralise.
AMORCES_FORMULE = ("=", "+", "-", "@")


class SheetError(RuntimeError):
    """Acces a la feuille impossible : on preferera ne rien ecrire du tout."""


def securiser(valeur: str) -> str:
    """Empeche qu'un intitule d'offre soit interprete comme une formule."""
    if valeur.startswith(AMORCES_FORMULE):
        # L'apostrophe initiale force le texte et n'est pas affichee par Sheets.
        return "'" + valeur
    return valeur


class GoogleSheet:
    def __init__(self, credentials: dict, sheet_id: str, cfg: dict) -> None:
        self.sheet_id = sheet_id
        self.nom_offres = cfg.get("onglet_offres", "Offres")
        self.nom_runs = cfg.get("onglet_runs", "Runs")
        try:
            creds = Credentials.from_service_account_info(credentials, scopes=SCOPES)
            self.client = gspread.authorize(creds)
            self.classeur = self.client.open_by_key(sheet_id)
        except gspread.exceptions.APIError as exc:
            raise SheetError(
                f"Acces refuse au classeur {sheet_id}. La feuille est-elle partagee "
                f"en Editeur avec l'adresse du compte de service "
                f"({credentials.get('client_email', '?')}) ? Detail : {exc}"
            ) from exc
        except Exception as exc:  # credentials mal formes, reseau, etc.
            raise SheetError(f"Connexion a Google Sheets impossible : {exc}") from exc

    def onglet(self, nom: str, entete: list[str]):
        """Recupere un onglet, le cree avec son en-tete s'il n'existe pas."""
        try:
            feuille = self.classeur.worksheet(nom)
        except gspread.WorksheetNotFound:
            feuille = self.classeur.add_worksheet(title=nom, rows=1000, cols=len(entete))
            feuille.append_row(entete, value_input_option="RAW")
            feuille.freeze(rows=1)
            log.info("Onglet '%s' cree", nom)
        return feuille

    def deja_vues(self) -> tuple[set[str], set[str]]:
        """Identifiants et cles floues des offres deja presentes dans la feuille.

        C'est la feuille elle-meme qui sert de memoire du pipeline : pas de
        fichier d'etat a maintenir, et l'historique reste visible.
        """
        feuille = self.onglet(self.nom_offres, COLONNES)
        try:
            lignes = feuille.get_all_values()
        except gspread.exceptions.APIError as exc:
            raise SheetError(f"Lecture de l'onglet '{self.nom_offres}' impossible : {exc}") from exc

        ids: set[str] = set()
        cles: set[str] = set()
        for ligne in lignes[1:]:  # on saute l'en-tete
            if len(ligne) > COL_ID and ligne[COL_ID]:
                ids.add(ligne[COL_ID].strip())
            intitule = ligne[COL_INTITULE] if len(ligne) > COL_INTITULE else ""
            entreprise = ligne[COL_ENTREPRISE] if len(ligne) > COL_ENTREPRISE else ""
            if intitule:
                # Meme fabrication que Offer.cle_floue : une cle calculee
                # autrement ici ne matcherait jamais celle d'une offre fraiche.
                cles.add(f"{cle_intitule(intitule)}|{normaliser(entreprise)}")
        log.info("Feuille : %d offres deja enregistrees", len(ids))
        return ids, cles

    def ajouter(self, offres: list[Offer]) -> int:
        if not offres:
            return 0
        feuille = self.onglet(self.nom_offres, COLONNES)
        horodatage = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        lignes = []
        for offre in offres:
            ligne = offre.to_row(horodatage)
            # La colonne url reste brute pour rester cliquable.
            index_url = COLONNES.index("url")
            lignes.append(
                [securiser(v) if i != index_url else v for i, v in enumerate(ligne)]
            )
        feuille.append_rows(lignes, value_input_option="USER_ENTERED")
        return len(lignes)

    def journaliser(self, source: str, recuperees: int, retenues: int, ajoutees: int, details: str = "") -> None:
        """Une ligne par source et par run, pour reperer une panne silencieuse."""
        try:
            feuille = self.onglet(self.nom_runs, ENTETE_RUNS)
            feuille.append_row(
                [
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    source,
                    str(recuperees),
                    str(retenues),
                    str(ajoutees),
                    details[:500],
                ],
                value_input_option="RAW",
            )
        except Exception as exc:  # le journal ne doit jamais faire echouer un run
            log.warning("Journalisation impossible : %s", exc)
