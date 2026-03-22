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
        raise Exception("Variables manquantes dans Railway")
    return create_client(url, key)

def charger_recherches(sb):
    try:
        result = sb.table("recherches").select("*").eq("actif", True).execute()
        return result.data or []
    except Exception as e:
        log(f"Erreur chargement recherches: {e}")
        return []

def scraper_leboncoin_mobile(recherche):
    """Utilise l API mobile LeBonCoin - moins bloquee que l API web"""
    try:
        marque = recherche.get("marque", "")
        modele = recherche.get("modele", "")
        prix_max = recherche.get("prix_max")
        km_max = recherche.get("km_max")
        annee_min = recherche.get("annee_min")
        annee_max = recherche.get("annee_max")
        carburant = recherche.get("carburant", "tous")

        # Parametres de recherche style app mobile
        params = {
            "category": "2",
            "limit": "100",
            "sort_by": "time",
            "sort_order": "desc",
        }

        if marque:
            params["brand"] = marque
        if modele:
            params["model"] = modele
        if prix_max:
            params["price_max"] = str(prix_max)
        if km_max:
            params["mileage_max"] = str(km_max)
        if annee_min:
            params["regdate_min"] = str(annee_min)
        if annee_max:
            params["regdate_max"] = str(annee_max)
        if carburant and carburant != "tous":
            params["fuel"] = carburant

        headers = {
            "User-Agent": "LeBonCoin/6.0.0 (iPhone; iOS 17.0; Scale/3.00)",
            "Accept": "application/json",
            "Accept-Language": "fr-FR;q=1.0",
            "api_key": "ba0c2dad52b3ec",
            "Content-Type": "application/json",
        }

        url = "https://api.leboncoin.fr/api/adfinder/v1/search"

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
            filters["ranges"]["price"] = {"max": prix_max}
        if km_max:
            filters["ranges"]["mileage"] = {"max": km_max}
        if annee_min or annee_max:
            filters["ranges"]["regdate"] = {}
            if annee_min:
                filters["ranges"]["regdate"]["min"] = annee_min
            if annee_max:
                filters["ranges"]["regdate"]["max"] = annee_max

        payload = {
            "limit": 100,
            "offset": 0,
            "filters": filters,
            "sort_by": "time",
            "sort_order": "desc",
            "owner_type": "all"
        }

        response = requests.post(url, json=payload, headers=headers, timeout=20)

        if response.status_code == 403:
            log(f"LeBonCoin bloque - tentative alternative...")
            return scraper_leboncoin_alternatif(recherche)

        response.raise_for_status()
        data = response.json()
        annonces = data.get("ads", [])
        prix_list = []

        for annonce in annonces:
            prix = annonce.get("price", [])
            if prix and isinstance(prix, list) and len(prix) > 0:
                p = prix[0]
                if isinstance(p, (int, float)) and 500 < p < 200000:
                    prix_list.append(int(p))

        log(f"LeBonCoin: {len(annonces)} annonces, {len(prix_list)} prix pour {recherche.get('nom')}")
        return prix_list

    except Exception as e:
        log(f"Erreur scraping mobile: {e}")
        return scraper_leboncoin_alternatif(recherche)

def scraper_leboncoin_alternatif(recherche):
    """Methode alternative via l URL de recherche classique"""
    try:
        marque = recherche.get("marque", "")
        modele = recherche.get("modele", "")
        prix_max = recherche.get("prix_max", "")
        km_max = recherche.get("km_max", "")

        # Construction URL de recherche
        query_parts = []
        if marque:
            query_parts.append(marque)
        if modele:
            query_parts.append(modele)

        query = "+".join(query_parts)
        url = f"https://www.leboncoin.fr/recherche?category=2&text={query}"

        if prix_max:
            url += f"&price=min-{prix_max}"
        if km_max:
            url += f"&mileage=min-{km_max}"

        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9",
        }

        response = requests.get(url, headers=headers, timeout=15)

        if response.status_code != 200:
            log(f"Methode alternative: statut {response.status_code}")
            return []

        # Extraction des prix depuis le HTML
        import re
        texte = response.text
        prix_list = []

        # Cherche les patterns de prix dans le HTML
        patterns = [
            r'"price":\[(\d+)\]',
            r'"price":(\d+)',
            r'(\d{3,6})\s*€',
            r'(\d{3,6})\s*&euro;',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, texte)
            for match in matches:
                try:
                    p = int(match)
                    if 500 < p < 200000:
                        prix_list.append(p)
                except:
                    pass

        # Deduplique et limite
        prix_list = list(set(prix_list))[:50]
        log(f"Alternative: {len(prix_list)} prix trouves pour {recherche.get('nom')}")
        return prix_list

    except Exception as e:
        log(f"Erreur methode alternative: {e}")
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
                    time.sleep(random.uniform(3, 8))
                    prix_list = scraper_leboncoin_mobile(recherche)
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
