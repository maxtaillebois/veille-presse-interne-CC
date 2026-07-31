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
- **`.github/workflows/envoi.yml`** : GitHub Action `workflow_dispatch` sans
  aucun input — elle exécute `envoi` tout court, qui lit la colonne
  « Sélectionné » du Sheet. **Ne jamais réintroduire d'input texte libre du type
  `selected_files`** : c'est ce qui a provoqué 3 envois erronés à Stéphanie le
  2026-07-31 (noms de fichiers sans plage de pages → aucune correspondance dans
  le Sheet, mail avec lignes vides et PDF fusionné faux).
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
| n8n à désactiver après bascule | W1 `KgSSxM4fCLnvBVTy` · W2 `LgqS9YPx77vPkoI1` · W3 `C7yzQTLVcIfl3aqG` · W4 `al4Sh59yAfsxGRAn` |

## Page HTML de sélection — à corriger (autre repo)

Dans `maxtaillebois/procivis-veille-interne/index.html`, la `CONFIG` actuelle a
`DEFAULT_CC_EMAILS` = Maxime + Aurélie : **c'est correct, ne pas le réduire à
Aurélie**. Le `POST` du bouton vise encore le webhook n8n : en v1, la page reste un
outil de coche (la sélection est relue depuis le Sheet) ; recâbler le bouton vers la
routine ENVOI relève de la v2 (cf. brief, section 6 — réserves CORS/token à lever).
