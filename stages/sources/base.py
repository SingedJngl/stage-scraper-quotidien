"""Contrat commun a toutes les sources d'offres."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..models import Offer

log = logging.getLogger(__name__)


class SourceError(RuntimeError):
    """Echec d'une source. Le pipeline continue avec les autres sources."""


class Source(ABC):
    """Une origine d'offres (une API, un flux...).

    `collecter` doit renvoyer des offres deja normalisees, sans filtrage metier :
    tout le tri se fait ensuite dans filters.py, au meme endroit pour tout le monde.
    """

    # nom technique (logs, config) et libelle affichable (feuille, journal)
    nom: str = "source"
    libelle: str = "Source"

    @abstractmethod
    def collecter(self, depuis_jours: int) -> list[Offer]:
        ...
