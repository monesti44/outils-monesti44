#!/usr/bin/env python3
"""
Espace outils MonEsti44 : TOUTES les ventes DVF de Loire-Atlantique pour la Carte Prospection.

- Télécharge les « DVF géolocalisées » (data.gouv.fr) du 44, de 2014 à aujourd'hui
  (les années anciennes sont prises dans les versions archivées).
- Ne garde que les ventes d'UN logement (maison ou appartement), prix net vendeur.
- Écrit dvf/<tuile>.json (même format que le site MonEsti44) et dvf/meta.json.
Ce script ne touche pas au site www.monesti44.fr.
Usage : python scripts/dvf_complet.py [--source-dir dossier_de_csv_locaux]
"""
import csv, gzip, io, json, math, os, re, sys, argparse, datetime, urllib.request, collections

DEP = "44"
BASE = "https://files.data.gouv.fr/geo-dvf"
OUT = os.environ.get("DVF_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
TILE = 50  # 1/50e de degré, environ 2,2 km x 1,5 km
MONTHS_RECENT = 24


def tile_key(lat, lon):
    return f"{math.floor(lat * TILE)}_{math.floor(lon * TILE)}"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "monesti44-build"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


ARCHIVES = [
    "https://files.data.gouv.fr/geo-dvf/{rel}/csv/{y}/departements/{dep}.csv.gz",
    "https://files.opendatarchives.fr/cadastre.data.gouv.fr/data/etalab-dvf/{rel}/csv/{y}/departements/{dep}.csv.gz",
]


def versions_possibles(year):
    """Noms de versions archivées (AAAA-MM) qui peuvent contenir l'année demandée, de la plus récente à la plus ancienne."""
    out = []
    for ry in range(min(year + 5, datetime.date.today().year), year, -1):
        for mo in range(12, 0, -1):
            out.append(f"{ry}-{mo:02d}")
    return out


def open_year(year, source_dir):
    """Renvoie un lecteur CSV pour l'année demandée, ou None."""
    if source_dir:
        for name in (f"{year}-{DEP}.csv.gz", f"{year}-{DEP}.csv"):
            p = os.path.join(source_dir, name)
            if os.path.exists(p):
                raw = open(p, "rb").read()
                return csv.DictReader(io.StringIO(gunzip(raw).decode("utf-8")))
    urls = [f"{BASE}/latest/csv/{year}/departements/{DEP}.csv.gz"]
    for rel in versions_possibles(year):
        urls += [modele.format(rel=rel, y=year, dep=DEP) for modele in ARCHIVES]
    for u in urls:
        try:
            raw = fetch(u)
            print(f"  {year} : {u} ({len(raw)//1024} Ko)")
            return csv.DictReader(io.StringIO(gunzip(raw).decode("utf-8")))
        except Exception:
            continue
    print(f"  {year} : introuvable")
    return None


def gunzip(raw):
    return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw


def num(x):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return None


def sales_from(reader):
    """Regroupe les lignes par mutation et ne garde que les ventes d'UN seul logement."""
    muts = collections.defaultdict(lambda: {"locaux": {}, "row": None, "terrain": 0.0})
    for r in reader:
        if r.get("nature_mutation") != "Vente":
            continue
        m = muts[r["id_mutation"]]
        m["row"] = m["row"] or r
        st = num(r.get("surface_terrain")) or 0
        m["terrain"] = max(m["terrain"], st)
        t = r.get("type_local")
        if t in ("Maison", "Appartement"):
            key = (t, r.get("surface_reelle_bati"), r.get("lot1_numero"), r.get("nombre_pieces_principales"))
            m["locaux"][key] = r
        elif t == "Local industriel. commercial ou assimilé":
            m["locaux"][("pro", r.get("id_parcelle"))] = None  # vente mixte : exclue
    out = []
    for mid, m in muts.items():
        loc = list(m["locaux"].values())
        if len(loc) != 1 or loc[0] is None:
            continue
        r = loc[0]
        prix, surf = num(r.get("valeur_fonciere")), num(r.get("surface_reelle_bati"))
        lat, lon = num(r.get("latitude")), num(r.get("longitude"))
        if not prix or not surf or lat is None or lon is None:
            continue
        if surf < 9 or prix < 15000:
            continue
        pm2 = prix / surf
        if pm2 < 400 or pm2 > 15000:
            continue
        date = r["date_mutation"]
        out.append({
            "d": date,
            "t": 0 if r["type_local"] == "Maison" else 1,
            "p": int(round(prix)),
            "s": int(round(surf)),
            "n": int(num(r.get("nombre_pieces_principales")) or 0),
            "la": round(lat, 5),
            "lo": round(lon, 5),
            "v": (r.get("adresse_nom_voie") or "").title()[:40],
            "c": r.get("nom_commune") or "",
            "te": int(m["terrain"]) if r["type_local"] == "Maison" else 0,
        })
    return out


def write_tiles(sales, folder):
    path = os.path.join(OUT, folder)
    os.makedirs(path, exist_ok=True)
    for f in os.listdir(path):
        if f.endswith(".json"):
            os.remove(os.path.join(path, f))
    tiles = collections.defaultdict(list)
    for s in sales:
        # format compact : [date, type, prix, surface, pièces, lat, lon, voie, commune, terrain]
        tiles[tile_key(s["la"], s["lo"])].append([s["d"], s["t"], s["p"], s["s"], s["n"], s["la"], s["lo"], s["v"], s["c"], s["te"]])
    for k, rows in tiles.items():
        with open(os.path.join(path, k + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, separators=(",", ":"))
    return len(tiles)


ANNEE_DEBUT = 2014


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir")
    a = ap.parse_args()
    this_year = datetime.date.today().year
    ventes, annees = [], {}
    for y in range(ANNEE_DEBUT, this_year + 1):
        rd = open_year(y, a.source_dir)
        if not rd:
            continue
        s = [x for x in sales_from(rd) if x["d"].startswith(str(y))]
        annees[str(y)] = len(s)
        ventes += s
        print(f"  {y} : {len(s)} ventes")
    if len(ventes) < 1000:
        sys.exit("Trop peu de ventes trouvées : arrêt sans rien modifier.")
    # une même vente peut apparaître dans deux versions : on dédoublonne
    vus, uniques = set(), []
    for s in ventes:
        k = (s["d"], s["p"], s["s"], s["la"], s["lo"])
        if k not in vus:
            vus.add(k); uniques.append(s)
    nt = write_tiles(uniques, "dvf")
    meta = {
        "departement": DEP,
        "periode_debut": min(s["d"] for s in uniques),
        "periode_fin": max(s["d"] for s in uniques),
        "ventes": len(uniques),
        "par_annee": annees,
        "annees_manquantes": [str(y) for y in range(ANNEE_DEBUT, this_year) if str(y) not in annees],
        "tuile": TILE,
        "tuiles": nt,
        "mise_a_jour": datetime.date.today().isoformat(),
        "source": "DVF géolocalisées, data.gouv.fr (DGFiP / Etalab)",
    }
    json.dump(meta, open(os.path.join(OUT, "dvf", "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(uniques)} ventes de {meta['periode_debut']} à {meta['periode_fin']}, {nt} tuiles.")


if __name__ == "__main__":
    main()
