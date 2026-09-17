"""Tests de normalisation des reponses d'API, sur des fixtures reelles.

Aucun appel reseau : ces tests protegent le mapping des champs, qui est ce qui
casse en premier quand une API change.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stages.filters import extraire_duree_semaines
from stages.models import COLONNES, Offer
from stages.sources.adzuna import Adzuna
from stages.sources.france_travail import FranceTravail, publiee_depuis

FIXTURES = Path(__file__).parent / "fixtures"


def charger(nom: str) -> dict:
    return json.loads((FIXTURES / nom).read_text(encoding="utf-8"))


class TestFranceTravail:
    def setup_method(self):
        self.resultats = charger("france_travail_search.json")["resultats"]

    def test_mapping_complet(self):
        offre = FranceTravail.normaliser(self.resultats[0])
        assert offre.id == "ft:187TYVM"
        assert offre.source == "France Travail"
        assert offre.entreprise == "ACME DRONES"
        assert offre.lieu == "91 - PALAISEAU"
        assert offre.type_contrat == "CDD - 6 Mois"
        # La date est tronquee au jour : la feuille n'a pas besoin de l'heure.
        assert offre.date_publication == "2026-09-14"
        assert offre.url.endswith("/187TYVM")

    def test_duree_lue_depuis_le_libelle_de_contrat(self):
        offre = FranceTravail.normaliser(self.resultats[0])
        assert extraire_duree_semaines(offre.duree_source) == 26

    def test_url_de_repli_sans_origineOffre(self):
        offre = FranceTravail.normaliser(self.resultats[1])
        assert offre.url == (
            "https://candidat.francetravail.fr/offres/recherche/detail/187ZZZZ"
        )
        assert offre.entreprise == ""  # entreprise absente de la reponse

    def test_offre_sans_id_ignoree(self):
        assert FranceTravail.normaliser(self.resultats[2]) is None

    @pytest.mark.parametrize(
        "jours,attendu", [(1, 1), (2, 3), (7, 7), (10, 14), (40, 31)]
    )
    def test_fenetre_arrondie_aux_valeurs_autorisees(self, jours, attendu):
        assert publiee_depuis(jours) == attendu


class TestAdzuna:
    def setup_method(self):
        self.resultats = charger("adzuna_search.json")["results"]

    def test_mapping_complet(self):
        offre = Adzuna.normaliser(self.resultats[0])
        assert offre.id == "adzuna:4123456789"
        assert offre.source == "Adzuna"
        assert offre.entreprise == "NanoSat"
        assert offre.lieu == "Toulouse, Haute-Garonne"
        assert offre.type_contrat == "contract full_time"
        assert offre.date_publication == "2026-09-14"

    def test_duree_lue_dans_la_description(self):
        offre = Adzuna.normaliser(self.resultats[0])
        assert extraire_duree_semaines(offre.duree_source) == 26

    def test_contrat_par_defaut_quand_absent(self):
        offre = Adzuna.normaliser(self.resultats[1])
        assert offre.type_contrat == "stage (a verifier)"


class TestDedoublonnage:
    def test_meme_annonce_sur_deux_sources_meme_cle(self):
        """Le cas qui justifie la cle floue : une annonce rediffusee."""
        ft = FranceTravail.normaliser(charger("france_travail_search.json")["resultats"][0])
        adzuna = Adzuna.normaliser(charger("adzuna_search.json")["results"][1])
        assert ft.id != adzuna.id
        assert ft.cle_floue == adzuna.cle_floue

    def test_prefixe_stage_ne_cree_pas_de_doublon(self):
        """Adzuna rediffuse la meme annonce prefixee, l'intitule change seul."""
        commun = dict(
            source="Adzuna",
            entreprise="ACME",
            lieu="Paris",
            type_contrat="contract",
            date_publication="2026-09-15",
            url="https://example.org",
        )
        nue = Offer(id="adzuna:1", intitule="Stage de fin d'etudes en electronique", **commun)
        prefixee = Offer(
            id="adzuna:2", intitule="Stage : Stage de fin d'etudes en electronique", **commun
        )
        assert nue.cle_floue == prefixee.cle_floue

    def test_intitule_reduit_au_seul_marqueur_garde_une_cle(self):
        commun = dict(
            source="Adzuna",
            entreprise="ACME",
            lieu="Paris",
            type_contrat="contract",
            date_publication="2026-09-15",
            url="https://example.org",
        )
        assert Offer(id="adzuna:3", intitule="Stage", **commun).cle_floue == "stage|acme"


class TestLigneFeuille:
    def test_structure_de_la_ligne(self):
        offre = Offer(
            id="ft:1",
            source="France Travail",
            intitule="Stage drone",
            entreprise="ACME",
            lieu="Paris",
            type_contrat="CDD - 6 Mois",
            date_publication="2026-09-15",
            url="https://example.org",
            duree_semaines=26,
            score=6,
            mots_cles=("drone", "embarque"),
        )
        ligne = offre.to_row("2026-09-15 06:00")
        assert len(ligne) == len(COLONNES)
        assert ligne[COLONNES.index("duree_semaines")] == "26"
        assert ligne[COLONNES.index("mots_cles")] == "drone, embarque"
        # Les trois colonnes de suivi restent vides : elles appartiennent a
        # l'utilisateur et ne doivent jamais etre ecrasees.
        assert ligne[-3:] == ["", "", ""]


class TestSecurisationSheet:
    def test_formule_neutralisee(self):
        pytest.importorskip("gspread")
        from stages.sinks.google_sheet import securiser

        assert securiser("=IMPORTXML(A1)").startswith("'")
        assert securiser("Stage drone") == "Stage drone"
