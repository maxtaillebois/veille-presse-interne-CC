# Veille Presse Procivis — routine Claude Code

Ce dépôt remplace l'automatisation n8n (4 workflows) de la veille presse interne du
réseau Procivis. Objectif : **zéro n8n**. C'est Claude qui analyse les PDF lui-même
(plus d'appels API en boucle → le rate limit Anthropic 30K tokens/min disparaît).

- `lance_veille_local.py` : boîte à outils I/O (Outlook, Google Sheet, fichiers).
- `SETUP.md` : dépendances + variables d'environnement.
- Mise au point / contexte historique : `../MIGRATION_N8N_VERS_CLAUDE_CODE.md`.

> ⚠️ **En cas de divergence avec `MIGRATION_N8N_VERS_CLAUDE_CODE.md`, CE fichier fait
> foi.** Le brief dit « CC = Aurélie seule » : **c'est faux**. Préférence confirmée
> par Maxime → **CC = Maxime + Aurélie** (cf. « Format du mail »).

## Périmètre (ne pas changer)

- Mots-clés surveillés : **Procivis**, **Immo de France**, **Maisons d'en France**, **Yannick Borde**.
- Cycle : collecte vendredi matin → sélection vendredi (manuelle, Maxime) → envoi → purge.
- Environnement de routine **éphémère** : rien ne survit entre deux runs hors git.
  Ce qui doit traverser COLLECTE → ENVOI est dans le **Google Sheet** ; les **PDF**
  sont re-téléchargés depuis Outlook à l'ENVOI (`fetch`).

## Routine COLLECTE (cron, vendredi matin, Europe/Paris)

1. Dépendances : cf. `SETUP.md` (script de setup d'environnement).
2. `python3 lance_veille_local.py extract --days 7`
   → `pdfs_extracted.json` + PDF dans `./pdfs/`.
3. Lire `pdfs_extracted.json`. **Un PDF n'est pas forcément un seul article** : les
   « revues de presse » (mails « RETOMBEES », « PANORAMA DE PRESSE ») compilent
   souvent plusieurs coupures dans un même PDF (une page « Sommaire » listant
   plusieurs titres + numéros de page en tête de fichier, ou plusieurs blocs
   `PUBLICATION:` / `COUNTRY:France...PAGE(S):` qui se succèdent). Dans ce cas,
   traiter **chaque coupure comme un article séparé**, pas le PDF dans son
   ensemble. Pour chaque article, produire un objet JSON :
   - `media`, `titre`, `date_publication` (AAAA-MM-JJ si possible)
   - `resume` : 3 à 5 phrases, factuel, **sans glose**
   - `mots_cles_trouves` : sous-ensemble réel de la liste des 4 mots-clés
   - `contexte_citations` : liste de `{mot_cle, phrase}`
   - `file_name` : recopier le `file_name` de l'entrée correspondante
   - `pages` : **si et seulement si** ce PDF compile plusieurs coupures, la plage de
     pages de CETTE coupure au sein du fichier (1-indexée, incluse — ex. `"4-5"`
     ou `"14"` pour une page seule). Déterminer les bornes en repérant, dans le
     texte extrait, les sauts de page (`\f` produits par `pdftotext -layout` —
     un saut = une page physique du PDF) et en associant chaque bloc `PUBLICATION:`
     / `COUNTRY:France...` à sa page de départ (recouper avec les numéros de la
     page « Sommaire » si présente : ils ont souvent un décalage constant avec
     l'index physique réel — **vérifier en comptant les `\f`, ne pas recopier le
     numéro du Sommaire tel quel**). Laisser `pages` absent/vide pour un PDF à
     article unique.
   Écarter tout PDF (ou toute coupure) ne contenant **aucun** des 4 mots-clés.
   Écrire la liste dans `analyses.json`.
4. `python3 lance_veille_local.py write --analyses analyses.json`
5. Mail de notification à `maxime.taillebois@procivis.fr`, objet « La veille presse
   est prête ! », corps court : nombre d'articles + lien
   `https://maxtaillebois.github.io/procivis-veille-interne/`.
6. En cas d'échec d'une étape : **ne rien écrire dans le Sheet**, prévenir Maxime
   par mail (étape en échec).

Ne **jamais** déclencher l'envoi final à Stéphanie depuis la COLLECTE.

## ENVOI (v1 — déclenché manuellement par Maxime après sélection)

Entrée : articles dont la colonne « Sélectionné » du Sheet vaut `true` (ou récap
fourni par Maxime).

1. Identifier les articles sélectionnés + leur `Nom fichier PDF` (col. 8) et leur
   `Pages PDF` (col. 9, vide si le PDF ne contient qu'un seul article).
2. `python3 lance_veille_local.py fetch --names "<fichiers, séparés par des virgules>"`
   → re-télécharge les PDF dans `./pdfs/` (ne pas supposer qu'ils y sont déjà). Le
   mode `fetch` télécharge par nom de fichier seulement (suffixe `@pages` toléré et
   ignoré) — dédoublonne naturellement si plusieurs coupures viennent du même PDF.
3. `envoi` fusionne automatiquement dans l'ordre voulu et **découpe chaque
   article sur ses seules pages** (`--names` accepte `fichier.pdf@debut-fin`,
   ex. `clips-....pdf@2-3,clips-....pdf@4-5`) — indispensable dès qu'au moins deux
   coupures sélectionnées partagent le même `Nom fichier PDF` (revue de presse
   compilée), sinon le PDF envoyé répète le fichier entier au lieu de chaque
   coupure et le mail affiche le même titre plusieurs fois (incident du
   2026-07-31, corrigé).
4. Rédiger le mail (voir « Format du mail »), **présenter le brouillon à Maxime
   pour validation**, puis envoyer via Microsoft Graph :
   - TO : `stephanie@papiersdesoi.fr`
   - **CC : `maxime.taillebois@procivis.fr, aurelie.hennetier@procivis.fr`**
   - PJ : le PDF fusionné
5. Après envoi réussi **uniquement** : `python3 lance_veille_local.py purge`
   (vide les lignes du Sheet — en-tête conservé — et supprime les fichiers de travail).
6. Confirmer à Maxime (récap des articles envoyés).

### Format du mail (préférences validées — ne pas redemander)

- Ton : **tutoiement** — « Hello Stéphanie » / « Bien à toi » (prestataire habituelle).
- Liste des articles dans l'ordre **`Média | Titre | Date`** (pas Média | Date | Titre).
- **Pas de glose** entre parenthèses derrière le titre.
- **CC = Maxime + Aurélie** (Maxime EST en CC — corrige l'erreur du brief de migration).

## Purge

Intégrée à l'ENVOI (étape 5), une fois le mail parti. Filet de sécurité optionnel :
petite routine cron du lundi qui lance `purge` si la semaine n'a pas été envoyée.

## Pièges connus

- **Rate limit Anthropic** : disparaît (Claude lit les PDF lui-même). Ne pas
  réintroduire d'appels API en boucle.
- **PDF scannés** : OCR `tesseract` (pack `fra`) ; si indisponible, lecture native
  du PDF par Claude.
- **Doublons** : mails de transfert → même PDF deux fois. `extract`/`fetch`
  dédoublonnent par nom de fichier.
- **Fenêtre 7 jours** : `extract --days 7`. Si l'ENVOI est lancé longtemps après la
  collecte, élargir `fetch --days`.
- **Partage du Sheet** : s'il repasse en « Restreint », la page HTML affiche
  « 0 article ». Garder « lien → Lecteur ».
- **PDF « revue de presse » compilant plusieurs coupures** : très fréquent (voir
  étape 3 de la COLLECTE). Toujours renseigner `pages` par coupure — sans ça,
  `envoi` ne peut pas distinguer deux articles du même fichier : il répète le
  même titre dans le mail et duplique le fichier entier dans le PDF fusionné au
  lieu d'en extraire chaque coupure (incident du 2026-07-31).
- **Colonne « Pages PDF » : toujours du TEXTE, jamais une date.** En
  `USER_ENTERED`, Sheets interprète « 2-3 » comme une date → gviz renvoie
  `Date(...)` à la page et `get_all_values` renvoie « 02/03/2026 » à
  `select`/`envoi` : plus rien ne matche. `run_write` préfixe donc la plage
  d'une apostrophe (forçage texte, invisible à l'affichage). Si on écrit cette
  colonne à la main dans le Sheet, taper l'apostrophe aussi (`'2-3`). Ne pas
  passer l'append en `RAW` : la colonne J (cases à cocher BOOLEAN) a besoin de
  `USER_ENTERED` pour transformer « false » en vrai booléen.
- **`.github/workflows/envoi.yml`** : GitHub Action `workflow_dispatch`, deux
  chemins. Input `selected_files` **rempli** (bouton de la page, format
  `fichier.pdf@debut-fin,...`) → `select` valide CHAQUE entrée contre le Sheet
  (une inconnue → échec, rien d'écrit, aucun mail), reporte la sélection dans
  la colonne « Sélectionné », puis `envoi --names` expédie dans l'ordre de la
  page. Input **vide** → `envoi` lit la colonne « Sélectionné » du Sheet.
  **La plage `@pages` est obligatoire pour toute coupure d'un PDF compilé** :
  des noms nus sans plage ont causé 3 envois erronés le 2026-07-31 ; c'est le
  garde-fou de `select` qui rend l'input de nouveau utilisable en confiance.
  L'input ne doit JAMAIS être branché sur `envoi --names` sans `select` devant.
- **Un correctif non mergé ne protège de rien** : le workflow fait un
  `checkout` de `main`. Tant qu'une correction dort dans une branche/PR, le
  bouton continue d'exécuter l'ancien code (constaté le 2026-07-31 : même bug
  reproduit à l'identique après un « correctif » resté en PR draft).
- **Garde-fou `envoi`** : si une sélection ne correspond à aucune ligne du
  Sheet, le script sort en erreur au lieu d'envoyer un mail incomplet.
  `envoi --dry-run` affiche les articles retenus sans rien envoyer ni purger —
  à utiliser pour vérifier avant tout envoi réel.

## Référentiel (aucun secret ici — voir variables d'env, SETUP.md)

| Élément | Valeur |
|---|---|
| Google Sheet pivot | `1t5e1hJ482g-wl6gHeoR0J3yTR-pVOgc9z0rHGREGMN0` — onglet « Veille Procivis » |
| Colonnes (10) | Semaine, Média, Titre, Date publication, Résumé, Mots-clés trouvés, Contexte citations, Nom fichier PDF, **Pages PDF** *(ex-« ID fichier Drive », abandonnée puis réutilisée — plage 1-indexée « 4-5 », vide si PDF à article unique)*, Sélectionné |
| Page de sélection | `https://maxtaillebois.github.io/procivis-veille-interne/` (repo `maxtaillebois/procivis-veille-interne`) |
| Boîte Outlook (lecture + envoi) | `maxime.taillebois@procivis.fr` |
| Destinataire mail final | TO `stephanie@papiersdesoi.fr` — **CC `maxime.taillebois@procivis.fr, aurelie.hennetier@procivis.fr`** |
| Filtre sujets Outlook | « retombees », « retombée », « PANORAMA DE PRESSE » — < 7 j, avec PJ |
| n8n | **Hors sujet — plus aucun n8n dans ce processus depuis longtemps.** Tout passe par Claude Code + la GitHub Action. Anciens ID (archive) : W1 `KgSSxM4fCLnvBVTy` · W2 `LgqS9YPx77vPkoI1` · W3 `C7yzQTLVcIfl3aqG` · W4 `al4Sh59yAfsxGRAn`. **Ne pas invoquer n8n comme cause d'un incident** (piste explorée à tort le 2026-07-31). |

## Page HTML de sélection (autre repo — architecture v2 du 2026-07-31)

`maxtaillebois/procivis-veille-interne/index.html`. `DEFAULT_CC_EMAILS` = Maxime +
Aurélie : **correct, ne pas le réduire à Aurélie**. Le bouton POST vers un **Worker
Cloudflare** (`veille-dispatch.maxime-taillebois.workers.dev`) qui relaie vers l'API
GitHub `workflow_dispatch` — le token GitHub vit dans le Worker, pas dans la page.

**Architecture v2 (remplace le constat d'origine « la page ne coche rien »)** : la
page n'écrit toujours pas directement dans le Sheet (gviz = lecture seule, aucune
credential côté client — c'est voulu). C'est le **workflow** qui écrit : au clic sur
« Envoyer », la page transmet `fichier.pdf@debut-fin,...` (colonne « Pages PDF » lue
via gviz) ; le job exécute `select` (validation stricte + report TRUE/FALSE dans la
colonne « Sélectionné ») puis `envoi --names`. Les coches de la page s'initialisent
depuis la colonne « Sélectionné » au chargement. Le Sheet reste le pivot ; Maxime n'a
plus à le toucher.

La page v2 (envoie `@pages` + initialise les coches depuis le Sheet) est **déployée
sur `main` du repo de la page depuis le 2026-07-31** (commit `a8d7f84`) et vérifiée
en ligne : sur une ligne de test réelle (Sélectionné = TRUE, Pages PDF = « 2-3 »),
la coche s'initialise depuis le Sheet et le payload construit vaut bien
`fichier.pdf@2-3`.

## Journal — incident du 2026-07-31 (à lire avant tout dépannage ENVOI)

**Résumé** : 3 mails erronés envoyés à Stéphanie (07h37, 10h13, 10h21) avant un 4ᵉ
correct (10h49). Trois causes distinctes empilées, corrigées une par une.

### Ce qui a été envoyé

| Heure | Contenu | Cause |
|---|---|---|
| 07h37 | Même titre ×3 (« Le Clos Vitalis »), PDF 42 p. | Identification par nom de fichier seul |
| 10h13 | Idem, à l'identique | Le correctif dormait en **PR draft non mergée** |
| 10h21 | Puces vides « \| clips-….pdf \| », PDF 42 p. | Noms nus vs clé `(fichier, pages)` → aucune correspondance |
| 10h49 | ✅ 3 articles distincts, PDF **5 pages**, 943 Ko | — |

### Les trois causes

1. **Coupures indistinguables.** `run_envoi` indexait les métadonnées par nom de
   fichier. 7 des 9 articles de la semaine venaient du seul
   `clips-20260728075205.pdf` → la dernière ligne portant ce nom écrasait les autres,
   et le fichier entier était fusionné une fois par sélection.
   → **Corrigé** : clé `(nom de fichier, plage de pages)` + découpe pypdf par plage.
2. **Correctif non déployé.** Le workflow `checkout` `main` ; la PR est restée en
   draft. Le bouton a réexécuté l'ancien code à l'identique.
   → **Leçon** : vérifier que le correctif est **sur `main`**, pas seulement poussé.
3. **Sélection transmise à la main.** L'input `selected_files` faisait reposer la
   justesse de l'envoi sur une saisie exacte (format `@pages` inconnu de la page).
   → **Corrigé** : la sélection est lue dans le Sheet ; l'input est conservé mais
   **ignoré** (sans lui : HTTP 422, cf. Pièges connus).

### Fausses pistes (ne pas y retourner)

- **n8n** : exploré à tort, il n'y en a plus depuis longtemps dans ce processus.
- **Purge intempestive** : la purge n'a fait que son travail (fin d'ENVOI). Le Sheet
  paraissait « vidé tout seul » parce qu'un ENVOI raté allait au bout et purgeait.

### Garde-fous ajoutés le 2026-07-31

- `envoi` **sort en erreur** si une sélection ne correspond à aucune ligne du Sheet
  (au lieu d'envoyer un mail à trous).
- `envoi --dry-run` : va jusqu'au PDF fusionné et au corps du mail, **sans** envoyer
  ni purger ; affiche nombre de pages et taille de la PJ. **À lancer systématiquement
  avant tout envoi réel.**
- `cffi` ajouté aux dépendances (sinon `import gspread` plante en environnement neuf).
- Colonne J passée en **vraies cases à cocher** (validation BOOLEAN, lignes de données
  uniquement — ne pas l'étendre à 1000 lignes vides, ça pollue le Sheet et la page).

### État au 2026-07-31 en fin de journée

Semaine S31-2026 envoyée et purgée (Sheet vide, en-tête conservé). Correctifs mergés
sur `main` (PR #1 à #4), puis **architecture v2 du flux page → Sheet** (mode `select`,
workflow à deux chemins, nouvelle page — voir section « Page HTML de sélection »).
Testé de bout en bout sur données réelles : select coche/décoche juste, rejette les
noms nus sans rien écrire, `envoi --names` respecte l'ordre de la page, PDF découpé
conforme.

**Déploiement terminé le 2026-07-31 (session locale)** : page v2 poussée sur `main`
de `procivis-veille-interne` (commit `a8d7f84`), GitHub Pages vérifié en ligne
(coche initialisée depuis le Sheet + payload `@pages` corrects sur ligne de test,
ligne supprimée ensuite). Au passage, bug latent corrigé dans `run_write` : la plage
« Pages PDF » était écrite sans forçage texte et serait devenue une date au premier
vendredi (cf. « Pièges connus »). Le premier cycle réel complet (collecte → page →
bouton → mail) reste à observer au prochain vendredi de collecte.
