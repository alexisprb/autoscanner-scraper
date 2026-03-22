
import os
import time
import random
import requests
from datetime import datetime
from supabase import create_client

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_supabase():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    log(f"SUPABASE_URL: {url}")
    log(f"SUPABASE_KEY debut: {key[:20] if key else 'NON DEFINIE'}...")
    if not url or not key:
        raise Exception("Variables SUPABASE_URL et SUPABASE_KEY manquantes dans Railway")
    return create_client(url, key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Referer": "https://www.leboncoin.fr/",
}

def charger_recherches(sb):
    try:
        result = sb.table("recherches").select("*").eq("actif", True).execute()
        return result.data or []
    except Exception as e:
        log(f"Erreur chargement recherches: {e}")
        return []

def scraper_leboncoin(recherche):
    try:
        url = "https://api.leboncoin.fr/api/adfinder/v1/search"
        filters = {"category": {"id": "2"}, "enums": {}, "location": {}}
        marque = recherche.get("marque", "")
        modele = recherche.get("modele", "")
        if marque:
            filters["enums"]["vehicle_brand"] = {"value": marque.lower()}
        if modele:
            filters["enums"]["vehicle_model"] = {"value": modele.lower()}
        if recherche.get("prix_max"):
            filters["enums"]["price"] = {"max": str(recherche["prix_max"])}
        if recherche.get("km_max"):
            filters["enums"]["mileage"] = {"max": str(recherche["km_max"])}
        if recherche.get("annee_min") or recherche.get("annee_max"):
            filters["enums"]["regdate"] = {}
            if recherche.get("annee_min"):
                filters["enums"]["regdate"]["min"] = str(recherche["annee_min"])
            if recherche.get("annee_max"):
                filters["enums"]["regdate"]["max"] = str(recherche["annee_max"])
        carburant = recherche.get("carburant", "tous")
        if carburant and carburant != "tous":
            filters["enums"]["fuel"] = {"value": carburant.lower()}

        payload = {"limit": 35, "offset": 0, "filters": filters, "sort_by": "time", "sort_order": "desc"}
        response = requests.post(url, json=payload, headers=HEADERS, timeout=15)
        response.raise_for_status()
        data = response.json()
        annonces = data.get("ads", [])
        prix_list = []
        for annonce in annonces:
            prix = annonce.get("price", [])
            if prix and isinstance(prix, list) and len(prix) > 0:
                p = prix[0]
                if isinstance(p, (int, float)) and 500 < p < 200000:
                    prix_list.append(p)
        log(f"LeBonCoin: {len(annonces)} annonces, {len(prix_list)} prix pour {recherche.get('nom')}")
        return prix_list
    except Exception as e:
        log(f"Erreur scraping: {e}")
        return []

def calculer_prix_moyen(prix_list):
    if not prix_list:
        return None, None
    if len(prix_list) == 1:
        return prix_list[0], 1
    prix_tries = sorted(prix_list)
    n = len(prix_tries)
    debut = max(0, int(n * 0.1))
    fin = min(n, int(n * 0.9) + 1)
    prix_filtres = prix_tries[debut:fin] or prix_tries
    return int(sum(prix_filtres) / len(prix_filtres)), len(prix_list)

def enregistrer_snapshot(sb, recherche, prix_moyen, nb_annonces):
    try:
        data = {
            "marque": recherche.get("marque"),
            "modele": recherche.get("modele") or recherche.get("marque"),
            "annee": recherche.get("annee_min"),
            "km_moyen": recherche.get("km_max"),
            "prix_moyen": prix_moyen,
            "nb_annonces": nb_annonces,
        }
        sb.table("historique_prix").insert(data).execute()
        log(f"Snapshot OK: {data['marque']} {data['modele']} = {prix_moyen} E ({nb_annonces} annonces)")
        return True
    except Exception as e:
        log(f"Erreur enregistrement: {e}")
        return False

def boucle_principale():
    log("AutoScanner Scraper demarre")
    interval_heures = int(os.environ.get("INTERVAL_HEURES", "6"))
    log(f"Intervalle: toutes les {interval_heures} heures")
    sb = get_supabase()
    log("Connexion Supabase OK")
    cycle = 0
    while True:
        cycle += 1
        log(f"=== Cycle {cycle} ===")
        recherches = charger_recherches(sb)
        log(f"{len(recherches)} recherche(s) active(s)")
        if not recherches:
            log("Aucune recherche - ajoute des recherches dans AutoScanner")
        else:
            succes = 0
            for recherche in recherches:
                try:
                    time.sleep(random.uniform(2, 5))
                    prix_list = scraper_leboncoin(recherche)
                    if prix_list:
                        prix_moyen, nb = calculer_prix_moyen(prix_list)
                        if prix_moyen and enregistrer_snapshot(sb, recherche, prix_moyen, nb):
                            succes += 1
                    else:
                        log(f"Aucun prix pour {recherche.get('nom')}")
                except Exception as e:
                    log(f"Erreur {recherche.get('nom')}: {e}")
            log(f"Cycle termine: {succes}/{len(recherches)} OK")
        log(f"Prochain scan dans {interval_heures}h")
        time.sleep(interval_heures * 3600)

if __name__ == "__main__":
    boucle_principale()
