"""Representation commune d'une offre, quelle que soit sa source."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Colonnes de la feuille, dans l'ordre. Les trois dernieres sont l'espace de
# travail de l'utilisateur : le pipeline ne les ecrit jamais.
COLONNES = [
    "id",
    "date_ajout",
    "date_publication",
    "source",
    "intitule",
    "entreprise",
    "lieu",
    "contrat",
    "duree_semaines",
    "score",
    "mots_cles",
    "url",
    "statut",
    "date_candidature",
    "notes",
]

# Index (0-based) des colonnes relues pour le dedoublonnage.
COL_ID = COLONNES.index("id")
COL_INTITULE = COLONNES.index("intitule")
COL_ENTREPRISE = COLONNES.index("entreprise")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, ponctuation ramenee a des espaces.

    Sert a la fois au filtrage et aux cles de dedoublonnage, pour que
    "Systemes Embarques (H/F)" et "systemes embarques h/f" se comparent.
    """
    if not texte:
        return ""
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    # On garde +, # et . qui portent du sens (c++, c#, v.i.e).
    nettoye = re.sub(r"[^a-z0-9+#.]+", " ", sans_accents.lower())
    return re.sub(r"\s+", " ", nettoye).strip()


# Mots liminaires sans valeur distinctive dans un intitule d'annonce. Adzuna
# rediffuse la meme offre prefixee "Stage :" ou "STAGE -", que la normalisation
# laisse en "stage stage ..." : sans ce nettoyage, une offre compte pour deux.
_MOTS_LIMINAIRES = ("offre de stage", "offre de", "stage", "stagiaire", "internship")


def cle_intitule(intitule: str) -> str:
    """Intitule ramene a son noyau, pour comparer deux rediffusions."""
    texte = normaliser(intitule)
    reduit = True
    while reduit:
        reduit = False
        for mot in _MOTS_LIMINAIRES:
            if texte.startswith(f"{mot} "):
                texte = texte[len(mot) + 1 :]
                reduit = True
    # Un intitule qui n'etait QUE "Stage" ne doit pas se reduire a une cle vide.
    return texte or normaliser(intitule)


@dataclass(slots=True)
class Offer:
    """Une offre normalisee, prete a etre filtree puis ecrite dans la feuille."""

    id: str
    source: str
    intitule: str
    entreprise: str
    lieu: str
    type_contrat: str
    date_publication: str  # ISO 8601, vide si la source ne la fournit pas
    url: str
    description: str = ""
    # Texte brut ou peut se lire une duree ("CDD - 6 Mois"), selon la source.
    duree_source: str = ""
    duree_semaines: int | None = None
    score: int = 0
    mots_cles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def cle_floue(self) -> str:
        """Cle de dedoublonnage inter-sources : meme poste, meme entreprise.

        France Travail et Adzuna rediffusent parfois la meme annonce avec des
        identifiants differents ; seul l'intitule et l'employeur les relient.
        """
        return f"{cle_intitule(self.intitule)}|{normaliser(self.entreprise)}"

    def to_row(self, date_ajout: str | None = None) -> list[str]:
        horodatage = date_ajout or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return [
            self.id,
            horodatage,
            self.date_publication,
            self.source,
            self.intitule,
            self.entreprise,
            self.lieu,
            self.type_contrat,
            str(self.duree_semaines) if self.duree_semaines is not None else "?",
            str(self.score),
            ", ".join(self.mots_cles),
            self.url,
            "",  # statut
            "",  # date_candidature
            "",  # notes
        ]
