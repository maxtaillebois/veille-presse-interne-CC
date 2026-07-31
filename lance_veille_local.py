#!/usr/bin/env python3
"""
Veille Presse Procivis — boîte à outils de la routine Claude Code.

L'analyse des articles n'est PAS faite par appel API : c'est Claude (la routine)
qui lit le JSON produit par ce script et rédige analyses.json lui-même. Le script
ne fait que les entrées/sorties (Outlook, Google Sheet, fichiers).

Modes :

  extract  Outlook → PDF → texte → pdfs_extracted.json (+ PDF dans ./pdfs/)
  write    analyses.json → une ligne par article dans le Google Sheet
  fetch    re-télécharge des PDF par nom depuis Outlook → ./pdfs/   (pour l'ENVOI)
  purge    vide les lignes du Sheet (en-tête conservé) + supprime les fichiers de travail

Secrets : tout passe par variables d'environnement (voir SETUP.md). Aucun secret
n'est codé en dur ni commité.
"""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import gspread
from google.oauth2.service_account import Credentials


# ─── Configuration (variables d'environnement) ──────────────────────────────

def require_env(name):
    val = os.environ.get(name)
    if not val:
        log(f"ERREUR : variable d'environnement manquante : {name}", "red")
        log("Voir SETUP.md pour la liste des variables requises.", "yellow")
        sys.exit(2)
    return val


# Non secret — surchargeable via env, valeurs par défaut sûres.
SPREADSHEET_ID = os.environ.get(
    "SPREADSHEET_ID", "1t5e1hJ482g-wl6gHeoR0J3yTR-pVOgc9z0rHGREGMN0"
)
SHEET_NAME = os.environ.get("SHEET_NAME", "Veille Procivis")

SUBJECT_PATTERNS = ["retombees", "retombée", "PANORAMA DE PRESSE"]


# ─── Logs ───────────────────────────────────────────────────────────────────

def log(msg, color=None):
    codes = {"green": 32, "red": 31, "yellow": 33, "blue": 34, "gray": 90, "bold": 1}
    prefix = datetime.now().strftime("[%H:%M:%S]")
    if color:
        msg = f"\033[{codes[color]}m{msg}\033[0m"
    print(f"\033[90m{prefix}\033[0m {msg}", flush=True)


# ─── Outlook (Microsoft Graph) ──────────────────────────────────────────────

