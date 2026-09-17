# stage-scraper-quotidien

Pipeline automatisé qui récupère chaque matin les offres de **stage technique de
16 semaines minimum** en électronique / systèmes embarqués / drone, écarte le
bruit (alternance, CDI, stages trop courts, hors domaine), supprime les doublons et
ajoute les nouvelles offres dans une **Google Sheet** qui sert de tableau de
bord de candidature.

Deux sources :

| Source | Accès | Rôle |
|---|---|---|
| [API Adzuna](https://developer.adzuna.com/overview) | clé développeur gratuite | **source principale** : agrège des job boards privés, une dizaine de stages techniques par mois |
| [API Offres d'emploi v2 — France Travail](https://francetravail.io/data/api/offres-emploi) | OAuth2, gratuit | veille à coût nul, voir ci-dessous |

France Travail a d'abord été choisie comme source principale pour son volume
(~300 000 offres en temps réel). La mesure a démenti ce choix : sur 30 jours,
**796 offres techniques** (embarqué, électronique, drone, FPGA, robotique) et
**aucune** avec « stage » dans l'intitulé. Le référentiel de l'API le confirme —
il n'existe pas de code contrat « stage ». C'est une API d'offres d'emploi, et
les stages techniques n'y sont pratiquement pas publiés. La source reste active
parce qu'elle coûte cinq requêtes par run et qu'elle signalera l'offre le jour
où il y en aura une.

L'exécution quotidienne tourne sur **GitHub Actions** : rien à laisser allumé.

---

## 1. Créer les trois accès

### France Travail

1. Créer un compte sur [francetravail.io](https://francetravail.io).
2. Créer une application, puis **souscrire à l'API « Offres d'emploi v2 »**.
3. Noter l'**identifiant client** et la **clé secrète** → `FT_CLIENT_ID`, `FT_CLIENT_SECRET`.

### Adzuna

1. S'inscrire sur [developer.adzuna.com/signup](https://developer.adzuna.com/signup).
2. Récupérer l'**Application ID** et l'**Application Key** → `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`.

### Google Sheets

1. Créer un projet sur [console.cloud.google.com](https://console.cloud.google.com).
2. Activer l'**API Google Sheets**.
3. Créer un **compte de service**, puis une **clé JSON** (elle se télécharge).
4. Créer une Google Sheet vide, et la **partager en « Éditeur » avec l'adresse
   e-mail du compte de service** (`...@...iam.gserviceaccount.com`, visible dans
   le JSON sous `client_email`). Sans ce partage, le script ne voit pas la feuille.
5. L'**ID du classeur** est la partie entre `/d/` et `/edit` dans l'URL → `SHEET_ID`.

> Le dépôt est public : la clé JSON ne doit **jamais** y être commitée. Elle ne
> vit que dans un `.env` local (ignoré par git) et dans les secrets GitHub.

## 2. Configurer les secrets GitHub

`Settings → Secrets and variables → Actions → New repository secret`, six secrets :

`FT_CLIENT_ID`, `FT_CLIENT_SECRET`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`GCP_SERVICE_ACCOUNT_JSON` (le **contenu complet** du fichier JSON), `SHEET_ID`.

## 3. Installation locale

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows ;  source .venv/bin/activate sous Linux/macOS
pip install -r requirements-dev.txt
cp .env.example .env          # puis remplir le .env
```

## 4. Les référentiels France Travail

```bash
python scripts/discover_referentiels.py
python scripts/discover_referentiels.py metiers electronique   # codes ROME
```

Il n'y a **pas de code contrat « stage »** à reporter : le référentiel
`typesContrats` ne connaît que CDD, CDI, intérim, saisonnier, et
`naturesContrats` que l'apprentissage et la professionnalisation. C'est la
raison pour laquelle `france_travail.type_contrat` et `nature_contrat` restent
vides — le tri se fait entièrement dans `filters.py`. Le script garde son
utilité pour les codes ROME, si un jour on veut resserrer par métier.

Attention aussi à la syntaxe de recherche, qui n'est pas celle d'Adzuna : le
paramètre `motsCles` traite une suite de mots comme une **expression exacte**.
« stage electronique » renvoie zéro résultat là où « electronique » seul en
renvoie 4 040 ; c'est la virgule qui fait un ET. D'où la liste `mots_cles`
séparée dans le bloc `france_travail` de `config.yaml`.

## 5. Premier run

```bash
python -m stages --dry-run --since-days 7   # affiche tout, n'écrit rien
python -m stages                            # écrit dans la feuille
python -m stages                            # relancé : doit dire « 0 nouvelle offre »
```

Puis, sur GitHub : onglet **Actions → Collecte quotidienne → Run workflow** pour
valider les secrets. Ensuite le cron prend le relais (06:00 UTC, du lundi au
vendredi).

---

## Réglage du filtrage

Tout se règle dans [`config.yaml`](config.yaml), sans toucher au code :

- `recherche.mots_cles` — une entrée = une requête par source. Chaque entrée
  consomme du quota Adzuna : garder la liste courte.
- `filtrage.duree_min_semaines` — 16 par défaut.
- `filtrage.garder_duree_inconnue` — une offre dont la durée n'est écrite nulle
  part est **conservée** et marquée `?` dans la feuille, plutôt que perdue.
- `filtrage.exclusions` — termes qui disqualifient un **intitulé** (alternance,
  apprentissage, CDI…). Volontairement pas appliqué à la description : beaucoup
  d'offres de stage mentionnent l'alternance en bas d'annonce.
- `filtrage.exiger_marqueur_stage` — une offre doit se **présenter** comme un
  stage (`marqueurs_stage` : stage, stagiaire, internship, trainee). Sans ce
  garde-fou, le moteur d'Adzuna, qui classe par pertinence et non par ET, fait
  passer des postes d'ingénieur en CDI : 14 sur 72 offres lors du premier run
  réel. Le marqueur dans l'intitulé fait foi ; trouvé seulement dans le corps de
  l'annonce, il ne suffit pas si le contrat est explicitement permanent.
- `filtrage.mots_cles_scores` — poids par mot-clé du domaine. Un mot trouvé dans
  l'intitulé vaut son poids plein, dans la description la moitié. En dessous de
  `score_min`, l'offre est écartée.

C'est le réglage qu'il faut ajuster après quelques jours : si la feuille se
remplit de bruit, monter `score_min` ; si elle reste vide, le baisser ou ajouter
des mots-clés.

Cas particulier des durées en intervalle : « 3 à 6 mois » est compté comme
**6 mois**. Une durée est souvent négociable, et le but est de ne pas rater une
offre — la colonne `durée_semaines` de la feuille permet de vérifier.

Une durée lue au-delà d'un an est ignorée plutôt que retenue : elle vient
toujours d'un chiffre qui parlait d'autre chose (« 5 ans d'expérience » donnait
des stages de 260 semaines). L'offre est alors marquée `?`, ce qui est une
information honnête, là où une durée inventée s'afficherait comme un fait.

## La feuille

Onglet **Offres**, une ligne par offre :

`id · date_ajout · date_publication · source · intitulé · entreprise · lieu ·
contrat · durée_semaines · score · mots_clés · url · statut · date_candidature · notes`

Les trois dernières colonnes sont l'espace de travail : **le pipeline n'écrit
jamais dessus**, il ne fait qu'ajouter des lignes. La feuille sert aussi de
mémoire — c'est la colonne `id` qui empêche de réécrire une offre déjà vue, donc
supprimer une ligne la fera revenir au prochain run.

Onglet **Runs** : une ligne par source et par exécution (récupérées, retenues,
ajoutées, erreur éventuelle). C'est là qu'on voit tout de suite qu'une source
est tombée en panne alors que l'autre continue de livrer.

## Fonctionnement

```
config.yaml + secrets
        │
        ├── France Travail ──┐
        │                    ├── normalisation (models.Offer)
        └── Adzuna ──────────┘
                             │
        filtrage (filters.py) : exclusions → est-ce un stage ? → durée → score
                             │
                     dédoublonnage : ids de la feuille + clé intitulé|entreprise
                             │
                     append_rows → Google Sheet
```

Si une source tombe, l'autre livre quand même (l'erreur est écrite dans l'onglet
`Runs`). Si la **lecture** de la feuille échoue, le run s'arrête sans rien
écrire : mieux vaut zéro ligne qu'une série de doublons.

## Tests

```bash
python -m pytest
```

Les tests couvrent le filtrage (durées, exclusions, score) et la normalisation
des deux APIs sur des fixtures JSON — aucun appel réseau. C'est ce qui casse en
premier quand une API change de format.

## Limites connues

- **GitHub désactive un workflow planifié après 60 jours sans commit** sur le
  dépôt. Un commit de temps en temps (ou un `Run workflow` manuel) le réactive.
- Le cron GitHub est en **UTC** et peut se déclencher avec quelques minutes de
  retard aux heures de pointe.
- Le quota gratuit Adzuna est limité : `adzuna.max_pages` et le nombre de
  mots-clés déterminent le nombre d'appels (environ 10 par run avec la config
  fournie).
- Les offres publiées uniquement sur des sites qui n'alimentent ni France Travail
  ni Adzuna (LinkedIn, JobTeaser d'école) ne remontent pas ici.
