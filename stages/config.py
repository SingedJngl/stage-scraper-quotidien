"""Chargement de la configuration (config.yaml) et des secrets (environnement)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

RACINE = Path(__file__).resolve().parent.parent
CONFIG_PAR_DEFAUT = RACINE / "config.yaml"

log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Configuration ou secret manquant : le run ne peut pas demarrer."""


@dataclass(slots=True)
class Secrets:
    ft_client_id: str = ""
    ft_client_secret: str = ""
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    sheet_id: str = ""
    google_credentials: dict | None = None

    @property
    def france_travail_pret(self) -> bool:
        return bool(self.ft_client_id and self.ft_client_secret)

    @property
    def adzuna_pret(self) -> bool:
        return bool(self.adzuna_app_id and self.adzuna_app_key)

    @property
    def sheet_pret(self) -> bool:
        return bool(self.sheet_id and self.google_credentials)


def charger_env() -> None:
    """Charge un .env local s'il existe. En CI les secrets sont deja dans l'env."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # dependance optionnelle pour les tests
        return
    fichier = RACINE / ".env"
    if fichier.exists():
        load_dotenv(fichier)
        log.debug("Secrets locaux charges depuis %s", fichier)


def _credentials_google() -> dict | None:
    """Le JSON du compte de service, depuis la variable CI ou un fichier local."""
    brut = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if brut:
        try:
            return json.loads(brut)
        except json.JSONDecodeError as exc:
            raise ConfigError(
                "GCP_SERVICE_ACCOUNT_JSON n'est pas un JSON valide : colle le contenu "
                "complet du fichier de cle du compte de service."
            ) from exc

    chemin = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if chemin:
        fichier = Path(chemin)
        if not fichier.exists():
            raise ConfigError(f"Fichier de credentials introuvable : {fichier}")
        return json.loads(fichier.read_text(encoding="utf-8"))
    return None


def charger_secrets() -> Secrets:
    charger_env()
    return Secrets(
        ft_client_id=os.getenv("FT_CLIENT_ID", "").strip(),
        ft_client_secret=os.getenv("FT_CLIENT_SECRET", "").strip(),
        adzuna_app_id=os.getenv("ADZUNA_APP_ID", "").strip(),
        adzuna_app_key=os.getenv("ADZUNA_APP_KEY", "").strip(),
        sheet_id=os.getenv("SHEET_ID", "").strip(),
        google_credentials=_credentials_google(),
    )


def charger_config(chemin: str | Path | None = None) -> dict:
    fichier = Path(chemin) if chemin else CONFIG_PAR_DEFAUT
    if not fichier.exists():
        raise ConfigError(f"Fichier de configuration introuvable : {fichier}")
    with fichier.open(encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    if not cfg.get("recherche", {}).get("mots_cles"):
        raise ConfigError("config.yaml : 'recherche.mots_cles' est vide.")
    return cfg
