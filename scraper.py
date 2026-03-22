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

def get_scraper_api_key():
    key = os.environ.get("SCRAPER_API_KEY")
    if not key:
        raise Exception("Variable SCRAPER_API_KEY manquante dans Railway")
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

        # Construction URL LeBonCoin
        query_parts = []
        if marque:
            query_parts.append(marque)
        if modele:
            query_parts.append(modele)
        query = " ".join(query_parts)

        url_lbc = f"https://www.leboncoin.fr/recherche?category=2&text={query.replace(' ', '+')}"
        if prix_max:
            url_lbc += f"&price=min-{prix_max}"
        if km_max:
            url_lbc += f"&mileage=min-{km_max}"
        if annee_min:
            url_lbc += f"&regdate={annee_min}-max"
        if annee_max:
            url_lbc += f"&regdate=min-{annee_max}"
        if carburant and carburant != "tous":
            url_lbc += f"&fuel={carburant}"

        log(f"Scraping: {url_lbc}")

        # Utilise ScraperAPI pour contourner le blocage
        scraper_url = "http://api.scraperapi.com"
        params = {
            "api_key": scraper_key,
            "url": url_lbc,
            "render": "true",
            "country_code": "fr",
        }

        response = requests.get(scraper_url, params=params, timeout=60)
        log(f"Statut ScraperAPI: {response.status_code}")

        if response.status_code != 200:
            log(f"Erreur ScraperAPI: {response.status_code}")
            return []

        import re
        texte = response.text
        prix_list = []

        # Extraction des prix depuis le HTML LeBonCoin
        patterns = [
            r'"price":\[(\d+)\]',
            r'"price":(\d+)',
            r'"Price":(\d+)',
            r'data-qa-id="aditem_price"[^>]*>([0-9\s]+)',
            r'(\d{3,6})\s*\u20ac',
            r'(\d{3,6})\s*&euro;',
            r'"price_cents":(\d+)',
        ]

        for pattern in patterns:
            matches = re.findall(pattern, texte)
            for match in matches:
                try:
                    # Nettoie les espaces
                    clean = str(match).replace(" ", "").replace("\u00a0", "")
                    p = int(clean)
                    if 500 < p < 200000:
                        prix_list.append(p)
                except:
                    pass

        # Cherche aussi dans le JSON embarque dans la page
        json_pattern = r'"price":\[(\d+)\]'
        json_matches = re.findall(json_pattern, texte)
        for match in json_matches:
            try:
                p = int(match)
                if 500 < p < 200000:
                    prix_list.append(p)
            except:
                pass

        # Deduplique
        prix_list = list(set(prix_list))
        log(f"LeBonCoin via ScraperAPI: {len(prix_list)} prix trouves pour {recherche.get('nom')}")
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
    scraper_key = get_scraper_api_key()
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
