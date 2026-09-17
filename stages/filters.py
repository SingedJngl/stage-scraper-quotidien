"""Tri des offres : exclusions, duree minimale, score de pertinence.

Uniquement des fonctions pures : c'est la partie qu'on retouche le plus souvent
apres quelques jours d'usage reel, et celle qui est couverte par les tests.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .models import Offer, normaliser

# Une annonce parle en mois, la contrainte est en semaines.
SEMAINES_PAR_MOIS = 4.345

# Au-dela, le nombre lu n'est plus une duree de stage mais un chiffre attrape
# ailleurs dans l'annonce ("5 ans d'experience"). Mieux vaut "duree inconnue",
# que la feuille affiche "?", qu'une duree fausse presentee comme un fait.
DUREE_MAX_PLAUSIBLE_SEMAINES = 52

# Une offre n'est un stage que si elle le dit. Sans ce garde-fou, une recherche
# "stage electronique" sur Adzuna - un moteur de pertinence, pas un ET - ramene
# des postes d'ingenieur en CDI qui franchissent tous les autres filtres.
MARQUEURS_STAGE_DEFAUT = ("stage", "stagiaire", "internship", "trainee")

# Libelles de contrat qui disqualifient une offre dont seul le corps d'annonce
# parle de stage : beaucoup de CDI mentionnent les stagiaires en bas de page.
CONTRATS_NON_STAGE = ("permanent", "cdi", "indetermine")

_PATTERNS_DUREE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(\d{1,2})\s*(?:a|-|/|et)\s*(\d{1,2})\s*mois"), "intervalle_mois"),
    (re.compile(r"(\d{1,2})\s*mois"), "mois"),
    (re.compile(r"(\d{1,3})\s*(?:a|-|/|et)\s*(\d{1,3})\s*semaines?"), "intervalle_semaines"),
    (re.compile(r"(\d{1,3})\s*semaines?"), "semaines"),
    (re.compile(r"(\d)\s*ans?\b"), "annees"),
]


def contient_mot(texte_normalise: str, terme: str) -> bool:
    """Recherche d'un terme entier dans un texte deja normalise.

    On n'utilise pas \b : les termes peuvent finir par un caractere non-mot
    (c++), ce que \b gere mal. Les lookarounds sur [a-z0-9] evitent aussi que
    "cdi" matche "cdiscount" ou "vie" matche "vienne".
    """
    terme = normaliser(terme)
    if not terme:
        return False
    motif = rf"(?<![a-z0-9]){re.escape(terme)}(?![a-z0-9])"
    return re.search(motif, texte_normalise) is not None


def extraire_duree_semaines(*textes: str) -> int | None:
    """Duree du stage en semaines, lue dans les textes par ordre de fiabilite.

    Les textes sont examines dans l'ordre fourni (typiquement : libelle de
    contrat de l'API, puis intitule, puis description) et le premier motif
    reconnu gagne. Renvoie None si aucune duree n'est lisible.
    """
    textes_normalises = [normaliser(t) for t in textes if t]

    for texte in textes_normalises:
        for pattern, genre in _PATTERNS_DUREE:
            m = pattern.search(texte)
            if not m:
                continue
            if genre == "intervalle_mois":
                # "3 a 6 mois" : on retient la borne HAUTE. Une duree est
                # souvent negociable, et le but du pipeline est de ne pas rater
                # une offre : la colonne duree de la feuille permet de trancher.
                valeur = round(max(int(m.group(1)), int(m.group(2))) * SEMAINES_PAR_MOIS)
            elif genre == "mois":
                valeur = round(int(m.group(1)) * SEMAINES_PAR_MOIS)
            elif genre == "intervalle_semaines":
                valeur = max(int(m.group(1)), int(m.group(2)))
            elif genre == "semaines":
                valeur = int(m.group(1))
            else:  # annees
                valeur = int(m.group(1)) * 52
            # Une duree invraisemblable vient d'un chiffre qui parlait d'autre
            # chose : on continue de chercher plutot que de la retenir.
            if valeur <= DUREE_MAX_PLAUSIBLE_SEMAINES:
                return valeur

    return None


def est_exclu(intitule: str, exclusions: list[str]) -> str | None:
    """Renvoie le terme qui disqualifie l'intitule, ou None."""
    intitule_normalise = normaliser(intitule)
    for terme in exclusions:
        if contient_mot(intitule_normalise, terme):
            return terme
    return None


