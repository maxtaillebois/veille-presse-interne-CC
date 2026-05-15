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
3. Lire `pdfs_extracted.json`. Pour chaque article, produire un objet JSON :
   - `media`, `titre`, `date_publication` (AAAA-MM-JJ si possible)
   - `resume` : 3 à 5 phrases, factuel, **sans glose**
   - `mots_cles_trouves` : sous-ensemble réel de la liste des 4 mots-clés
   - `contexte_citations` : liste de `{mot_cle, phrase}`
   - `file_name` : recopier le `file_name` de l'entrée correspondante
   Écarter tout PDF ne contenant **aucun** des 4 mots-clés. Écrire la liste dans
   `analyses.json`.
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

1. Identifier les articles sélectionnés + leur `Nom fichier PDF` (col. 8 du Sheet).
2. `python3 lance_veille_local.py fetch --names "<fichiers, séparés par des virgules>"`
   → re-télécharge les PDF dans `./pdfs/` (ne pas supposer qu'ils y sont déjà).
3. Fusionner les PDF dans l'ordre du Sheet en un seul (`pypdf`).
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

## Référentiel (aucun secret ici — voir variables d'env, SETUP.md)

| Élément | Valeur |
|---|---|
| Google Sheet pivot | `1t5e1hJ482g-wl6gHeoR0J3yTR-pVOgc9z0rHGREGMN0` — onglet « Veille Procivis » |
| Colonnes (10) | Semaine, Média, Titre, Date publication, Résumé, Mots-clés trouvés, Contexte citations, Nom fichier PDF, *(col. 9 vide — ex-Drive, abandonnée)*, Sélectionné |
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
