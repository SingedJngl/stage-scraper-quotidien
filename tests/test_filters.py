"""Tests du tri des offres : c'est la partie qu'on retouche le plus souvent."""

from __future__ import annotations

import pytest

from stages.filters import (
    calculer_score,
    contient_mot,
    est_exclu,
    est_un_stage,
    extraire_duree_semaines,
    filtrer,
)
from stages.models import Offer, normaliser

EXCLUSIONS = ["alternance", "apprentissage", "cdi", "contrat pro"]
POIDS = {"embarque": 3, "drone": 3, "c++": 1, "robotique": 2}


def offre(intitule: str, description: str = "", **kwargs) -> Offer:
    defauts = dict(
        id=kwargs.pop("id", "ft:1"),
        source="France Travail",
        entreprise="ACME",
        lieu="Paris",
        type_contrat="CDD",
        date_publication="2026-09-15",
        url="https://example.org/1",
    )
    defauts.update(kwargs)
    return Offer(intitule=intitule, description=description, **defauts)


class TestNormalisation:
    def test_supprime_accents_et_ponctuation(self):
        assert normaliser("Systèmes Embarqués (H/F)") == "systemes embarques h f"

    def test_conserve_les_caracteres_techniques(self):
        assert normaliser("Stage C++ / C#") == "stage c++ c#"


class TestContientMot:
    def test_mot_entier_seulement(self):
        assert contient_mot("stage en cdi deguise", "cdi")
        # "cdi" ne doit pas matcher a l'interieur d'un autre mot
        assert not contient_mot("stage chez cdiscount", "cdi")

    def test_terme_avec_caractere_special(self):
        assert contient_mot(normaliser("Dev C++ embarque"), "c++")

    def test_insensible_aux_accents(self):
        assert contient_mot(normaliser("Systèmes embarqués"), "embarques")


class TestDuree:
    @pytest.mark.parametrize(
        "texte,attendu",
        [
            ("CDD - 6 Mois", 26),
            ("Stage de 4 mois", 17),
            ("stage 24 semaines", 24),
            ("contrat de 1 an", 52),
            ("", None),
            ("Stage sans duree precisee", None),
        ],
    )
    def test_lecture_directe(self, texte, attendu):
        assert extraire_duree_semaines(texte) == attendu

    def test_intervalle_retient_la_borne_haute(self):
        # Une duree negociable ne doit pas faire perdre l'offre.
        assert extraire_duree_semaines("stage de 3 a 6 mois") == 26

    def test_fin_etudes_ne_suppose_aucune_duree(self):
        # Le stage vise est un stage ING4, pas un PFE : "fin d'etudes" ne dit
        # rien de la duree, et une duree inventee s'afficherait dans la feuille
        # comme un fait verifie.
        assert extraire_duree_semaines("Stage de fin d'etudes - PFE") is None

    def test_duree_invraisemblable_ignoree(self):
        # Observe en production : "5 ans d'experience" donnait 260 semaines.
        assert extraire_duree_semaines("stage, 5 ans d'experience appreciee") is None

    def test_duree_plausible_prefere_a_la_mention_parasite(self):
        assert extraire_duree_semaines("5 ans d'experience", "stage de 6 mois") == 26

    def test_ordre_de_priorite_des_textes(self):
        # Le libelle de contrat prime sur la description.
        assert extraire_duree_semaines("CDD - 6 Mois", "", "experience de 2 mois") == 26


class TestExclusions:
    def test_alternance_rejetee(self):
        assert est_exclu("Alternance - Ingenieur embarque", EXCLUSIONS) == "alternance"

    def test_stage_conserve(self):
        assert est_exclu("Stage systemes embarques", EXCLUSIONS) is None

    def test_exclusion_multi_mots(self):
        assert est_exclu("Contrat pro electronique", EXCLUSIONS) == "contrat pro"


