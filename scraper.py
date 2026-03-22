import os
import time
import random
import requests
import json
import re
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
        raise Exception("Variables manquantes dans Railway")
    return create_client(url, key)

def get_scraper_key():
    key = os.environ.get("SCRAPER_API_KEY")
    if not key:
        raise Exception("SCRAPER_API_KEY manquante")
    return key

def charger_recherches(sb):
    try:
        result = sb.table("recherches").select("*").eq("actif", True).execute()
        return result.data or []
    except Exception as e:
        log(f"Erreur chargement recherches: {e}")
        return []

def scraper_leboncoin(recherche, scraper_key):
    try:
        marque = recherche.get("marque", "")
        modele = recherche.get("modele", "")
        prix_max = recherche.get("prix_max")
        km_max = recherche.get("km_max")
        annee_min = recherche.get("annee_min")
        annee_max = recherche.get("annee_max")
        carburant = recherche.get("carburant", "tous")

        # Construction payload API LeBonCoin
        filters = {
            "category": {"id": "2"},
            "enums": {},
            "ranges": {},
            "location": {}
        }

        if marque:
            filters["enums"]["vehicle_brand"] = {"value": marque.lower()}
        if modele:
            filters["enums"]["vehicle_model"] = {"value": modele.lower()}
        if carburant and carburant != "tous":
            filters["enums"]["fuel"] = {"value": carburant.lower()}
        if prix_max:
            filters["ranges"]["price"] = {"max": int(prix_max)}
        if km_max:
            filters["ranges"]["mileage"] = {"max": int(km_max)}
        if annee_min or annee_max:
            filters["ranges"]["regdate"] = {}
            if annee_min:
                filters["ranges"]["regdate"]["min"] = int(annee_min)
            if annee_max:
                filters["ranges"]["regdate"]["max"] = int(annee_max)

        payload = {
            "limit": 100,
            "offset": 0,
            "filters": filters,
            "sort_by": "time",
            "sort_order": "desc",
            "owner_type": "all"
        }

        # URL API LeBonCoin passee via ScraperAPI
        lbc_api_url = "https://api.leboncoin.fr/api/adfinder/v1/search"

        # ScraperAPI avec proxy France, sans rendu JS
       scraper_url = f"http://api.scraperapi.com/?api_key={scraper_key}&url={requests.utils.quote(lbc_api_url)}&country_code=fr&premium=true"

        log(f"Envoi requete via ScraperAPI...")

        response = requests.post(
            scraper_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "api_key": "ba0c2dad52b3ec",
                "Accept": "application/json",
            },
            timeout=60
        )

        log(f"Statut: {response.status_code}")

        if response.status_code == 200:
            try:
                data = response.json()
                annonces = data.get("ads", [])
                prix_list = []
                for annonce in annonces:
                    prix = annonce.get("price", [])
                    if prix and isinstance(prix, list) and len(prix) > 0:
                        p = prix[0]
                        if isinstance(p, (int, float)) and 500 < p < 200000:
                            prix_list.append(int(p))
                log(f"Succes: {len(annonces)} annonces, {len(prix_list)} prix pour {recherche.get('nom')}")
                return prix_list
            except Exception as e:
                log(f"Erreur parsing JSON: {e}")
                log(f"Reponse brute: {response.text[:200]}")
                return []
        else:
            log(f"Erreur {response.status_code}: {response.text[:200]}")
            return []

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
    scraper_key = get_scraper_key()
    log("Cle ScraperAPI OK")
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
                    prix_list = scraper_leboncoin(recherche, scraper_key)
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