def outlook_get_token():
    log("Auth Microsoft Graph…")
    r = requests.post(
        f"https://login.microsoftonline.com/{require_env('OUTLOOK_TENANT')}/oauth2/v2.0/token",
        data={
            "client_id": require_env("OUTLOOK_CLIENT_ID"),
            "client_secret": require_env("OUTLOOK_CLIENT_SECRET"),
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def outlook_search_messages(token, days=7):
    user = require_env("OUTLOOK_USER")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    headers = {"Authorization": f"Bearer {token}"}
    all_msgs = {}

    for pattern in SUBJECT_PATTERNS:
        log(f"  Recherche sujet « {pattern} »…")
        url = (
            f"https://graph.microsoft.com/v1.0/users/{user}/messages"
            f'?$search="{pattern}"'
            f"&$top=50"
            f"&$select=id,subject,from,receivedDateTime,hasAttachments"
        )
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        for m in r.json().get("value", []):
            if m["receivedDateTime"] < since:
                continue
            if not m.get("hasAttachments"):
                continue
            all_msgs[m["id"]] = m

    log(f"  → {len(all_msgs)} mails trouvés (avec PJ, < {days} jours)", "green")
    return list(all_msgs.values())


def outlook_get_pdf_attachments(token, message_id):
    user = require_env("OUTLOOK_USER")
    url = (
        f"https://graph.microsoft.com/v1.0/users/{user}"
        f"/messages/{message_id}/attachments"
    )
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    pdfs = []
    for att in r.json().get("value", []):
        if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
            continue
        name = att.get("name", "")
        ctype = att.get("contentType", "")
        if not (name.lower().endswith(".pdf") or ctype == "application/pdf"):
            continue
        pdfs.append({"name": name, "content_bytes": att["contentBytes"]})
    return pdfs


# ─── Fichiers PDF ───────────────────────────────────────────────────────────

def safe_pdf_name(name):
    """Nom de fichier sûr (pas de traversée de chemin), extension .pdf."""
    base = Path(name).name.strip() or "document.pdf"
    base = re.sub(r"[^\w.\- ]+", "_", base)
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base


def split_name_pages(token):
    """« fichier.pdf » ou « fichier.pdf@debut-fin » (plage 1-indexée, incluse) →
    (nom_fichier, pages_str). pages_str vide = tout le fichier (PDF à un seul
    article). Un même PDF « revue de presse » compilant plusieurs coupures aura
    plusieurs lignes de Sheet partageant le même nom de fichier mais des pages
    différentes : c'est cette plage qui les distingue, pas le seul nom."""
    fname, _, pages = token.partition("@")
    return fname.strip(), pages.strip()


def page_range_to_slice(pages_str):
    """« 5 » ou « 5-6 » (1-indexé, inclus) → tuple (debut0, fin0) pour
    PdfWriter.append(..., pages=...) (0-indexé, fin exclue). None si vide
    (= tout le fichier)."""
    pages_str = (pages_str or "").strip()
    if not pages_str:
        return None
    if "-" in pages_str:
        a, b = pages_str.split("-", 1)
        start, end = int(a), int(b)
    else:
        start = end = int(pages_str)
    return (start - 1, end)


def save_pdf(pdf_dir, name, content_b64):
    pdf_dir.mkdir(parents=True, exist_ok=True)
    path = pdf_dir / safe_pdf_name(name)
    path.write_bytes(base64.b64decode(content_b64))
    return path


def extract_text_from_pdf(pdf_path, use_ocr=True):
    """pdftotext d'abord, OCR tesseract en fallback si le texte est trop court."""
    r = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, text=True, timeout=60,
    )
    text = r.stdout.strip()
    if len(text) > 200 or not use_ocr:
        return text
    log("    → pdftotext insuffisant, OCR avec tesseract…", "yellow")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(
            ["pdftoppm", "-r", "200", str(pdf_path), f"{td}/page"],
            capture_output=True, timeout=180,
        )
        ocr_text = []
        for img in sorted(Path(td).glob("page-*.ppm")):
            r = subprocess.run(
                ["tesseract", str(img), "-", "-l", "fra", "--psm", "6"],
                capture_output=True, text=True, timeout=180,
            )
            ocr_text.append(r.stdout)
        return "\n".join(ocr_text).strip()


# ─── Google Sheet ───────────────────────────────────────────────────────────

def open_sheet():
    raw = require_env("GOOGLE_SA_JSON")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    if raw.lstrip().startswith("{"):
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(raw, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


def get_week_label(d=None):
    d = d or datetime.now()
    iso = d.isocalendar()
    return f"S{iso.week:02d}-{iso.year}"


# ─── Mode EXTRACT ───────────────────────────────────────────────────────────

def run_extract(args):
    log("=== Mode EXTRACT ===", "bold")
    pdf_dir = Path(args.pdf_dir)
    token = outlook_get_token()
    msgs = outlook_search_messages(token, days=args.days)
    if not msgs:
        log("Aucun mail trouvé. Fin.", "yellow")
        Path(args.out).write_text("[]", encoding="utf-8")
        return

    log("Téléchargement des PJ PDF…")
    seen = set()
    extracted = []
    count = 0
    for m in msgs:
        for pdf in outlook_get_pdf_attachments(token, m["id"]):
            fname = safe_pdf_name(pdf["name"])
            if fname in seen:                       # dédoublonnage par nom
                log(f"  (doublon ignoré : {fname})", "gray")
                continue
            seen.add(fname)
            count += 1
            if args.limit and count > args.limit:
                log(f"  → arrêt à {args.limit} PDF (--limit)", "yellow")
                break
            log(f"  [{count}] {fname}")
            try:
                path = save_pdf(pdf_dir, pdf["name"], pdf["content_bytes"])
                text = extract_text_from_pdf(path, use_ocr=not args.no_ocr)
                log(f"    {len(text)} chars (~{len(text)//4} tokens) → {path}")
                extracted.append({
                    "name": pdf["name"],
                    "file_name": fname,
                    "subject": m["subject"],
                    "from": m["from"]["emailAddress"]["address"],
                    "received": m["receivedDateTime"],
                    "text": text,
                })
            except Exception as e:
                log(f"    Erreur : {e}", "red")
                extracted.append({"name": pdf["name"], "file_name": fname,
                                   "error": str(e)})
        else:
            continue
        break

    Path(args.out).write_text(
        json.dumps(extracted, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"→ {len(extracted)} PDF écrits dans {args.out} (originaux dans {pdf_dir}/)", "green")
    log("", None)
    log("Étape suivante : Claude lit ce fichier et produit analyses.json,", "blue")
    log("puis : python3 lance_veille_local.py write --analyses analyses.json", "blue")


# ─── Mode WRITE ─────────────────────────────────────────────────────────────

def run_write(args):
    log("=== Mode WRITE ===", "bold")
    analyses = json.loads(Path(args.analyses).read_text(encoding="utf-8"))
    if not isinstance(analyses, list):
        log("ERREUR : analyses.json doit être une liste JSON.", "red")
        sys.exit(1)

    if args.dry_run:
        log(f"DRY RUN — {len(analyses)} ligne(s) seraient écrites.", "yellow")
        for a in analyses:
            log(f"  • {a.get('media','?')} | {a.get('titre','?')[:60]} | "
                f"{a.get('date_publication','?')}")
        return

    ws = open_sheet()
    log(f"Connecté au Sheet « {SHEET_NAME} »", "green")
    week_label = get_week_label()
    rows = []
    for a in analyses:
        contexte_str = json.dumps(a.get("contexte_citations", []), ensure_ascii=False)
        rows.append([
            week_label,
            a.get("media", ""),
            a.get("titre", ""),
            a.get("date_publication", ""),
            a.get("resume", ""),
            ", ".join(a.get("mots_cles_trouves", [])),
            contexte_str,
            a.get("file_name", ""),
            a.get("pages", ""),       # col 9 — Pages PDF (ex: "2-3" ; vide = tout
                                       # le fichier). Distingue plusieurs coupures
                                       # partageant un même PDF « revue de presse ».
            "false",                  # col 10 — Sélectionné
        ])
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    log(f"→ {len(rows)} ligne(s) ajoutée(s) au Sheet", "green")
    log(f"→ Sheet : https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit", "blue")
    log("→ Sélection : https://maxtaillebois.github.io/procivis-veille-interne/", "blue")


# ─── Mode FETCH (pour l'ENVOI) ──────────────────────────────────────────────

def run_fetch(args):
    """Re-télécharge des PDF par nom depuis Outlook (l'environnement est éphémère :
    les PDF de la COLLECTE du vendredi n'existent plus au moment de l'ENVOI)."""
    log("=== Mode FETCH ===", "bold")
    wanted = []
    if args.names:
        wanted += [n.strip() for n in args.names.split(",") if n.strip()]
    if args.names_file:
        wanted += [l.strip() for l in Path(args.names_file).read_text(
            encoding="utf-8").splitlines() if l.strip()]
    # « fichier.pdf@2-3 » → on télécharge le fichier entier, la plage ne sert
    # qu'à la fusion (mode ENVOI) — voir split_name_pages().
    wanted_safe = {safe_pdf_name(split_name_pages(n)[0]) for n in wanted}
    if not wanted_safe:
        log("ERREUR : aucun nom de fichier fourni (--names ou --names-file).", "red")
        sys.exit(1)
    log(f"{len(wanted_safe)} PDF demandé(s) : {', '.join(sorted(wanted_safe))}")

    pdf_dir = Path(args.pdf_dir)
    token = outlook_get_token()
    msgs = outlook_search_messages(token, days=args.days)

    found = set()
    for m in msgs:
        if wanted_safe <= found:
            break
        for pdf in outlook_get_pdf_attachments(token, m["id"]):
            fname = safe_pdf_name(pdf["name"])
            if fname in wanted_safe and fname not in found:
                path = save_pdf(pdf_dir, pdf["name"], pdf["content_bytes"])
                found.add(fname)
                log(f"  ✓ {path}", "green")

    missing = wanted_safe - found
    if missing:
        log(f"  ✗ Introuvables ({len(missing)}) : {', '.join(sorted(missing))}", "red")
        log("    → élargir --days si l'ENVOI est lancé longtemps après la collecte.",
            "yellow")
        sys.exit(1)
    log(f"→ {len(found)} PDF récupérés dans {pdf_dir}/", "green")


# ─── Mode PURGE ─────────────────────────────────────────────────────────────

def run_purge(args):
    log("=== Mode PURGE ===", "bold")
    if not args.sheet_only:
        for f in ("pdfs_extracted.json", "analyses.json"):
            p = Path(f)
            if p.exists():
                if args.dry_run:
                    log(f"  (dry-run) supprimerait {p}", "yellow")
                else:
                    p.unlink()
                    log(f"  supprimé : {p}")
        pdf_dir = Path(args.pdf_dir)
        if pdf_dir.exists():
            for p in pdf_dir.glob("*.pdf"):
                if args.dry_run:
                    log(f"  (dry-run) supprimerait {p}", "yellow")
                else:
                    p.unlink()
            if not args.dry_run:
                log(f"  vidé : {pdf_dir}/")

    if args.files_only:
        return

    ws = open_sheet()
    values = ws.get_all_values()
    n_data = max(0, len(values) - 1)
    if args.dry_run:
        log(f"  (dry-run) viderait {n_data} ligne(s) du Sheet (en-tête conservé)",
            "yellow")
        return
    if n_data > 0:
        ws.delete_rows(2, len(values))
    log(f"→ {n_data} ligne(s) supprimée(s) du Sheet (en-tête conservé)", "green")


# ─── Mode ENVOI ─────────────────────────────────────────────────────────────

def run_envoi(args):
    """Fetch PDF → merge → mail Stéphanie → purge. Déclenché par le bouton HTML.

    Un même PDF « revue de presse » peut compiler plusieurs coupures (voir
    colonne « Pages PDF »). Les articles sont donc identifiés par le couple
    (nom de fichier, plage de pages), jamais par le seul nom de fichier —
    sinon deux coupures différentes issues du même PDF s'écrasent l'une
    l'autre côté métadonnées, et le PDF fusionné répète le fichier entier
    au lieu de n'en extraire que les pages voulues.
    """
    log("=== Mode ENVOI ===", "bold")

    # 1. Métadonnées du Sheet, indexées par (fichier, pages) — un seul passage.
    ws = open_sheet()
    rows = ws.get_all_values()
    if not rows:
        log("ERREUR : Sheet vide.", "red"); sys.exit(1)
    hdr = [h.strip().lower() for h in rows[0]]
    def _ci(*cands):
        for c in cands:
            try: return hdr.index(c)
            except ValueError: pass
        return -1
    i_media = _ci("média", "media")
    i_titre = _ci("titre")
    i_date  = _ci("date publication", "date_publication")
    i_file  = _ci("nom fichier pdf")
    i_pages = _ci("pages pdf", "id fichier drive")
    i_sel   = _ci("sélectionné")
    if i_file < 0 or i_sel < 0:
        log("ERREUR : colonnes 'Nom fichier PDF' ou 'Sélectionné' introuvables.", "red")
        sys.exit(1)

    meta = {}
    sheet_selection = []
    for r in rows[1:]:
        if i_file >= len(r) or not r[i_file].strip():
            continue
        fname = safe_pdf_name(r[i_file])
        pages = r[i_pages].strip() if 0 <= i_pages < len(r) else ""
        key = (fname, pages)
        meta[key] = {
            "media": r[i_media] if 0 <= i_media < len(r) else "",
            "titre": r[i_titre] if 0 <= i_titre < len(r) else fname,
            "date":  r[i_date]  if 0 <= i_date  < len(r) else "",
        }
        if i_sel < len(r) and r[i_sel].strip().lower() == "true":
            sheet_selection.append(key)

    # 2. Liste des articles à traiter (--names, format "fichier.pdf@debut-fin",
    #    ou lecture Sheet colonne Sélectionné).
    if args.names:
        wanted = []
        for tok in args.names.split(","):
            tok = tok.strip()
            if not tok:
                continue
            fname, pages = split_name_pages(tok)
            wanted.append((safe_pdf_name(fname), pages))
    else:
        wanted = sheet_selection

    if not wanted:
        log("Aucun article sélectionné. Fin.", "yellow"); sys.exit(0)
    log(f"{len(wanted)} article(s) : " +
        ", ".join(f"{f}@{p}" if p else f for f, p in wanted))

    articles_meta = [meta.get(w, {"media": "", "titre": w[0], "date": ""}) for w in wanted]

    # 3. Télécharger chaque PDF source depuis Outlook, une seule fois par nom
    #    (plusieurs articles peuvent partager le même fichier compilé).
    pdf_dir = Path(args.pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    base_files = {fname for fname, _ in wanted}
    token = outlook_get_token()
    msgs  = outlook_search_messages(token, days=args.days)
    found = set()
    for m in msgs:
        if base_files <= found:
            break
        for pdf in outlook_get_pdf_attachments(token, m["id"]):
            fname = safe_pdf_name(pdf["name"])
            if fname in base_files and fname not in found:
                save_pdf(pdf_dir, pdf["name"], pdf["content_bytes"])
                found.add(fname)
                log(f"  ✓ {fname}", "green")
    missing = base_files - found
    if missing:
        log(f"  ✗ PDF introuvables : {', '.join(sorted(missing))}", "red")
        log("    → élargir --days si l'envoi est lancé longtemps après la collecte.", "yellow")
        sys.exit(1)

    # 4. Fusionner : chaque article n'apporte que ses pages (tout le fichier si
    #    aucune plage n'est précisée), dans l'ordre de sélection.
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    for fname, pages in wanted:
        reader = PdfReader(str(pdf_dir / fname))
        page_slice = page_range_to_slice(pages)
        if page_slice is None:
            writer.append(reader)
        else:
            writer.append(reader, pages=page_slice)
    merged_path = pdf_dir / "veille_procivis_envoi.pdf"
    with open(merged_path, "wb") as fh:
        writer.write(fh)
    log(f"→ PDF fusionné ({len(wanted)} articles) : {merged_path}", "green")

    # 5. Envoyer le mail à Stéphanie via Microsoft Graph
    semaine = get_week_label()
    lignes_html = "".join(
        f"<li>{a['media']} | {a['titre']} | {a['date']}</li>"
        for a in articles_meta
    )
    body_html = (
        f"<p>Hello Stéphanie,</p>"
        f"<p>Voici la sélection de la veille presse Procivis pour la semaine {semaine} :</p>"
        f"<ul>{lignes_html}</ul>"
        f"<p>Les articles sont en pièce jointe.</p>"
        f"<p>Bien à toi,<br>Maxime</p>"
    )
    with open(merged_path, "rb") as fh:
        pdf_b64 = base64.b64encode(fh.read()).decode()

    to_email = os.environ.get("ENVOI_TO", "stephanie@papiersdesoi.fr")
    cc_list  = [
        c.strip()
        for c in os.environ.get(
            "ENVOI_CC",
            "maxime.taillebois@procivis.fr,aurelie.hennetier@procivis.fr",
        ).split(",")
        if c.strip()
    ]
    token_graph = outlook_get_token()
    user = require_env("OUTLOOK_USER")
    mail = {
        "message": {
            "subject": f"Veille presse Procivis — {semaine}",
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
            "ccRecipients": [{"emailAddress": {"address": cc}} for cc in cc_list],
            "attachments": [{
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": f"veille_procivis_{semaine}.pdf",
                "contentType": "application/pdf",
                "contentBytes": pdf_b64,
            }],
        },
        "saveToSentItems": True,
    }
    r = requests.post(
        f"https://graph.microsoft.com/v1.0/users/{user}/sendMail",
        headers={"Authorization": f"Bearer {token_graph}", "Content-Type": "application/json"},
        json=mail,
        timeout=60,
    )
    r.raise_for_status()
    log(f"→ Mail envoyé à {to_email} (CC : {', '.join(cc_list)})", "green")

    # 6. Purge (uniquement après envoi réussi)
    run_purge(argparse.Namespace(
        pdf_dir=args.pdf_dir, sheet_only=False, files_only=False, dry_run=False,
    ))
    log("=== ENVOI TERMINÉ ===", "bold")


# ─── CLI ────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description="Veille presse Procivis — boîte à outils")
    sub = p.add_subparsers(dest="mode", required=True)

    pe = sub.add_parser("extract", help="Outlook → PDF → texte → JSON")
    pe.add_argument("--days", type=int, default=7)
    pe.add_argument("--limit", type=int, default=None)
    pe.add_argument("--no-ocr", action="store_true")
    pe.add_argument("--pdf-dir", default="pdfs")
    pe.add_argument("--out", default="pdfs_extracted.json")

    pw = sub.add_parser("write", help="analyses.json → Google Sheet")
    pw.add_argument("--analyses", required=True)
    pw.add_argument("--dry-run", action="store_true")

    pf = sub.add_parser("fetch", help="re-télécharge des PDF par nom → ./pdfs/")
    pf.add_argument("--names", help="noms de fichiers séparés par des virgules "
                     "(un suffixe @debut-fin est toléré et ignoré ici)")
    pf.add_argument("--names-file", help="fichier texte, un nom par ligne")
    pf.add_argument("--days", type=int, default=10)
    pf.add_argument("--pdf-dir", default="pdfs")

    pp = sub.add_parser("purge", help="vide le Sheet + supprime les fichiers de travail")
    pp.add_argument("--pdf-dir", default="pdfs")
    pp.add_argument("--sheet-only", action="store_true")
    pp.add_argument("--files-only", action="store_true")
    pp.add_argument("--dry-run", action="store_true")

    penv = sub.add_parser("envoi", help="fetch + merge PDF + mail Stéphanie + purge")
    penv.add_argument("--names", help="noms PDF séparés par des virgules, dans l'ordre voulu ; "
                     "pour une coupure au sein d'un PDF « revue de presse » compilant plusieurs "
                     "articles, suffixer @debut-fin (pages 1-indexées, ex: clips.pdf@2-3)")
    penv.add_argument("--days", type=int, default=10)
    penv.add_argument("--pdf-dir", default="pdfs")

    args = p.parse_args()
    {"extract": run_extract, "write": run_write,
     "fetch": run_fetch, "purge": run_purge, "envoi": run_envoi}[args.mode](args)


if __name__ == "__main__":
    main()