class TestMarqueurStage:
    def test_marqueur_dans_l_intitule(self):
        assert est_un_stage(offre("Stage systemes embarques"))

    def test_poste_en_cdi_sans_marqueur_rejete(self):
        # Le cas qui motive le garde-fou : le moteur d'Adzuna classe par
        # pertinence, pas par ET, et remonte des CDI sur une requete "stage ...".
        assert not est_un_stage(
            offre("Ingenieur Systemes Embarques Junior F/H", type_contrat="permanent")
        )

    def test_marqueur_en_description_suffit_hors_cdi(self):
        assert est_un_stage(
            offre(
                "Ingenieur developpement logiciel",
                "Nous proposons un stage de 5 mois",
                type_contrat="contract",
            )
        )

    def test_marqueur_en_description_ne_suffit_pas_en_cdi(self):
        # Une fiche de poste en CDI cite souvent les stagiaires en bas de page.
        assert not est_un_stage(
            offre(
                "Ingenieur developpement logiciel",
                "Nous accueillons aussi des stagiaires chaque annee",
                type_contrat="permanent",
            )
        )

    def test_pfe_n_est_pas_un_marqueur(self):
        assert not est_un_stage(offre("Offre PFE electronique", type_contrat="contract"))


class TestScore:
    def test_intitule_vaut_plus_que_description(self):
        score_titre, _ = calculer_score("Stage drone", "", POIDS)
        score_desc, _ = calculer_score("Stage", "projet de drone", POIDS)
        assert score_titre == 3
        assert score_desc == 2  # moitie arrondie au superieur
        assert score_titre > score_desc

    def test_termes_reconnus_tries_par_poids(self):
        _, mots = calculer_score("Stage drone embarque c++", "", POIDS)
        assert mots[0] in ("drone", "embarque")
        assert "c++" in mots

    def test_aucun_mot_cle(self):
        assert calculer_score("Stage comptabilite", "", POIDS) == (0, ())


class TestFiltrer:
    cfg = {
        "exclusions": EXCLUSIONS,
        "mots_cles_scores": POIDS,
        "duree_min_semaines": 16,
        "garder_duree_inconnue": True,
        "score_min": 3,
    }

    def test_offre_conforme_retenue(self):
        gardees, stats = filtrer([offre("Stage drone", duree_source="CDD - 6 Mois")], self.cfg)
        assert len(gardees) == 1
        assert gardees[0].duree_semaines == 26
        assert stats["retenues"] == 1

    def test_stage_trop_court_rejete(self):
        gardees, stats = filtrer([offre("Stage drone", duree_source="CDD - 2 Mois")], self.cfg)
        assert gardees == []
        assert stats["rejet_trop_court"] == 1

    def test_alternance_rejetee_avant_tout(self):
        gardees, stats = filtrer(
            [offre("Alternance drone embarque", duree_source="CDD - 12 Mois")], self.cfg
        )
        assert gardees == []
        assert stats["rejet_exclusion"] == 1

    def test_hors_domaine_rejete(self):
        gardees, stats = filtrer(
            [offre("Stage assistant commercial", duree_source="CDD - 6 Mois")], self.cfg
        )
        assert gardees == []
        assert stats["rejet_score"] == 1

    def test_duree_inconnue_conservee_et_marquee(self):
        gardees, stats = filtrer([offre("Stage drone embarque")], self.cfg)
        assert len(gardees) == 1
        assert gardees[0].duree_semaines is None
        assert gardees[0].to_row()[8] == "?"
        assert stats["duree_inconnue_gardee"] == 1

    def test_duree_inconnue_rejetee_si_desactivee(self):
        cfg = dict(self.cfg, garder_duree_inconnue=False)
        gardees, stats = filtrer([offre("Stage drone embarque")], cfg)
        assert gardees == []
        assert stats["rejet_duree_inconnue"] == 1

    def test_poste_en_cdi_rejete_avant_le_score(self):
        gardees, stats = filtrer(
            [offre("Ingenieur drone embarque", type_contrat="permanent")], self.cfg
        )
        assert gardees == []
        assert stats["rejet_pas_un_stage"] == 1

    def test_garde_fou_desactivable(self):
        cfg = dict(self.cfg, exiger_marqueur_stage=False)
        gardees, _ = filtrer(
            [offre("Ingenieur drone embarque", type_contrat="permanent")], cfg
        )
        assert len(gardees) == 1

    def test_tri_par_score_decroissant(self):
        offres = [
            offre("Stage drone", duree_source="CDD - 6 Mois", id="ft:1"),
            offre("Stage drone embarque robotique", duree_source="CDD - 6 Mois", id="ft:2"),
        ]
        gardees, _ = filtrer(offres, self.cfg)
        assert [o.id for o in gardees] == ["ft:2", "ft:1"]
