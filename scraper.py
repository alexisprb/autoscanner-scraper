import os
import time
import random
import requests
from datetime import datetime
from supabase import create_client

# Configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://bwslubxankieemxzwfye.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_9iexFtszDH2DCBfterJStA_w-EN3v3U")
INTERVAL_HEURES = int(os.environ.get("INTERVAL_HEURES", "6"))

# Headers pour simuler un vrai navigateur
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.leboncoin.fr/",
    "Origin": "https://www.leboncoin.fr",
}

sb = create_client(SUPABASE_URL, SUPABASE_KEY)


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def charger_recherches():
    """Charge toutes les recherches actives depuis Supabase"""
    try:
        result = sb.table("recherches").select("*").eq("actif", True).execute()
        return result.data or []
    except Exception as e:
        log(f"Erreur chargement recherches: {e}")
        return []


def scraper_leboncoin(recherche):
    """
    Scrape LeBonCoin pour une recherche donnee
    Retourne une liste de prix trouves
    """
    marque = recherche.get("marque", "").lower()
    modele = recherche.get("modele", "").lower() if recherche.get("modele") else ""
    prix_max = recherche.get("prix_max")
    km_max = recherche.get("km_max")
    annee_min = recherche.get("annee_min")
    annee_max = recherche.get("annee_max")
    carburant = recherche.get("carburant", "tous")

    # Construction de la requete API LeBonCoin
    params = {
        "category": "2",  # Categorie voitures
        "locations": "",
        "limit": "35",
        "offset": "0",
        "sort_by": "time",
        "sort_order": "desc",
    }

    # Filtres
    filters = {}

    if marque:
        filters["vehicle_brand"] = {"value": marque}

    if modele:
        filters["vehicle_model"] = {"value": modele}

    if prix_max:
        filters["price"] = {"max": str(prix_max)}

    if km_max:
        filters["mileage"] = {"max": str(km_max)}

    if annee_min or annee_max:
        filters["regdate"] = {}
        if annee_min:
            filters["regdate"]["min"] = str(annee_min)
        if annee_max:
            filters["regdate"]["max"] = str(annee_max)

    if carburant and carburant != "tous":
        carburant_map = {
            "diesel": "diesel",
            "essence": "essence",
            "hybride": "hybride",
            "electrique": "electrique"
        }
        if carburant.lower() in carburant_map:
            filters["fuel"] = {"value": carburant_map[carburant.lower()]}

    # Appel API LeBonCoin
    try:
        url = "https://api.leboncoin.fr/api/adfinder/v1/search"
        payload = {
            "limit": 35,
            "offset": 0,
            "filters": {
                "category": {"id": "2"},
                "enums": filters,
                "location": {}
            },
            "sort_by": "time",
            "sort_order": "desc"
        }

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

        log(f"LeBonCoin: {len(annonces)} annonces trouvees, {len(prix_list)} prix valides pour {recherche.get('nom')}")
        return prix_list

    except requests.exceptions.RequestException as e:
        log(f"Erreur requete LeBonCoin: {e}")
        return []
    except Exception as e:
        log(f"Erreur scraping: {e}")
        return []


def calculer_prix_moyen(prix_list):
    """Calcule le prix moyen en excluant les valeurs aberrantes"""
    if not prix_list:
        return None, None

    if len(prix_list) == 1:
        return prix_list[0], 1

    # Tri et suppression des 10% extremes pour avoir une moyenne propre
    prix_tries = sorted(prix_list)
    n = len(prix_tries)
    debut = max(0, int(n * 0.1))
    fin = min(n, int(n * 0.9) + 1)
    prix_filtres = prix_tries[debut:fin]

    if not prix_filtres:
        prix_filtres = prix_tries

    moyenne = int(sum(prix_filtres) / len(prix_filtres))
    return moyenne, len(prix_list)


def enregistrer_snapshot(recherche, prix_moyen, nb_annonces, km_moyen=None):
    """Enregistre un snapshot de prix dans Supabase"""
    try:
        data = {
            "marque": recherche.get("marque"),
            "modele": recherche.get("modele") or recherche.get("marque"),
            "annee": recherche.get("annee_min"),
            "km_moyen": km_moyen or recherche.get("km_max"),
            "prix_moyen": prix_moyen,
            "nb_annonces": nb_annonces,
        }

        result = sb.table("historique_prix").insert(data).execute()
        log(f"Snapshot enregistre: {data['marque']} {data['modele']} = {prix_moyen} E ({nb_annonces} annonces)")
        return True

    except Exception as e:
        log(f"Erreur enregistrement snapshot: {e}")
        return False


def traiter_recherche(recherche):
    """Traite une recherche : scrape + calcule + enregistre"""
    log(f"Traitement: {recherche.get('nom')} ({recherche.get('marque')} {recherche.get('modele', '')})")

    # Pause aleatoire pour eviter d etre bloque
    time.sleep(random.uniform(2, 5))

    # Scraping
    prix_list = scraper_leboncoin(recherche)

    if not prix_list:
        log(f"Aucun prix trouve pour {recherche.get('nom')}")
        return False

    # Calcul prix moyen
    prix_moyen, nb_annonces = calculer_prix_moyen(prix_list)

    if not prix_moyen:
        return False

    # Enregistrement
    return enregistrer_snapshot(recherche, prix_moyen, nb_annonces)


def boucle_principale():
    """Boucle principale du scraper"""
    log("AutoScanner Scraper demarre")
    log(f"Intervalle: toutes les {INTERVAL_HEURES} heures")

    cycle = 0
    while True:
        cycle += 1
        log(f"=== Cycle {cycle} ===")

        # Charger les recherches
        recherches = charger_recherches()
        log(f"{len(recherches)} recherche(s) active(s) trouvee(s)")

        if not recherches:
            log("Aucune recherche configuree - va dans AutoScanner pour en ajouter")
        else:
            succes = 0
            for recherche in recherches:
                try:
                    if traiter_recherche(recherche):
                        succes += 1
                except Exception as e:
                    log(f"Erreur pour {recherche.get('nom')}: {e}")

            log(f"Cycle termine: {succes}/{len(recherches)} recherches traitees avec succes")

        # Attendre le prochain cycle
        prochaine = INTERVAL_HEURES * 3600
        log(f"Prochain scan dans {INTERVAL_HEURES}h")
        time.sleep(prochaine)


if __name__ == "__main__":
    boucle_principale()
