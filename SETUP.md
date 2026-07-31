# SETUP — environnement d'exécution de la routine

Ce dépôt est destiné à tourner comme **routine Claude Code** (agent planifié, cron
hebdomadaire). L'environnement d'exécution est **éphémère** : chaque run repart d'un
clone vierge du dépôt. Rien ne persiste entre deux runs sauf ce qui est commité dans
git. Conséquence directe sur l'architecture : les PDF de la COLLECTE (vendredi) ne
sont plus là au moment de l'ENVOI → l'ENVOI les **re-télécharge** depuis Outlook
(`fetch`). Ne pas chercher à faire survivre `./pdfs/` entre deux runs.

> Doc de référence (la syntaxe de config peut évoluer) :
> https://code.claude.com/docs/en/routines
> https://code.claude.com/docs/en/claude-code-on-the-web

---

## 1. Dépendances système (script de setup d'environnement)

À mettre dans le **script de setup de l'environnement** de la routine (exécuté une
fois puis mis en cache — il ne doit donc rien faire de dynamique) :

```bash
#!/bin/bash
set -e
# Les PPA tierces pré-installées dans l'image (deadsnakes, ondrej/php) sont
# bloquées par le réseau de la routine (403 Forbidden) et font échouer
# apt-get update. On les retire : nos paquets sont dans les dépôts Ubuntu
# officiels, qui fonctionnent.
rm -f /etc/apt/sources.list.d/*deadsnakes* /etc/apt/sources.list.d/*ondrej*
apt-get update
apt-get install -y poppler-utils tesseract-ocr tesseract-ocr-fra
# Le script de setup tourne sans le dépôt cloné → on installe les paquets
# par leur nom, pas via -r requirements.txt (le fichier n'est pas là).
# cffi : requis par cryptography (dépendance de google-auth) — sans lui,
# l'import de gspread plante avec « ModuleNotFoundError: _cffi_backend »
# (repéré en routine le 2026-07-31, corrigé à la main pour ce run).
pip install gspread google-auth requests pypdf cffi
```

> `requirements.txt` reste la référence (versions, install locale). Le script de
> setup, lui, liste les paquets en clair car il s'exécute hors du dépôt.

- `poppler-utils` → `pdftotext`, `pdftoppm`
- `tesseract-ocr` + `tesseract-ocr-fra` → fallback OCR pour les PDF scannés
- Filet de sécurité : si `tesseract` est indisponible, Claude peut lire le PDF
  nativement (vision) au lieu de l'OCR.

L'accès réseau aux miroirs de paquets et à PyPI est couvert par le niveau d'accès
réseau **Trusted** (par défaut).

## 2. Dépendances Python

`requirements.txt` : `gspread`, `google-auth`, `requests`, `pypdf` (fusion PDF côté
ENVOI). Installé par le script de setup ci-dessus.

## 3. Variables d'environnement (secrets de la routine)

À renseigner dans la **config d'environnement** de la routine (format `.env`, une
ligne `CLE=valeur`, **sans guillemets**). À ne JAMAIS committer.

| Variable | Rôle |
|---|---|
| `OUTLOOK_TENANT` | Tenant Microsoft Graph |
| `OUTLOOK_CLIENT_ID` | Client ID de l'app Azure |
| `OUTLOOK_CLIENT_SECRET` | Client secret Azure — **à régénérer** (a traîné en clair) |
| `OUTLOOK_USER` | Boîte de lecture/envoi (`maxime.taillebois@procivis.fr`) |
| `GOOGLE_SA_JSON` | **Contenu** de la clé de service Google (JSON sur une ligne) ou chemin vers le fichier |
| `SPREADSHEET_ID` | *(optionnel)* surcharge l'ID du Sheet |
| `SHEET_NAME` | *(optionnel)* surcharge le nom de l'onglet (défaut « Veille Procivis ») |

Pour `GOOGLE_SA_JSON` : le script accepte soit le **JSON complet** collé dans la
variable (recommandé en routine), soit un **chemin** vers un fichier `.json`. Il
détecte automatiquement (commence par `{` → contenu JSON, sinon → chemin).

> Pas de coffre-fort de secrets dédié côté routines (à ce jour) : les variables sont
> visibles de qui peut éditer l'environnement. Acceptable pour cette automatisation
> interne. Régénérer le client secret Outlook côté Azure après la migration.

## 4. Accès réseau

> ⚠️ **Vérifié en conditions réelles** : le niveau réseau par défaut **bloque
> `graph.microsoft.com`** (« Host not in allowlist »). Il faut une politique
> **Custom** déclarant explicitement les hôtes.

Régler l'accès réseau de l'environnement sur **Custom** et autoriser :

```
login.microsoftonline.com
graph.microsoft.com
oauth2.googleapis.com
sheets.googleapis.com
www.googleapis.com
```

(ou `*.googleapis.com` si les jokers sont acceptés). `login.microsoftonline.com`
est joignable par défaut mais à inclure par clarté ; `graph.microsoft.com` sert au
`extract` ET au mail de notification (`sendMail`) ; les hôtes `googleapis.com`
servent au `write` (gspread + auth compte de service).

## 5. Accès des comptes (à vérifier une fois)

- Le compte de service Google doit être **Éditeur** du Sheet pivot (partage
  explicite — un partage « lien » ne suffit pas pour un compte de service).
- Le Sheet doit rester en **« Tout le monde avec le lien → Lecteur »** pour que la
  page HTML de sélection le lise (API gviz publique).
- L'app Azure (client ID) doit conserver les permissions Microsoft Graph d'envoi et
  de lecture sur la boîte `maxime.taillebois@procivis.fr`.

## 6. Lancer en local (mise au point)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OUTLOOK_TENANT=... OUTLOOK_CLIENT_ID=... OUTLOOK_CLIENT_SECRET=... \
       OUTLOOK_USER=maxime.taillebois@procivis.fr
export GOOGLE_SA_JSON="$(cat '../CLE JSON/veille-presse-dbaf377bdd50.json')"

python3 lance_veille_local.py extract --days 7 --limit 2   # test collecte
python3 lance_veille_local.py write --analyses analyses.json --dry-run
python3 lance_veille_local.py fetch --names "a.pdf,b.pdf"   # test ENVOI
python3 lance_veille_local.py purge --dry-run               # test purge
```