def est_un_stage(offre: Offer, marqueurs: list[str] | None = None) -> bool:
    """Vrai si l'annonce se presente elle-meme comme un stage.

    Le marqueur dans l'intitule fait foi. Trouve uniquement dans le corps de
    l'annonce, il ne suffit pas quand le contrat est explicitement permanent :
    une fiche de poste en CDI cite souvent les stages sans en proposer un.
    """
    termes = marqueurs or list(MARQUEURS_STAGE_DEFAUT)

    titre = normaliser(offre.intitule)
    if any(contient_mot(titre, terme) for terme in termes):
        return True

    contrat = normaliser(offre.type_contrat)
    if any(contient_mot(contrat, terme) for terme in CONTRATS_NON_STAGE):
        return False

    description = normaliser(offre.description)
    return any(contient_mot(description, terme) for terme in termes)


def calculer_score(
    intitule: str, description: str, poids: dict[str, int]
) -> tuple[int, tuple[str, ...]]:
    """Score de pertinence metier et liste des termes reconnus.

    Un terme dans l'intitule vaut son poids plein ; dans la description il vaut
    la moitie (arrondie au superieur) : une annonce intitulee "stage FPGA" est
    plus pertinente qu'une annonce qui cite FPGA au detour d'un paragraphe.
    """
    titre_normalise = normaliser(intitule)
    description_normalisee = normaliser(description)
    score = 0
    trouves: list[tuple[int, str]] = []

    for terme, poids_terme in poids.items():
        if contient_mot(titre_normalise, terme):
            score += poids_terme
            trouves.append((poids_terme, terme))
        elif contient_mot(description_normalisee, terme):
            gagne = math.ceil(poids_terme / 2)
            score += gagne
            trouves.append((gagne, terme))

    trouves.sort(key=lambda t: (-t[0], t[1]))
    return score, tuple(terme for _, terme in trouves)


def filtrer(offres: list[Offer], cfg: dict) -> tuple[list[Offer], Counter]:
    """Applique exclusions, duree minimale et score. Renseigne les offres gardees.

    Renvoie les offres retenues (triees par score decroissant) et un compteur des
    motifs de rejet, qui alimente le log et l'onglet Runs.
    """
    exclusions = cfg.get("exclusions", [])
    marqueurs = cfg.get("marqueurs_stage") or list(MARQUEURS_STAGE_DEFAUT)
    exiger_stage = cfg.get("exiger_marqueur_stage", True)
    poids = cfg.get("mots_cles_scores", {})
    duree_min = cfg.get("duree_min_semaines", 16)
    garder_inconnue = cfg.get("garder_duree_inconnue", True)
    score_min = cfg.get("score_min", 0)

    stats: Counter = Counter()
    retenues: list[Offer] = []

    for offre in offres:
        stats["examinees"] += 1

        terme_exclu = est_exclu(offre.intitule, exclusions)
        if terme_exclu:
            stats["rejet_exclusion"] += 1
            continue

        if exiger_stage and not est_un_stage(offre, marqueurs):
            stats["rejet_pas_un_stage"] += 1
            continue

        if offre.duree_semaines is None:
            offre.duree_semaines = extraire_duree_semaines(
                offre.duree_source, offre.intitule, offre.description
            )
        if offre.duree_semaines is None:
            if not garder_inconnue:
                stats["rejet_duree_inconnue"] += 1
                continue
            stats["duree_inconnue_gardee"] += 1
        elif offre.duree_semaines < duree_min:
            stats["rejet_trop_court"] += 1
            continue

        offre.score, offre.mots_cles = calculer_score(offre.intitule, offre.description, poids)
        if offre.score < score_min:
            stats["rejet_score"] += 1
            continue

        stats["retenues"] += 1
        retenues.append(offre)

    retenues.sort(key=lambda o: o.score, reverse=True)
    return retenues, stats
