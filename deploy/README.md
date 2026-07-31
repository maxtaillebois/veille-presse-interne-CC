# À déployer : nouvelle page de sélection (v2 du 2026-07-31)

`index.html` de ce dossier = la version à installer sur `main` du repo
**`maxtaillebois/procivis-veille-interne`** (GitHub Pages), en remplacement
complet du `index.html` actuel.

Ce qu'elle change (cf. CLAUDE.md, section « Page HTML de sélection ») :
- le bouton « Envoyer » transmet `fichier.pdf@debut-fin,...` (colonne
  « Pages PDF ») au lieu de noms de fichiers nus ;
- les coches s'initialisent depuis la colonne « Sélectionné » du Sheet.

Déploiement en 30 secondes :
https://github.com/maxtaillebois/procivis-veille-interne/edit/main/index.html
→ tout remplacer par le contenu de `deploy/index.html` → Commit changes.

Une fois déployée et vérifiée, ce dossier `deploy/` peut être supprimé.
