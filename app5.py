import os
import io
import sys
import csv
import json
import time
import re
import urllib.parse
from collections import deque
from flask import Flask, render_template, request, redirect, url_for, flash, send_file,jsonify,session
from bs4 import BeautifulSoup
import tldextract
import requests
import hashlib
from pymongo import MongoClient
from community_manager_agent import CommunityManagerAgent
import random
from video_generator import VideoGenerator
from dotenv import load_dotenv
load_dotenv()
from leonardo_ai import LeonardoAIGenerator
from typing import Dict
from authlib.integrations.flask_client import OAuth
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
import datetime
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage
import mimetypes
from bson import ObjectId
from advanced_adcopy_generator import AdvancedAdCopyGenerator
from improved_calendar_generator import ImprovedCalendarGenerator




LEONARDO_API_KEY = os.getenv("LEONARDO_API_KEY","1c895e8a-aad0-4a9a-bf84-9f802d729319")
print(f"🎨 LEONARDO_API_KEY chargée: {'OUI' if LEONARDO_API_KEY else 'NON'}")
if LEONARDO_API_KEY:
    print(f"🎨 Clé API Leonardo (premiers caractères): {LEONARDO_API_KEY[:10]}...")
    
REPLICATE_API_KEY = os.getenv("REPLICATE_API_KEY")
print(f"🎬 REPLICATE_API_KEY chargée: {'OUI' if REPLICATE_API_KEY else 'NON'}")


current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.append(parent_dir)
from leonardo_ai import LeonardoAIGenerator

from rag_system import initialize_rag_system,get_rag_system
from extra_routes import extra_routes
"""from rag_system_chroma import get_rag_system_chroma as get_rag_system"""



GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
print(f"🔑 GEMINI_API_KEY chargée: {'OUI' if GEMINI_API_KEY else 'NON'}")


try:
    from google import genai
    GOOGLE_GENAI_AVAILABLE = True
    print("✅ Bibliothèque google-genai disponible")
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False
    print("❌ Bibliothèque google-genai non disponible. Installez: pip install google-genai")

# Playwright import (sync)
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    PLAYWRIGHT_AVAILABLE = True
except Exception:
    PLAYWRIGHT_AVAILABLE = False

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_this_secret_please_change")
app.register_blueprint(extra_routes)

# Configuration OAuth
app.config['FACEBOOK_CLIENT_ID'] = os.getenv('FACEBOOK_CLIENT_ID', '')
app.config['FACEBOOK_CLIENT_SECRET'] = os.getenv('FACEBOOK_CLIENT_SECRET', '')
app.config['INSTAGRAM_CLIENT_ID'] = os.getenv('INSTAGRAM_CLIENT_ID', '')
app.config['INSTAGRAM_CLIENT_SECRET'] = os.getenv('INSTAGRAM_CLIENT_SECRET', '')


# ✅ CONFIGURATION GOOGLE OAUTH
app.config['GOOGLE_CLIENT_ID'] = os.getenv('GOOGLE_CLIENT_ID', '')
app.config['GOOGLE_CLIENT_SECRET'] = os.getenv('GOOGLE_CLIENT_SECRET', '')

# Initialisation OAuth
oauth = OAuth(app)

# Configuration Facebook
facebook = oauth.register(
    name='facebook',
    client_id=app.config['FACEBOOK_CLIENT_ID'],
    client_secret=app.config['FACEBOOK_CLIENT_SECRET'],
    access_token_url='https://graph.facebook.com/oauth/access_token',
    access_token_params=None,
    authorize_url='https://www.facebook.com/dialog/oauth',
    authorize_params=None,
    api_base_url='https://graph.facebook.com/',
    client_kwargs={'scope': 'email,public_profile,instagram_basic,pages_show_list'},
)

# Configuration Instagram
instagram = oauth.register(
    name='instagram',
    client_id=app.config['INSTAGRAM_CLIENT_ID'],
    client_secret=app.config['INSTAGRAM_CLIENT_SECRET'],
    access_token_url='https://api.instagram.com/oauth/access_token',
    authorize_url='https://api.instagram.com/oauth/authorize',
    api_base_url='https://graph.instagram.com/',
    client_kwargs={'scope': 'user_profile,user_media'},
)



google = oauth.register(
    name='google',
    client_id=app.config['GOOGLE_CLIENT_ID'],
    client_secret=app.config['GOOGLE_CLIENT_SECRET'],
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)




# Connexion MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["scraping_db"]
scrapes_collection = mongo_db["scraped_sites"]

import threading
last_request_time = {}
request_lock = threading.Lock()

session_cache = {}

# CONFIG par défaut
DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_DEPTH = 2
REQUEST_TIMEOUT = 20  # secondes pour requests
PLAYWRIGHT_TIMEOUT = 30000  # ms pour playwright


UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
IMAGES_FOLDER = os.path.join(UPLOAD_FOLDER, 'images')
VIDEOS_FOLDER = os.path.join(UPLOAD_FOLDER, 'videos')

# Créer les dossiers s'ils n'existent pas
os.makedirs(IMAGES_FOLDER, exist_ok=True)
os.makedirs(VIDEOS_FOLDER, exist_ok=True)

# Configuration Flask
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max

# Extensions autorisées
ALLOWED_IMAGES = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}
ALLOWED_VIDEOS = {'mp4', 'avi', 'mov', 'mkv', 'flv', 'wmv', 'webm', 'mts', 'm2ts'}

# Collection MongoDB pour les uploads
uploads_collection = mongo_db["uploads"]

# ==========================================
# 📦 MODÈLE UTILISATEUR (Utiliser MongoDB)
# ==========================================

users_collection = mongo_db["users"]

def create_user(email, password=None, name='', picture='', provider='email', provider_id=''):
    """Crée un nouvel utilisateur"""
    try:
        # Vérifier si l'utilisateur existe déjà
        if users_collection.find_one({'email': email}):
            return {'success': False, 'error': 'Cet email est déjà enregistré'}
        
        user_data = {
            'email': email,
            'name': name or email.split('@')[0],
            'picture': picture,
            'provider': provider,
            'provider_id': provider_id,
            'created_at': time.strftime("%Y-%m-%d %H:%M:%S"),
            'last_login': time.strftime("%Y-%m-%d %H:%M:%S"),
            'is_active': True,
            'onboarding_completed': False
        }
        
        if password:
            user_data['password'] = generate_password_hash(password)
        
        result = users_collection.insert_one(user_data)
        
        return {
            'success': True,
            'user_id': str(result.inserted_id),
            'message': 'Utilisateur créé avec succès'
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}


def get_user_by_email(email):
    """Récupère un utilisateur par email"""
    try:
        user = users_collection.find_one({'email': email})
        if user:
            user['_id'] = str(user['_id'])
        return user
    except Exception as e:
        print(f"❌ Erreur récupération utilisateur: {e}")
        return None


def get_user_by_id(user_id):
    """Récupère un utilisateur par ID"""
    try:
        from bson import ObjectId
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        if user:
            user['_id'] = str(user['_id'])
        return user
    except Exception as e:
        print(f"❌ Erreur récupération utilisateur: {e}")
        return None


def update_last_login(user_id):
    """Met à jour la dernière connexion"""
    try:
        from bson import ObjectId
        users_collection.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'last_login': time.strftime("%Y-%m-%d %H:%M:%S")}}
        )
    except Exception as e:
        print(f"❌ Erreur mise à jour connexion: {e}")


def smart_delay(domain, min_delay=2, max_delay=5):
    """Délai intelligent entre requêtes pour éviter les rate limits"""
    with request_lock:
        now = time.time()
        if domain in last_request_time:
            elapsed = now - last_request_time[domain]
            if elapsed < min_delay:
                wait_time = random.uniform(min_delay, max_delay)
                time.sleep(wait_time)
        last_request_time[domain] = time.time()
# Utilitaires
def same_domain(url1, url2):
    e1 = tldextract.extract(url1)
    e2 = tldextract.extract(url2)
    return (e1.domain, e1.suffix) == (e2.domain, e2.suffix)

def normalize_link(base, link):
    if not link:
        return None
    link = link.split('#')[0].strip()
    if link.startswith("javascript:") or link.startswith("mailto:"):
        return None
    return urllib.parse.urljoin(base, link)

def extract_products(html, url):
    """Extrait les informations des produits avec détection avancée"""
    soup = BeautifulSoup(html, "html.parser")
    products = []
    
    print(f"📄 Taille HTML: {len(html)} caractères")
    
    # Debug: compter les sélecteurs
    debug_counts = {
        '.product-miniature': len(soup.select('.product-miniature')),
        '.product': len(soup.select('.product')),
        '.ajax_block_product': len(soup.select('.ajax_block_product')),
        '[class*="product"]': len(soup.select('[class*="product"]')),
        'article': len(soup.select('article')),
        '.item': len(soup.select('.item'))
    }
    print(f"🔍 Debug sélecteurs: {debug_counts}")
    
    # APPROCHE 1: Sélecteurs CSS
    products.extend(extract_with_css_selectors(soup, url))
    print(f"📦 Approche 1 (CSS): {len(products)} produits")
    
    # APPROCHE 2: Analyse de contenu
    if len(products) < 2:
        content_products = extract_with_content_analysis(soup, url)
        products.extend(content_products)
        print(f"📦 Approche 2 (Contenu): {len(content_products)} produits")
    
    # APPROCHE 3: Grilles
    grid_products = extract_with_grid_detection(soup, url)
    products.extend(grid_products)
    print(f"📦 Approche 3 (Grilles): {len(grid_products)} produits")
    
    # APPROCHE 4: Données structurées
    structured_products = extract_from_structured_data(soup, url)
    products.extend(structured_products)
    print(f"📦 Approche 4 (Structurées): {len(structured_products)} produits")
    
    # APPROCHE 5: Regex texte (spécial Comptoirs Richard)
    if 'comptoirsrichard' in url:
        regex_products = extract_products_by_text_pattern(soup, url)
        products.extend(regex_products)
        print(f"📦 Approche 5 (Regex): {len(regex_products)} produits")
    
    # Déduplication
    unique_products = deduplicate_products(products)
    print(f"✅ Total final: {len(unique_products)} produits uniques")
    
    return unique_products

def extract_products_by_text_pattern(soup, base_url):
    """Extrait les produits à partir de motifs textuels comme 'Nom16,97 €'"""
    products = []
    # Récupérer tout le texte visible
    text = soup.get_text()
    # Pattern : texte (>=8 caractères) suivi immédiatement d'un prix en €
    pattern = r'([A-Za-zÀ-ÿ0-9\s\-&éèêëçîïôù\’\'\(\)]{8,}?)(\d+,\d{2}\s*€)'
    matches = re.findall(pattern, text)
    for name, price in matches:
        name = name.strip()
        price = price.strip()
        # Filtrer les faux positifs (trop court, contient "€", etc.)
        if len(name) < 8 or "€" in name or name.isdigit():
            continue
        # Nettoyer les doublons de prix ou de nom (cas fréquent sur ce site)
        if price in name:
            name = name.replace(price, "").strip()
        products.append({
            "name": name,
            "price": price,
            "product_url": base_url,
            "is_promoted": True  # car souvent dans "Produits connexes"
        })
    return products


def extract_with_css_selectors(soup, url):
    """Extraction avec des sélecteurs spécifiques pour Comptoirs Richard"""
    products = []
    
    # Sélecteurs spécifiques pour Comptoirs Richard
    if 'comptoirsrichard' in url:
        selectors = [
            # Sélecteurs spécifiques au site
            '.product-miniature',
            '.ajax_block_product',
            '.products article',
            '#content article',
            '.featured-products article',
            '.product-list article',
            'div[itemtype*="Product"]',
            '.js-product-miniature',
            '.product-container',
            '.item-product',
            '.product-item',
            # Nouveaux sélecteurs observés
            '[data-id-product]',
            '.product-thumbnail',
            '.thumbnail-container',
            '.product-description',
            '.product-title',
            # Sélecteurs de grille
            '.product-grid .item',
            '.products-grid .item',
            '.item.product',
            '.product-element'
        ]
    else:
        selectors = [...]  # vos sélecteurs existants
    
    for selector in selectors:
        try:
            elements = soup.select(selector)
            print(f"🔍 Sélecteur '{selector}': {len(elements)} éléments trouvés")
            for element in elements:
                product_data = extract_product_data_from_element(element, url)
                if product_data and product_data.get('name'):
                    products.append(product_data)
        except Exception as e:
            print(f"❌ Erreur avec sélecteur {selector}: {e}")
            continue
    
    return products

# ================================
# FONCTIONS POUR PRODUITS PROMUS (intégrées depuis app_promotions.py)
# ================================

def extract_promoted_products(html, base_url):
    """Extrait les produits promus UNIQUEMENT depuis le carrousel Instagram"""
    soup = BeautifulSoup(html, "html.parser")
    found_products = []

    # Cible les listes de produits dans les posts Instagram
    product_lists = soup.select('.ybc_ins_popup_product_list')
    for product_list in product_lists:
        product_items = product_list.select('.ybc_ins_popup_product_item')
        for item in product_items:
            try:
                # Nom
                name_elem = item.select_one('.product_name')
                if not name_elem:
                    continue
                name = name_elem.get_text(strip=True)
                if not name or len(name) < 5:
                    continue

                # Prix
                price_elem = item.select_one('.price')
                price = price_elem.get_text(strip=True) if price_elem else ""

                # URL
                link_elem = item.select_one('a[href]')
                product_url = urllib.parse.urljoin(base_url, link_elem['href']) if link_elem else base_url

                # Image (optionnel)
                img_elem = item.select_one('.ybc_ins_popup_product_image')
                image = img_elem['src'] if img_elem and img_elem.get('src') else ""

                found_products.append({
                    "name": name,
                    "price": price,
                    "product_url": product_url,
                    "image": image,
                    "is_promoted": True,
                    "promotion_detected": True,
                    "promotion_indicators": ["instagram_carousel"]
                })
            except Exception as e:
                continue
                 
    # Déduplication
    unique_products = []
    seen = set()
    for p in found_products:
        key = f"{p['name'].lower()}|{p['price']}"
        if key not in seen:
            seen.add(key)
            unique_products.append(p)

    print(f"🎯 Produits promus extraits depuis Instagram : {len(unique_products)}")
    return unique_products

def extract_promoted_product_data(element, base_url):
    """Extrait les données d'un produit promu"""
    product_data = {}
    
    # Nom
    name = extract_promoted_product_name(element)
    if name:
        product_data['name'] = name
    
    # Prix
    price = extract_promoted_product_price(element)
    if price:
        product_data['price'] = price
    
    # Description
    description = extract_promoted_product_description(element)
    if description:
        product_data['description'] = description
    
    # Image
    image = extract_promoted_product_image(element, base_url)
    if image:
        product_data['image'] = image
    
    # URL produit
    product_url = extract_promoted_product_url(element, base_url)
    if product_url:
        product_data['product_url'] = product_url
    
    # Détection de promotion
    promotion_indicators = detect_promotion_indicators(element)
    if promotion_indicators:
        product_data['promotion_indicators'] = promotion_indicators
    
    return product_data

def extract_promoted_product_name(element):
    """Extrait le nom d'un produit promu"""
    name_selectors = [
        'h1', 'h2', 'h3', '.product-name', '.title', '.name',
        '.promo-title', '.featured-title', '.banner-title',
        '[data-product-name]', '.heading', '.title-promo'
    ]
    
    for selector in name_selectors:
        name_elem = element.select_one(selector)
        if name_elem:
            name = name_elem.get_text(strip=True)
            if 3 <= len(name) <= 200:
                return name
    
    # Fallback: chercher dans tout l'élément
    text_content = element.get_text(strip=True)
    lines = [line.strip() for line in text_content.split('\n') if line.strip()]
    for line in lines:
        if 5 <= len(line) <= 100 and not line.isdigit():
            return line
    
    return None

def extract_promoted_product_price(element):
    """Extrait le prix d'un produit promu"""
    price_selectors = [
        '.price', '.promo-price', '.special-price', '.discount-price',
        '.new-price', '.current-price', '.price-tag', '.cost'
    ]
    
    for selector in price_selectors:
        price_elem = element.select_one(selector)
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            price_match = re.search(r'[\d]+[.,\s]*\d*[.,\s]*\d+', price_text)
            if price_match:
                return price_text.strip()
    
    # Fallback: chercher par regex dans tout l'élément
    element_text = element.get_text()
    price_match = re.search(r'[\d]+[.,\s]*\d*[.,\s]*\d+', element_text)
    if price_match:
        return price_match.group(0).strip()
    
    return None

def extract_promoted_product_description(element):
    """Extrait la description d'un produit promu"""
    desc_selectors = [
        '.description', '.promo-desc', '.featured-desc', '.product-desc',
        '.excerpt', '.summary', '.desc', '.text-content'
    ]
    
    for selector in desc_selectors:
        desc_elem = element.select_one(selector)
        if desc_elem:
            desc_text = desc_elem.get_text(strip=True)
            if desc_text:
                return desc_text[:300]
    
    return None

def extract_promoted_product_image(element, base_url):
    """Extrait l'image d'un produit promu"""
    img_selectors = [
        'img', '.product-image', '.promo-image', '.featured-image',
        '.banner-image', '.main-image', '.hero-image'
    ]
    
    for selector in img_selectors:
        img_elem = element.select_one(selector)
        if img_elem:
            src = (img_elem.get('src') or 
                  img_elem.get('data-src') or 
                  img_elem.get('data-original'))
            if src and not src.startswith('data:'):
                return urllib.parse.urljoin(base_url, src)
    
    return None

def extract_promoted_product_url(element, base_url):
    """Extrait l'URL d'un produit promu"""
    link_selectors = [
        'a', '.product-link', '.promo-link', '.featured-link',
        '.banner-link', '.cta-button', '.btn', '.button'
    ]
    
    for selector in link_selectors:
        link_elem = element.select_one(selector)
        if link_elem and link_elem.get('href'):
            href = link_elem.get('href')
            if href and not href.startswith(('javascript:', '#')):
                return urllib.parse.urljoin(base_url, href)
    
    return None

def detect_promotion_indicators(element):
    """Détecte les indicateurs de promotion"""
    indicators = []
    element_text = element.get_text().lower()
    element_classes = ' '.join(element.get('class', [])).lower()
    
    # Mots-clés de promotion
    promotion_keywords = [
        'promo', 'promotion', 'sale', 'solde', 'reduction', 'discount',
        'offre', 'special', 'spécial', 'new', 'nouveau', 'nouvelle',
        'limited', 'limitée', 'exclusif', 'exclusive', 'best', 'top',
        'featured', 'vedette', 'highlight', 'spotlight', 'banner',
        'hero', 'main', 'principal'
    ]
    
    for keyword in promotion_keywords:
        if (keyword in element_text or 
            keyword in element_classes or 
            keyword in str(element.get('id', '')).lower()):
            indicators.append(keyword)
    
    # Indicateurs visuels (badges, labels)
    badge_selectors = ['.badge', '.label', '.tag', '.ribbon', '.sticker']
    for selector in badge_selectors:
        if element.select_one(selector):
            indicators.append('badge_present')
            break
    
    return indicators

def extract_products_from_section(section, base_url):
    """Extrait les produits d'une section principale"""
    products = []
    
    # Chercher les éléments qui ressemblent à des produits
    product_like_elements = section.find_all(['div', 'article', 'li'], 
                                           class_=re.compile(r'product|item|card'))
    
    for element in product_like_elements:
        product_data = extract_promoted_product_data(element, base_url)
        if product_data and product_data.get('name'):
            products.append(product_data)
    
    return products


def extract_with_content_analysis(soup, url):
    """Analyse de contenu renforcée pour sites dynamiques"""
    products = []
    
    # Recherche étendue dans tout le HTML
    potential_elements = soup.find_all(['div', 'article', 'li', 'section', 'tr', 'td'])
    
    for element in potential_elements:
        element_text = element.get_text(strip=True)
        
        # Critères pour identifier un produit
        has_price = bool(re.search(r'\d+[.,]\d{2}\s*€', element_text))
        has_name = len(element_text) > 10 and len(element_text) < 200
        
        if has_price and has_name:
            product_data = extract_product_data_from_content(element, url)
            if product_data and product_data.get('name'):
                products.append(product_data)
    
    return products

def extract_with_grid_detection(soup, url):
    """Détection des produits dans les grilles et listes"""
    products = []
    
    # Chercher les conteneurs de grille
    grid_indicators = ['grid', 'list', 'products', 'items', 'catalog', 'shop', 'row', 'cols']
    grid_containers = []
    
    for element in soup.find_all(['div', 'section', 'ul']):
        element_classes = element.get('class', [])
        element_id = element.get('id', '')
        element_text = element.get_text().lower()
        
        # Vérifier si c'est un conteneur de grille
        is_grid_container = (
            any(indicator in str(element_classes).lower() for indicator in grid_indicators) or
            any(indicator in element_id.lower() for indicator in grid_indicators) or
            any(indicator in element_text for indicator in ['produit', 'product', 'article', 'item'])
        )
        
        if is_grid_container:
            grid_containers.append(element)
    
    # Analyser les éléments dans les grilles
    for container in grid_containers:
        children = container.find_all(['div', 'article', 'li', 'section'])
        for child in children:
            if is_likely_product_element(child):
                product_data = extract_product_data_from_content(child, url)
                if product_data and product_data.get('name'):
                    products.append(product_data)
    
    return products

def extract_from_structured_data(soup, url):
    """Extraction depuis les données structurées"""
    products = []
    
    # JSON-LD
    script_tags = soup.find_all('script', type='application/ld+json')
    for script in script_tags:
        try:
            data = json.loads(script.string)
            if isinstance(data, dict):
                data = [data]
            
            for item in data if isinstance(data, list) else [data]:
                if item.get('@type') in ['Product', 'IndividualProduct', 'ProductGroup']:
                    product_data = {
                        'name': item.get('name', ''),
                        'price': extract_price_from_structured_data(item),
                        'description': item.get('description', '')[:200],
                        'image': item.get('image', ''),
                        'product_url': item.get('url', ''),
                        'sku': item.get('sku', '')
                    }
                    if product_data['name']:
                        products.append(product_data)
        except Exception:
            continue
    
    # Microdata
    microdata_products = soup.find_all(attrs={'itemtype': re.compile(r'.*Product.*')})
    for product_elem in microdata_products:
        product_data = extract_from_microdata(product_elem, url)
        if product_data and product_data.get('name'):
            products.append(product_data)
    
    return products

def is_likely_product_element(element):
    """Détermine si un élément est probablement un produit"""
    # Vérifier la taille du contenu
    text_content = element.get_text(strip=True)
    if len(text_content) < 10 or len(text_content) > 2000:
        return False
    
    # Vérifier les caractéristiques d'un produit
    has_image = bool(element.find('img'))
    has_title = bool(element.find(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'strong', 'b']))
    has_price = bool(re.search(r'[\d]+[.,\s]*\d*[.,\s]*\d+', text_content))
    has_link = bool(element.find('a', href=True))
    
    # Score de probabilité
    score = sum([has_image, has_title, has_price, has_link])
    
    return score >= 2  # Au moins 2 caractéristiques

def extract_product_data_from_element(element, url):
    """Extrait les données d'un élément produit identifié par sélecteur CSS"""
    product_data = {}
    
    # Nom du produit
    name = extract_product_name(element)
    if name:
        product_data['name'] = name
    
    # Prix
    price = extract_product_price(element)
    if price:
        product_data['price'] = price
    
    # Description
    description = extract_product_description(element)
    if description:
        product_data['description'] = description
    
    # Image
    image = extract_product_image(element, url)
    if image:
        product_data['image'] = image
    
    # Lien
    product_url = extract_product_url(element, url)
    if product_url:
        product_data['product_url'] = product_url
    
    # SKU
    sku = extract_product_sku(element)
    if sku:
        product_data['sku'] = sku
    
    return product_data

def extract_product_data_from_content(element, url):
    """Extrait les données d'un produit par analyse de contenu"""
    product_data = {}
    
    # Nom - chercher les titres et textes significatifs
    name_candidates = []
    
    # Titres
    titles = element.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    for title in titles:
        text = title.get_text(strip=True)
        if 3 <= len(text) <= 100:
            name_candidates.append(text)
    
    # Textes en gras/strong
    strong_texts = element.find_all(['strong', 'b'])
    for strong in strong_texts:
        text = strong.get_text(strip=True)
        if 5 <= len(text) <= 80:
            name_candidates.append(text)
    
    # Premier lien significatif
    links = element.find_all('a', href=True)
    for link in links:
        text = link.get_text(strip=True)
        if 5 <= len(text) <= 80 and not text.isdigit():
            name_candidates.append(text)
            break
    
    if name_candidates:
        product_data['name'] = name_candidates[0]
    
    # Prix - recherche avancée
    price = extract_price_advanced(element)
    if price:
        product_data['price'] = price
    
    # Image
    image = extract_product_image(element, url)
    if image:
        product_data['image'] = image
    
    # Lien
    product_url = extract_product_url(element, url)
    if product_url:
        product_data['product_url'] = product_url
    
    return product_data

def extract_product_name(element):
    """Extrait le nom du produit"""
    name_selectors = [
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        '.product-name', '.product-title', '.name', '.title',
        '.item-name', '.item-title', '.product__name', '.product-name-link',
        '.nom-produit', '.titre-produit', '.productName', '.product_name',
        '.card-title', '.product-card__title', '.product__title',
        '[data-product-name]', '[data-name]', '.elementor-heading-title'
    ]
    
    for selector in name_selectors:
        name_elem = element.select_one(selector)
        if name_elem:
            name = name_elem.get_text(strip=True)
            if name and 3 <= len(name) <= 200:
                return name
    
    return None

def extract_product_price(element):
    """Extrait le prix du produit"""
    price_selectors = [
        '.price', '.product-price', '.cost', '.amount', '.current-price',
        '.price-amount', '.woocommerce-Price-amount', '.regular-price',
        '.sale-price', '.special-price', '.prix', '.price-final',
        '.product-price', '.item-price', '.price__amount',
        '[class*="price"]', '[class*="prix"]', '.elementor-price'
    ]
    
    for selector in price_selectors:
        price_elem = element.select_one(selector)
        if price_elem:
            price_text = price_elem.get_text(strip=True)
            # Nettoyer et valider le prix
            price_match = re.search(r'[\d]+[.,\s]*\d*[.,\s]*\d+', price_text)
            if price_match:
                return price_text.strip()
    
    return None

def extract_price_advanced(element):
    """Extraction avancée des prix"""
    element_text = element.get_text()
    
    # Patterns de prix plus robustes
    price_patterns = [
        r'(\d+[.,]\d{1,2})\s*€',
        r'€\s*(\d+[.,]\d{1,2})',
        r'(\d+)\s*EUR',
        r'Prix\s*:\s*[\'"]?(\d+[.,]\d{1,2})',
        r'price\s*:\s*[\'"]?(\d+[.,]\d{1,2})',
        r'(\d+)[\s,]*(\d+)[\s,]*(\d+)\s*€',  # Format 16 97 €
    ]
    
    for pattern in price_patterns:
        matches = re.findall(pattern, element_text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                # Reconstituer le prix depuis les groupes
                price = ''.join(match).replace(' ', '')
            else:
                price = match
            # Nettoyer le prix
            price = price.replace(',', '.').replace(' ', '')
            if re.match(r'\d+\.\d{2}', price):
                return f"{price} €"
    
    return None

def extract_product_description(element):
    """Extrait la description du produit"""
    desc_selectors = [
        '.description', '.product-description', '.desc', '.excerpt',
        '.product-desc', '.item-description', '.short-description',
        '.product-short-description', '.resume', '.product__description'
    ]
    
    for selector in desc_selectors:
        desc_elem = element.select_one(selector)
        if desc_elem:
            desc_text = desc_elem.get_text(strip=True)
            if desc_text:
                return desc_text[:500]
    
    return None

def extract_product_image(element, base_url):
    """Extrait l'image du produit"""
    img_selectors = [
        'img', '.product-image', '.image', '.item-image',
        '.product-img', '.product-thumbnail', '.thumbnail',
        '.product__image', '.card-img-top', '.product-image-img'
    ]
    
    for selector in img_selectors:
        img_elem = element.select_one(selector)
        if img_elem:
            src = img_elem.get('src') or img_elem.get('data-src') or img_elem.get('data-original')
            if src and not src.startswith('data:'):
                return urllib.parse.urljoin(base_url, src)
    
    return None

def extract_product_url(element, base_url):
    """Extrait l'URL du produit"""
    link_selectors = [
        'a', '.product-link', '.item-link', '.product-url',
        '.product-title-link', '.more-details', '.voir-produit'
    ]
    
    for selector in link_selectors:
        link_elem = element.select_one(selector)
        if link_elem and link_elem.get('href'):
            href = link_elem.get('href')
            if href and not href.startswith(('javascript:', '#')):
                return urllib.parse.urljoin(base_url, href)
    
    return None

def extract_product_sku(element):
    """Extrait le SKU du produit"""
    sku_selectors = [
        '.sku', '.product-id', '.reference', '.product-reference',
        '[data-sku]', '[data-product-id]', '[data-id]', '[data-reference]'
    ]
    
    for selector in sku_selectors:
        sku_elem = element.select_one(selector)
        if sku_elem:
            sku_text = sku_elem.get_text(strip=True)
            sku_data = (sku_elem.get('data-sku') or 
                       sku_elem.get('data-product-id') or 
                       sku_elem.get('data-id') or
                       sku_elem.get('data-reference'))
            return sku_text or sku_data
    
    return None

def extract_price_from_structured_data(data):
    """Extrait le prix des données structurées"""
    price = data.get('price') or data.get('offers', {}).get('price')
    if price:
        if isinstance(price, (int, float)):
            return f"{price} €"
        elif isinstance(price, str):
            price_match = re.search(r'(\d+[.,]\d{1,2})', price)
            if price_match:
                return f"{price_match.group(1)} €"
    return None

def extract_from_microdata(element, url):
    """Extrait les données depuis les microdatas"""
    product_data = {}
    
    # Nom
    name_elem = element.find(attrs={'itemprop': 'name'})
    if name_elem:
        product_data['name'] = name_elem.get_text(strip=True)
    
    # Prix
    price_elem = element.find(attrs={'itemprop': 'price'})
    if price_elem:
        product_data['price'] = price_elem.get_text(strip=True)
    
    # Image
    image_elem = element.find(attrs={'itemprop': 'image'})
    if image_elem and image_elem.get('src'):
        product_data['image'] = urllib.parse.urljoin(url, image_elem['src'])
    
    # URL
    url_elem = element.find(attrs={'itemprop': 'url'})
    if url_elem and url_elem.get('href'):
        product_data['product_url'] = urllib.parse.urljoin(url, url_elem['href'])
    
    return product_data

def deduplicate_products(products):
    """Déduplique les produits"""
    seen = set()
    unique_products = []
    
    for product in products:
        # Créer une clé unique
        name = product.get('name', '').lower().strip()
        url = product.get('product_url', '')
        price = product.get('price', '')
        
        key = f"{name}|{url}|{price}"
        
        if key not in seen and name:  # Ignorer les produits sans nom
            seen.add(key)
            unique_products.append(product)
    
    return unique_products

def clean_mongo_data(data):
    """Nettoie les données MongoDB pour la sérialisation JSON"""
    if isinstance(data, dict):
        return {k: clean_mongo_data(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [clean_mongo_data(item) for item in data]
    elif hasattr(data, 'isoformat'):  # datetime
        return data.isoformat()
    elif hasattr(data, '__str__') and 'ObjectId' in str(type(data)):
        return str(data)
    else:
        return data

def extract_footer(html, url):
    """Extrait toutes les informations du footer"""
    soup = BeautifulSoup(html, "html.parser")
    footer_data = {}
    
    # Trouver le footer avec plus de sélecteurs
    footer_selectors = ['footer', '.footer', '#footer', '.site-footer', '.main-footer']
    footer = None
    for selector in footer_selectors:
        footer = soup.select_one(selector)
        if footer:
            break
    
    if footer:
        # Liens du footer
        footer_links = []
        for link in footer.find_all('a', href=True):
            link_text = link.get_text(strip=True)
            if link_text:  # Ignorer les liens sans texte
                footer_links.append({
                    'text': link_text,
                    'url': urllib.parse.urljoin(url, link['href'])
                })
        footer_data['links'] = footer_links
        
        # Texte du footer
        footer_text = footer.get_text(separator=' ', strip=True)
        footer_data['text'] = footer_text[:1000]  # Limiter la longueur
        
        # Informations de contact
        contact_info = {}
        
        # Extraire numéros de téléphone
        phones = re.findall(r'[\+]?[0-9\s\-\(\)]{10,}', footer_text)
        if phones:
            contact_info['phones'] = phones
        
        # Emails
        emails = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', footer_text)
        if emails:
            contact_info['emails'] = emails
            
        footer_data['contact_info'] = contact_info
    
    return footer_data


def extract_all_data(html, url,depth=0):
    """Extrait toutes les données structurées d'une page - Version améliorée"""
    soup = BeautifulSoup(html, "html.parser")
    
    # Données de base avec plus de contexte
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    meta_desc = ""
    m = soup.find("meta", attrs={"name":"description"})
    if m and m.get("content"):
        meta_desc = m["content"].strip()
    
    # Contenu textuel enrichi
    h1 = ""
    h1_tag = soup.find("h1")
    if h1_tag:
        h1 = h1_tag.get_text(separator=" ", strip=True)
    
    # Extraire tout le contenu textuel important
    content_text = []
    for tag in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']:
        elements = soup.find_all(tag)
        for elem in elements:
            text = elem.get_text(separator=" ", strip=True)
            if text and len(text) > 10:
                content_text.append(text)
    
    excerpt = " | ".join(content_text)[:3000]  # Plus de contenu
    
    # Premier paragraphe (alternative à first_paragraph)
    first_paragraph = ""
    p_tag = soup.find('p')
    if p_tag:
        first_paragraph = p_tag.get_text(strip=True)[:500]
    
    # Images avec contexte
    images = []
    for img in soup.find_all('img', src=True):
        src = img.get('src') or img.get('data-src') or img.get('data-original')
        if src:
            alt = img.get('alt', '')
            images.append({
                'url': urllib.parse.urljoin(url, src),
                'alt': alt[:200] if alt else ''
            })
    
    # Métadonnées étendues
    meta_data = {}
    meta_tags = soup.find_all('meta')
    for meta in meta_tags:
        name = meta.get('name') or meta.get('property') or meta.get('itemprop')
        content = meta.get('content')
        if name and content:
            meta_data[name] = content
    
    # Extraire les produits avec catégorisation
    all_products = extract_products(html, url)
    promoted_products = []
    is_homepage = is_likely_homepage(url, depth)
    
    if is_homepage:
        print(f"🎯 Page d'accueil détectée - Tous les {len(all_products)} produits sont marqués comme promus")
        
        # Copier tous les produits comme produits promus
        for product in all_products:
            promoted_product = product.copy()
            promoted_product.update({
                "is_promoted": True,
                "promoted_on_homepage": True,
                "promotion_type": "homepage_featured"
            })
            promoted_products.append(promoted_product)
    
    # Pour les produits normaux, s'assurer qu'ils ne sont pas marqués comme promus
    for product in all_products:
        product['is_promoted'] = False
        product['promoted_on_homepage'] = False
    
    # Extraire les données structurées
    structured_data = extract_structured_data(soup, url)
    
    return {
        "url": url,
        "title": title,
        "meta_description": meta_desc,
        "h1": h1,
        "excerpt": excerpt,
        "first_paragraph": first_paragraph,
        "content_text": content_text[:20],
        "images": images[:15],
        "meta_data": meta_data,
        "structured_data": structured_data,
        "products": all_products,           # Produits normaux (is_promoted=False)
        "promoted_products": promoted_products,  # Produits promus (is_promoted=True)
        "footer": extract_footer(html, url),
        "word_count": len(soup.get_text().split()),
        "is_homepage": is_homepage,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

def is_likely_homepage(url, depth):
    """Détermine si l'URL est une page d'accueil"""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.rstrip('/')
    
    # Critères simples pour identifier la page d'accueil
    homepage_indicators = [
        depth == 0,  # URL de départ
        path == "" or path == "/",  # Racine du site
        '/home' in path.lower(),
        '/accueil' in path.lower(), 
        '/index' in path.lower(),
        path.count('/') <= 2,  # Peu de segments dans le chemin
        # Domaines simples sans chemin complexe
        len(path.split('/')) <= 2 and not any(ext in path for ext in ['.html', '.php', '.asp'])
    ]
    
    is_home = any(homepage_indicators)
    
    if is_home:
        print(f"🏠 Page d'accueil détectée: {url} (depth: {depth})")
    
    return is_home

def extract_structured_data(soup, url):
    """Extrait toutes les données structurées"""
    structured_data = []
    
    # JSON-LD
    script_tags = soup.find_all('script', type='application/ld+json')
    for script in script_tags:
        try:
            data = json.loads(script.string)
            structured_data.append(data)
        except Exception:
            pass
    
    # Microdata
    microdata = soup.find_all(attrs={'itemtype': True})
    for item in microdata:
        item_type = item.get('itemtype', '')
        if 'Product' in item_type or 'Offer' in item_type:
            try:
                name_elem = item.find(attrs={'itemprop': 'name'})
                price_elem = item.find(attrs={'itemprop': 'price'})
                
                microdata_obj = {
                    '@type': item_type,
                    'name': name_elem.get_text(strip=True) if name_elem else '',
                    'price': price_elem.get_text(strip=True) if price_elem else ''
                }
                structured_data.append(microdata_obj)
            except Exception:
                pass
    
    return structured_data

def merge_scraped_data():
    """Fusionne les données en séparant clairement produits normaux et produits promus"""
    try:
        # Charger last_scrape.json (produits normaux + données complètes)
        scrape_data = {}
        if os.path.exists("last_scrape.json"):
            with open("last_scrape.json", "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    scrape_data = json.loads(content)
                    if not isinstance(scrape_data, dict):
                        print("⚠️ last_scrape.json n'est pas un dictionnaire, réinitialisation")
                        scrape_data = {}
        
        # Charger last_promotions.json (uniquement produits promus)
        promo_data = {}
        if os.path.exists("last_promotions.json"):
            with open("last_promotions.json", "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    promo_data = json.loads(content)
                    if not isinstance(promo_data, dict):
                        print("⚠️ last_promotions.json n'est pas un dictionnaire, ignoré")
                        promo_data = {}
        
        # Structure finale fusionnée
        merged_data = scrape_data.copy()
        
        print(f"🔍 Fusion en cours: {len(scrape_data)} sites scrapés, {len(promo_data)} sites promotionnels")
        
        # Pour chaque site dans les promotions
        for site_id, promo_site_data in promo_data.items():
            if not isinstance(promo_site_data, dict):
                continue
                
            # Vérifier si le site existe déjà dans les données scrapées
            if site_id in merged_data:
                print(f"🔄 Fusion du site existant: {site_id}")
                
                # S'assurer que la structure est correcte
                if not isinstance(merged_data[site_id], dict):
                    merged_data[site_id] = {"results": []}
                
                if "results" not in merged_data[site_id]:
                    merged_data[site_id]["results"] = []
                
                # Récupérer les résultats promotionnels
                promo_results = promo_site_data.get("results", [])
                if not isinstance(promo_results, list):
                    promo_results = []
                
                # Pour chaque résultat promotionnel
                for promo_result in promo_results:
                    if not isinstance(promo_result, dict):
                        continue
                        
                    # Trouver le résultat correspondant dans les données scrapées (par URL)
                    promo_url = promo_result.get("url", "")
                    found_match = False
                    
                    for i, existing_result in enumerate(merged_data[site_id]["results"]):
                        if not isinstance(existing_result, dict):
                            continue
                            
                        existing_url = existing_result.get("url", "")
                        
                        # Si les URLs correspondent, ajouter les produits promus
                        if existing_url == promo_url or not existing_url:
                            print(f"  ✅ Ajout de {len(promo_result.get('promoted_products', []))} produits promus à la page {i}")
                            
                            # S'assurer que la clé promoted_products existe
                            if "promoted_products" not in existing_result:
                                existing_result["promoted_products"] = []
                            
                            # Ajouter les produits promus (remplacement complet)
                            existing_promoted = existing_result.get("promoted_products", [])
                            new_promoted = promo_result.get("promoted_products", [])
                            
                            # Fusionner et dédupliquer
                            all_promoted = existing_promoted + new_promoted
                            unique_promoted = []
                            seen = set()
                            
                            for product in all_promoted:
                                if not isinstance(product, dict):
                                    continue
                                key = f"{product.get('name', '').lower()}|{product.get('product_url', '')}"
                                if key not in seen:
                                    seen.add(key)
                                    unique_promoted.append(product)
                            
                            existing_result["promoted_products"] = unique_promoted
                            found_match = True
                            break
                    
                    # Si aucun match trouvé, ajouter comme nouveau résultat
                    if not found_match and promo_url:
                        print(f"  ➕ Nouvelle page ajoutée: {promo_url}")
                        merged_data[site_id]["results"].append(promo_result)
            
            else:
                # Nouveau site - l'ajouter complètement
                print(f"➕ Nouveau site ajouté: {site_id}")
                merged_data[site_id] = promo_site_data
        
        # Sauvegarder le résultat fusionné
        with open("last_scrape.json", "w", encoding="utf-8") as f:
            json.dump(merged_data, f, ensure_ascii=False, indent=2)
        
        # Calculer les statistiques finales
        total_normal_products = 0
        total_promoted_products = 0
        total_sites = 0
        
        for site_id, site_data in merged_data.items():
            if isinstance(site_data, dict) and "results" in site_data:
                total_sites += 1
                for result in site_data["results"]:
                    if isinstance(result, dict):
                        total_normal_products += len(result.get("products", []))
                        total_promoted_products += len(result.get("promoted_products", []))
        
        print(f"✅ Fusion terminée: {total_sites} sites, {total_normal_products} produits normaux, {total_promoted_products} produits promus")
        
        return merged_data
        
    except Exception as e:
        print(f"❌ Erreur lors de la fusion: {e}")
        import traceback
        print(f"🔍 Détails: {traceback.format_exc()}")
        raise

def extract_links(html, base_url):
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a in soup.find_all("a", href=True):
        norm = normalize_link(base_url, a["href"])
        if norm:
            links.add(norm)
    return list(links)

def fetch_with_requests(url):
    domain = urllib.parse.urlparse(url).netloc
    
    # Réutiliser la session pour le même domaine
    if domain not in session_cache:
        session_cache[domain] = requests.Session()
    
    session = session_cache[domain]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
        "DNT": "1",
        "Referer": f"https://{domain}/"
    }
    
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, headers=headers, allow_redirects=True)
        resp.raise_for_status()
        return resp.text, None
    except Exception as e:
        return None, str(e)

def fetch_with_playwright(url, timeout_ms=90000):
    """Version UNIVERSELLE qui s'adapte à tous les sites"""
    if not PLAYWRIGHT_AVAILABLE:
        return None, "Playwright non disponible"
    
    try:
        with sync_playwright() as pw:
            # 🔧 CONFIGURATION ADAPTATIVE
            domain = urllib.parse.urlparse(url).netloc.lower()
            
            # Détecter le type de site
            is_complex_site = any(site in domain for site in [
                'comptoirsrichard', 'leroymerlin', 'darty', 'boulanger', 
                'cdiscount', 'fnac', 'amazon'
            ])
            
            # Choisir la stratégie
            if is_complex_site:
                print(f"🎯 Site complexe détecté: {domain} - Stratégie ROBUSTE")
                headless = False  # Visible pour debug
                wait_strategy = 'networkidle'
                extra_wait = 20000
                scroll_iterations = 12
                scroll_delay = 1500
            else:
                print(f"🎯 Site standard détecté: {domain} - Stratégie STANDARD")
                headless = True
                wait_strategy = 'domcontentloaded'
                extra_wait = 8000
                scroll_iterations = 6
                scroll_delay = 1000
            
            # 🚀 CONFIGURATION DU NAVIGATEUR
            browser = pw.chromium.launch(
                headless=headless,
                args=[
                    '--no-sandbox',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--disable-web-security',
                    '--disable-features=IsolateOrigins,site-per-process',
                    '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                locale='fr-FR',
                timezone_id='Europe/Paris',
                java_script_enabled=True,
                ignore_https_errors=True
            )

            # 🛡️ ANTI-DÉTECTION
            page = context.new_page()
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['fr-FR','fr','en-US','en']});
                window.chrome = {runtime: {}};
            """)
            
            print(f"🌐 Navigation vers {url}...")
            
            # 📥 CHARGEMENT DE LA PAGE
            try:
                page.goto(url, timeout=timeout_ms, wait_until=wait_strategy)
            except Exception as e:
                print(f"⚠️ Premier chargement échoué, tentative fallback: {e}")
                try:
                    page.goto(url, timeout=timeout_ms, wait_until='load')
                except:
                    page.goto(url, timeout=timeout_ms, wait_until='domcontentloaded')
            
            # ⏳ ATTENTE INTELLIGENTE
            print(f"⏳ Attente du chargement ({extra_wait//1000}s)...")
            page.wait_for_timeout(extra_wait)
            
            # 🔍 DÉTECTION DU CONTENU
            content_info = page.evaluate("""() => {
                const bodyText = document.body.innerText;
                return {
                    bodyLength: bodyText.length,
                    hasProducts: document.querySelectorAll('.product, [class*="product"], .item, .price, .produit').length > 0,
                    title: document.title,
                    totalElements: document.querySelectorAll('*').length
                };
            }""")
            
            print(f"🔍 Analyse contenu: {content_info}")
            
            # 🎯 STRATÉGIE ADAPTATIVE SELON LE CONTENU
            if content_info['bodyLength'] < 500 and not content_info['hasProducts']:
                print("🔄 Contenu insuffisant - Activation mode ROBUSTE")
                # Basculer en mode robuste
                scroll_iterations = 10
                extra_wait += 10000
                
                # Essayer différents sélecteurs
                selectors_to_try = [
                    '.product', '.product-miniature', '.item', '.price',
                    '[class*="product"]', '[class*="item"]', 'article',
                    '.product-grid', '.products', '.catalog', '.shop'
                ]
                
                for selector in selectors_to_try:
                    try:
                        page.wait_for_selector(selector, timeout=5000)
                        print(f"✅ Élément trouvé: {selector}")
                        break
                    except:
                        continue
            
            # 🖱️ DÉFILEMENT INTELLIGENT
            print(f"🔄 Défilement ({scroll_iterations} passages)...")
            for i in range(scroll_iterations):
                scroll_position = (i + 1) * 800
                page.evaluate(f"window.scrollTo(0, {scroll_position})")
                page.wait_for_timeout(scroll_delay)
                
                # Vérifier périodiquement le contenu
                if i % 3 == 0:
                    current_content = page.evaluate("document.body.innerText.length")
                    print(f"   📊 Contenu après scroll {i+1}: {current_content} caractères")
            
            # ⏳ DERNIÈRE ATTENTE
            page.wait_for_timeout(5000)
            
            # 💾 RÉCUPÉRATION FINALE
            html = page.content()
            browser.close()
            
            # ✅ VÉRIFICATION FINALE
            final_length = len(html)
            print(f"✅ HTML final: {final_length} caractères")
            
            if final_length < 1000:
                print("❌ HTML trop court - Site probablement bloqué")
                return None, f"Blocage détecté - HTML: {final_length} caractères"
            
            return html, None

    except Exception as e:
        print(f"❌ Erreur Playwright universel: {e}")
        import traceback
        traceback.print_exc()
        return None, f"Erreur: {e}"


def fetch_url_with_retry(url, render_js=False, max_retries=3):
    """Tente de récupérer l'URL avec plusieurs essais et délais intelligents"""
    domain = urllib.parse.urlparse(url).netloc
    
    for attempt in range(max_retries + 1):
        try:
            # Délai intelligent avant chaque requête
            smart_delay(domain, min_delay=2, max_delay=5)
            
            if render_js and PLAYWRIGHT_AVAILABLE:
                html, error = fetch_with_playwright(url)
            else:
                html, error = fetch_with_requests(url)
                
            if html and not error:
                return html, None
                
            # Alterner entre les méthodes si échec
            if html is None and PLAYWRIGHT_AVAILABLE and not render_js:
                time.sleep(random.uniform(1, 3))  # Pause aléatoire
                html, error = fetch_with_playwright(url)
                if html:
                    return html, None
                    
        except Exception as e:
            error = str(e)
            
        if attempt < max_retries:
            backoff_time = (2 ** attempt) + random.uniform(0, 1)  # Backoff exponentiel
            time.sleep(backoff_time)
            print(f"🔄 Nouvelle tentative {attempt + 1}/{max_retries} pour {url} (attente: {backoff_time:.1f}s)")
    
    return None, error or "Échec après plusieurs tentatives"

# Fonction pour calculer les statistiques
def calculate_statistics(results):
    """Calcule les statistiques des résultats"""
    total_pages = len(results)
    total_products = 0
    total_images = 0
    error_pages = 0
    
    for item in results:
        total_products += len(item.get('products', []))
        total_images += len(item.get('images', []))
        if item.get('error'):
            error_pages += 1
    
    return {
        'total_pages': total_pages,
        'total_products': total_products,
        'total_images': total_images,
        'error_pages': error_pages
    }



def generate_advanced_adcopy_for_post(
    rag_system,
    site_id: str,
    site_info: dict,
    post_data: dict,
    mongo_client
) -> dict:
    """
    Génère un ad-copy AVANCÉ et contextuel pour un post spécifique
    À AJOUTER DANS app5.py
    """
    try:
        from pymongo import MongoClient
        import re
        
        # 1️⃣ RÉCUPÉRER TOUS LES PRODUITS DU SITE
        print(f"🔍 Analyse des produits pour ad-copy avancé...")
        
        scrapes_collection = mongo_client["scraping_db"]["scraped_sites"]
        site_doc = scrapes_collection.find_one({"site_id": site_id})
        
        if not site_doc:
            print(f"⚠️ Site {site_id} non trouvé - ad-copy basique")
            return _generate_basic_adcopy(post_data, site_info)
        
        all_products = []
        results = site_doc.get("results", [])
        
        for page in results:
            if isinstance(page, dict):
                for product in page.get("products", []):
                    if isinstance(product, dict):
                        all_products.append(product)
                for product in page.get("promoted_products", []):
                    if isinstance(product, dict):
                        all_products.append(product)
        
        print(f"📊 {len(all_products)} produits trouvés")
        
        # 2️⃣ ANALYSER LES PRODUITS
        analysis = {
            'price_ranges': _analyze_price_ranges(all_products),
            'product_categories': _categorize_products(all_products),
            'key_features': _extract_key_features(all_products),
            'usp': _extract_usp(site_info, all_products),
            'pain_points': _analyze_pain_points(all_products),
            'promoted_count': len([p for p in all_products if p.get('is_promoted')])
        }
        
        print(f"✅ Analyse complétée:")
        print(f"   - Catégories: {', '.join(analysis['product_categories'][:3])}")
        print(f"   - Prix: {analysis['price_ranges']['min']} - {analysis['price_ranges']['max']}")
        print(f"   - Caractéristiques: {', '.join(analysis['key_features'][:3])}")
        
        # 3️⃣ CONSTRUIRE LE PROMPT AVANCÉ
        advanced_prompt = f"""
EN TANT QUE COPYWRITER SENIOR EN E-COMMERCE (15+ ans d'expérience):

## CONTEXTE CLIENT PRÉCIS
- **Entreprise**: {site_info.get('company_name', 'Notre marque')}
- **Industrie**: {site_info.get('industry', 'e-commerce')}
- **Voice de marque**: {site_info.get('brand_voice', 'professionnel')}
- **Positionnement**: {site_info.get('market_position', 'Standard')}
- **Target audience**: {site_info.get('target_audience', {}).get('demographics', ['General'])}

## ANALYSE PRODUITS RÉELLE
- **Catégories**: {', '.join(analysis['product_categories'][:5])}
- **Plage de prix**: {analysis['price_ranges']['min']} - {analysis['price_ranges']['max']} (Segment: {analysis['price_ranges'].get('range', 'Standard')})
- **Caractéristiques clés**: {', '.join(analysis['key_features'][:5])}
- **Propositions uniques**: {', '.join(analysis['usp'][:3])}
- **Points de douleur clients**: {', '.join(analysis['pain_points'][:3])}
- **Nombre de produits promus**: {analysis['promoted_count']}

## POST À PROMOUVOIR
- **Thème**: {post_data.get('theme', 'Produit')}
- **Type de contenu**: {post_data.get('content_type', 'general')}
- **Angle créatif**: {post_data.get('creative_angle', 'Standard')}
- **Objectif**: {post_data.get('marketing_goal', 'Engagement')}
- **Heure de publication**: {post_data.get('best_time', '12:00')}

## INSTRUCTIONS COPYWRITING AVANCÉ
Génère un ad-copy PRÉCIS, CONTEXTUEL et PERSUASIF en format JSON:

{{
    "short_copy": "COURT (120 car max) - Hook accrocheur + CTA immédiat",
    "medium_copy": "MOYEN (250 car max) - Context + bénéfice + CTA",
    "long_copy": "LONG (500 car max) - Story complète + traiter objections + CTA",
    "hashtags": ["#tag1", "#tag2", "#tag3", "#tag4"],
    "cta_variations": ["CTA variante 1", "CTA variante 2", "CTA variante 3"],
    "emoji_suggestion": "emoji_approprié",
    "platform_tips": "Conseils d'optimisation pour la plateforme"
}}

### Règles à appliquer:
1. Utiliser les VRAIES catégories et prix trouvés
2. Adapter au tone de marque ({site_info.get('brand_voice', 'professional')})
3. Créer URGENCE et DÉSIR d'achat
4. Traiter les objections avec preuves sociales
5. CTA clair et irrésistible
6. Hashtags pertinents et populaires actuellement

### Exemple de qualité attendue:

Thème: "Électronique premium été"
SHORT: "⚡ Tech 2024 - Réduction 30% sur les meilleurs appareils. Stock limité → Découvrir"
MEDIUM: "L'été c'est l'occasion de s'équiper! 🌞 Notre sélection premium d'électronique combine performance et design. 5000+ clients satisfaits ⭐ Livraison gratuite + Garantie 2 ans → Commander"
LONG: "Vous cherchez LA bonne tech pour cet été? 💻 Nous avons sélectionné les meilleurs appareils du marché - testés et approuvés par nos experts. Nos clients les adorent (note moyenne 4.8/5 ⭐). Prix réduit MAINTENANT SEULEMENT: jusqu'à 30% de réduction. Livraison gratuite en 48h + Garantie 2 ans complète. Ne laissez pas passer cette opportunité → Commander maintenant avant rupture"

## GÉNÉRATION RÉELLE:
Génère maintenant un ad-copy AUTHENTIQUE basé sur tous les éléments ci-dessus.
Réponds UNIQUEMENT en JSON valide, sans preamble.
"""
        
        # 4️⃣ GÉNÉRER AVEC GEMINI
        print("🤖 Génération ad-copy avec Gemini...")
        response = rag_system.generate_response(advanced_prompt)
        
        # 5️⃣ PARSER LA RÉPONSE
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                adcopy_data = json.loads(json_match.group())
                print("✅ Ad-copy généré avec succès!")
                return adcopy_data
            else:
                print("⚠️ JSON non trouvé - ad-copy basique")
                return _generate_basic_adcopy(post_data, site_info)
        except json.JSONDecodeError as e:
            print(f"❌ Erreur JSON parsing: {e}")
            return _generate_basic_adcopy(post_data, site_info)
            
    except Exception as e:
        print(f"❌ Erreur génération ad-copy avancé: {e}")
        return _generate_basic_adcopy(post_data, site_info)


# ==========================================
# 🛠️ FONCTIONS HELPER
# ==========================================

def _analyze_price_ranges(products: list) -> dict:
    """Analyse les plages de prix"""
    prices = []
    for p in products:
        price_str = p.get('price', '')
        import re
        price_match = re.search(r'(\d+)[.,](\d+)', price_str)
        if price_match:
            price = float(f"{price_match.group(1)}.{price_match.group(2)}")
            prices.append(price)
    
    if not prices:
        return {'min': 'N/A', 'max': 'N/A', 'avg': 'N/A', 'range': 'N/A'}
    
    prices.sort()
    return {
        'min': f"{min(prices):.2f}€",
        'max': f"{max(prices):.2f}€",
        'avg': f"{sum(prices)/len(prices):.2f}€",
        'range': 'Premium' if max(prices) > 500 else 'Mid-range' if max(prices) > 100 else 'Budget'
    }


def _categorize_products(products: list) -> list:
    """Catégorise les produits"""
    categories = set()
    
    for product in products:
        name = product.get('name', '').lower()
        desc = product.get('description', '').lower()
        text = name + " " + desc
        
        category_keywords = {
            'électronique': ['téléphone', 'laptop', 'pc', 'ordinateur', 'électronique', 'tech'],
            'mode': ['vêtement', 'robe', 'chaussures', 'sac', 'accessoires', 'mode'],
            'beauté': ['cosmétique', 'maquillage', 'soins', 'parfum', 'beauté'],
            'maison': ['meuble', 'décoration', 'cuisine', 'salle', 'maison'],
            'sport': ['sport', 'fitness', 'équipement', 'chaussures'],
            'alimentation': ['aliment', 'nourriture', 'boisson', 'café', 'chocolat'],
        }
        
        for category, keywords in category_keywords.items():
            if any(kw in text for kw in keywords):
                categories.add(category)
    
    return list(categories) if categories else ['e-commerce']


def _extract_key_features(products: list) -> list:
    """Extrait les caractéristiques clés"""
    features = set()
    
    for product in products:
        text = (product.get('name', '') + " " + product.get('description', '')).lower()
        
        feature_keywords = [
            'gratuit', 'livraison', 'nouveau', 'stock', 'limité', 
            'exclusif', 'promo', 'remise', 'garantie', 'premium',
            'luxe', 'écologique', 'bio', 'naturel'
        ]
        
        for feature in feature_keywords:
            if feature in text:
                features.add(feature.capitalize())
    
    return list(features)[:8]


def _extract_usp(site_info: dict, products: list) -> list:
    """Extrait les propositions uniques"""
    usp = []
    
    market_pos = site_info.get('market_position', '').lower()
    brand_voice = site_info.get('brand_voice', '').lower()
    
    if 'premium' in market_pos:
        usp.append('Qualité exceptionnelle')
    if 'accessible' in brand_voice or 'budget' in market_pos:
        usp.append('Prix compétitifs')
    if 'innovation' in brand_voice:
        usp.append('Produits innovants')
    
    if len([p for p in products if p.get('is_promoted')]) > 0:
        usp.append('Offres exclusives')
    
    return usp if usp else ['Meilleure sélection']


def _analyze_pain_points(products: list) -> list:
    """Analyse les pain points"""
    pain_points = []
    all_text = " ".join([p.get('description', '') for p in products]).lower()
    
    if 'livraison' in all_text:
        pain_points.append('Livraison rapide')
    if 'garantie' in all_text:
        pain_points.append('Garantie & assurance')
    if 'retour' in all_text:
        pain_points.append('Flexibilité retours')
    if len(pain_points) == 0:
        pain_points.append('Qualité & fiabilité')
    
    return pain_points


def _generate_basic_adcopy(post_data: dict, site_info: dict) -> dict:
    """Génère un ad-copy de base en fallback"""
    theme = post_data.get('theme', 'Découvrez nos produits')
    company = site_info.get('company_name', 'Notre marque')
    
    return {
        'short_copy': f"✨ {theme} - Qualité premium → Découvrir",
        'medium_copy': f"Trouvez ce que vous cherchez chez {company}. Qualité garantie.",
        'long_copy': f"Bienvenue chez {company}! Découvrez notre sélection exclusive combinant qualité et prix.",
        'hashtags': ['#ecommerce', '#shopping', '#qualité'],
        'cta_variations': ['Découvrir', 'En savoir plus', 'Explorer'],
        'emoji_suggestion': '✨',
        'platform_tips': 'Utiliser des images haute qualité'
    }

# Endpoint page principale
@app.route("/", methods=["GET"])
def index():
    """Page d'accueil - Redirection vers login si non connecté"""
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    return render_template("index5.html", playwright_available=PLAYWRIGHT_AVAILABLE)

# Endpoint de scraping (form POST)
@app.route("/scrape", methods=["POST"])
def scrape():
    start_url = request.form.get("start_url", "").strip()
    
    if 'comptoirsrichard' in start_url.lower():
        render_js = True
        print("🔧 Comptoirs Richard détecté - Forçage Playwright")
    else:
        render_js = True if request.form.get("render_js") == "on" else False
    scrape_products = True if request.form.get("scrape_products") == "on" else False
    scrape_promoted_products = True if request.form.get("scrape_promoted_products") == "on" else False
    scrape_footer = True if request.form.get("scrape_footer") == "on" else False
    
    try:
        max_pages = int(request.form.get("max_pages") or DEFAULT_MAX_PAGES)
    except Exception:
        max_pages = DEFAULT_MAX_PAGES
    try:
        max_depth = int(request.form.get("max_depth") or DEFAULT_MAX_DEPTH)
    except Exception:
        max_depth = DEFAULT_MAX_DEPTH

    if not start_url:
        flash("URL de départ manquante.", "danger")
        return redirect(url_for("index"))

    # normaliser start_url
    parsed = urllib.parse.urlparse(start_url)
    if not parsed.scheme:
        start_url = "http://" + start_url
    start_url = start_url.rstrip("/")
    
     # Générer un site_id stable basé sur le domaine
    domain = urllib.parse.urlparse(start_url).netloc
    site_id = hashlib.md5(domain.encode("utf-8")).hexdigest()[:8]

    print(f"🕸️ Scraping du site {start_url} (site_id={site_id})")

    # BFS crawl
    visited = set()
    q = deque()
    q.append((start_url, 0))
    results = []

    while q and len(visited) < max_pages:
        url, depth = q.popleft()
        if url in visited:
            continue
        if depth > max_depth:
            continue
        if not same_domain(start_url, url):
            continue

        visited.add(url)


        # Récup HTML
        html = None
        error = None
        
    
        if render_js and PLAYWRIGHT_AVAILABLE:
            print(f"🎯 Utilisation de Playwright UNIVERSEL pour {url}")
            html, error = fetch_with_playwright(url)
        else:
            html, error = fetch_with_requests(url)

        if html:
            # Extraire toutes les données
            all_data = extract_all_data(html, url,depth)
            
            # Log des résultats
            total_products = len(all_data.get("products", []))
            promoted_products = len(all_data.get("promoted_products", []))
            is_homepage = all_data.get("is_homepage", False)
            
            print(f"📊 Page {url} - Produits: {total_products} - Promus: {promoted_products} - Accueil: {is_homepage}")
            
            # Si on ne veut pas les produits ou footer, les retirer
            if not scrape_products:
                all_data.pop('products', None)
            if not scrape_promoted_products:
                all_data.pop('promoted_products', None)
            else:
                # Logique pour détecter automatiquement les pages d'accueil
                current_url = all_data.get("url", "")
                is_likely_homepage = (
                    current_url.endswith('/') or 
                    '/home' in current_url.lower() or 
                    current_url.count('/') <= 2 or
                    depth == 0
                )
                all_data["is_homepage"] = is_likely_homepage
                
            if not scrape_footer:
                all_data.pop('footer', None)
                
            struct = all_data
            links = extract_links(html, url)
        else:
            struct = {
                "url": url,
                "title": "",
                "meta_description": "",
                "h1": "",
                "excerpt": "",
                "first_paragraph": "",
                "images": [],
                "meta_data": {},
                "structured_data": [],
                "products": [],
                "promoted_products": [],
                "footer": {},
                "word_count": 0,
                "is_homepage": False
            }
            links = []

        item = {
            "url": struct["url"],
            "title": struct["title"],
            "meta_description": struct["meta_description"],
            "h1": struct["h1"],
            "excerpt": struct["excerpt"],
            "first_paragraph": struct.get("first_paragraph",""),
            "images": struct.get("images", []),
            "meta_data": struct.get("meta_data", {}),
            "structured_data": struct.get("structured_data", []),
            "products": struct.get("products", []),
            "promoted_products": struct.get("promoted_products", []),
            "footer": struct.get("footer", {}),
            "word_count": struct.get("word_count", 0),
            "depth": depth,
            "error": error,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        results.append(item)

        # queue internal links
        for link in links:
            if len(visited) + len(q) >= max_pages:
                break
            if not same_domain(start_url, link):
                continue
            if link in visited:
                continue
            q.append((link.rstrip("/"), depth + 1))


    # À la fin du scraping, sauvegarder dans MongoDB
    scrape_data = {
        "site_id": site_id,
        "start_url": start_url,
        "results": results,
        "scraped_count": len(results),
        "max_pages": max_pages,
        "max_depth": max_depth,
        "scraped_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }

    # Supprimer les anciennes données du même site avant d’insérer
    scrapes_collection.delete_one({"site_id": site_id})
    scrapes_collection.insert_one(scrape_data)

    print(f"✅ Données du site {site_id} sauvegardées dans MongoDB.")


    # Calculer les statistiques pour CE SITE uniquement
    stats = calculate_statistics(results)
    
    flash(f"✅ {len(results)} pages scrappées pour le site {start_url}.", "success")
    return render_template("index5.html",
                         results=results,
                         stats=stats,
                         playwright_available=PLAYWRIGHT_AVAILABLE,
                         current_site_id=site_id,
                         all_sites=scrape_data)
    
@app.route("/merge_data", methods=["POST"])
def merge_data():
    try:
        merged_data = merge_scraped_data()
        flash(f"Données fusionnées avec succès! {len(merged_data)} sites disponibles.", "success")
    except Exception as e:
        flash(f"Erreur lors de la fusion: {str(e)}", "danger")
    
    return redirect(url_for("index"))

# Téléchargement JSON
@app.route("/download_json", methods=["GET"])
def download_json():
    path = "last_scrape.json"
    if not os.path.exists(path):
        flash("Aucun résultat disponible. Lance un scraping d'abord.", "warning")
        return redirect(url_for("index"))
    return send_file(path, as_attachment=True, download_name="data.json", mimetype="application/json")


@app.route("/init_rag_manual", methods=["POST"])
def init_rag_manual():
    """Initialise manuellement le RAG avec chargement incrémentiel"""
    if not GEMINI_API_KEY:
        flash("Clé API Gemini non configurée.", "danger")
        return redirect(url_for("ask_question"))
    
    if not GOOGLE_GENAI_AVAILABLE:
        flash("Bibliothèque google-genai non installée. Exécutez: pip install google-genai", "danger")
        return redirect(url_for("ask_question"))
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Vérifier d'abord s'il y a des changements
        changes = rag_system.check_data_changes()
        
        if not changes['has_changes'] and rag_system.is_initialized:
            flash("✅ RAG déjà à jour - Pas de nouveau site détecté", "info")
            return redirect(url_for("ask_question"))
        
        # Charger les données (incrémentiel si déjà initialisé)
        success = rag_system.load_scraped_data()
        
        if success:
            stats = rag_system.get_stats()
            if changes['has_changes']:
                message = (f"🎉 {changes['new_sites_count']} nouveau(x) site(s) chargé(s)!\n"
                          f"📊 Total: {stats['total_sites']} sites, {stats['total_documents']} documents")
            else:
                message = f"✅ RAG initialisé: {stats['total_sites']} sites, {stats['total_documents']} documents"
            flash(message, "success")
        else:
            flash("❌ Échec du chargement des données", "danger")
            
    except Exception as e:
        flash(f"❌ Erreur lors du chargement: {str(e)}", "danger")
    
    return redirect(url_for("ask_question"))

@app.route("/init_rag_force", methods=["POST"])
def init_rag_force():
    """Force la réinitialisation complète du RAG"""
    if not GEMINI_API_KEY:
        flash("Clé API Gemini non configurée. Configurez la variable GEMINI_API_KEY.", "danger")
        return redirect(url_for("index"))
    
    success, message = initialize_rag_system(GEMINI_API_KEY, force_reload=True)
    
    if success:
        flash(message, "success")
    else:
        flash(message, "danger")
    
    return redirect(url_for("ask_question"))


# Téléchargement CSV
@app.route("/download_csv", methods=["GET"])
def download_csv():
    path = "last_scrape.json"
    if not os.path.exists(path):
        flash("Aucun résultat disponible. Lance un scraping d'abord.", "warning")
        return redirect(url_for("index"))
    
    with open(path, "r", encoding="utf-8") as f:
        data_all = json.load(f)

    all_results = []
    for site_id, site_data in data_all.items():
        results = site_data.get("results", [])
        for r in results:
            r["site_id"] = site_id
            r["site_url"] = site_data.get("start_url", "")
            all_results.append(r)

    # Générer CSV en mémoire
    out = io.StringIO()
    writer = csv.writer(out)

    # En-tête étendu
    header = [
        "site_id", "site_url", "url", "title", "meta_description", "h1", 
        "first_paragraph", "excerpt", "word_count", "images_count", 
        "products_count", "footer_links_count", "depth", "error", "fetched_at"
    ]
    writer.writerow(header)

    for r in all_results:
        writer.writerow([
            r.get("site_id", ""),
            r.get("site_url", ""),
            r.get("url", ""),
            r.get("title", ""),
            r.get("meta_description", ""),
            r.get("h1", ""),
            r.get("first_paragraph", ""),
            r.get("excerpt", ""),
            r.get("word_count", 0),
            len(r.get("images", [])),
            len(r.get("products", [])),
            len(r.get("footer", {}).get("links", [])),
            r.get("depth", ""),
            r.get("error", ""),
            r.get("fetched_at", "")
        ])

    mem = io.BytesIO()
    mem.write(out.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="data.csv", mimetype="text/csv; charset=utf-8")


# Téléchargement produits CSV
@app.route("/download_products_csv", methods=["GET"])
def download_products_csv():
    path = "last_scrape.json"
    if not os.path.exists(path):
        flash("Aucun résultat disponible. Lance un scraping d'abord.", "warning")
        return redirect(url_for("index"))
    
    with open(path, "r", encoding="utf-8") as f:
        data_all = json.load(f)

    all_products = []
    for site_id, site_data in data_all.items():
        results = site_data.get("results", [])
        for page in results:
            for product in page.get("products", []):
                product["site_id"] = site_id
                product["site_url"] = site_data.get("start_url", "")
                product["page_url"] = page.get("url", "")
                all_products.append(product)

    # Générer CSV produits
    out = io.StringIO()
    writer = csv.writer(out)
    
    header = ["site_id", "site_url", "page_url", "name", "price", "description", "image", "product_url", "sku"]
    writer.writerow(header)
    
    for product in all_products:
        writer.writerow([
            product.get("site_id", ""),
            product.get("site_url", ""),
            product.get("page_url", ""),
            product.get("name", ""),
            product.get("price", ""),
            product.get("description", ""),
            product.get("image", ""),
            product.get("product_url", ""),
            product.get("sku", "")
        ])
    
    mem = io.BytesIO()
    mem.write(out.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(mem, as_attachment=True, download_name="products.csv", mimetype="text/csv; charset=utf-8")

@app.route("/check_rag_changes")
def check_rag_changes():
    """Vérifie si des changements sont détectés sans initialiser"""
    if not GEMINI_API_KEY:
        return {"error": "Clé API non configurée"}, 400
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY)
        stats = rag_system.get_stats()
        
        # Méthode simplifiée pour détecter les changements
        file_path = "last_scrape.json"
        has_changes = False
        reason = "unknown"
        
        if not os.path.exists(file_path):
            has_changes = True
            reason = "file_not_found"
        elif not stats.get('initialized', False):
            has_changes = True
            reason = "not_initialized"
        else:
            # Vérifier si le fichier a été modifié récemment
            file_mtime = os.path.getmtime(file_path)
            # Si le fichier a été modifié dans les dernières 24h, considérer qu'il y a des changements
            if time.time() - file_mtime < 86400:  # 24 heures
                has_changes = True
                reason = "recent_changes"
            else:
                has_changes = False
                reason = "no_changes"
        
        return {
            "has_changes": has_changes,
            "is_initialized": stats.get('initialized', False),
            "reason": reason,
            "can_answer_questions": stats.get('initialized', False)
        }
    except Exception as e:
        return {"error": str(e)}, 500



# Route de compatibilité
@app.route("/init_rag")
def init_rag():
    """Initialise le système RAG seulement si le scraping a changé"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)

        # Vérifie s'il y a des changements dans MongoDB
        change_info = rag_system.check_data_changes()

        if not change_info['has_changes']:
            flash("✅ Aucune modification détectée dans MongoDB — RAG déjà à jour.", "info")
            return redirect(url_for("index"))

        # Si des changements détectés, recharger les données
        rag_system.load_scraped_data()
        stats = rag_system.get_stats()
        flash(f"🎉 RAG réinitialisé : {stats['total_sites']} sites, {stats['total_products']} produits.", "success")
        return redirect(url_for("index"))

    except Exception as e:
        flash(f"❌ Erreur d'initialisation du RAG : {str(e)}", "danger")
        return redirect(url_for("index"))

@app.route("/ask", methods=["GET", "POST"])
def ask_question():
    if not GEMINI_API_KEY:
        flash("Clé API Gemini non configurée.", "danger")
        return redirect(url_for("ask_question"))
    
    if not GOOGLE_GENAI_AVAILABLE:
        flash("Bibliothèque google-genai non installée. Exécutez: pip install google-genai", "danger")
        return redirect(url_for("ask_question"))
    
    try:
            rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
            rag_stats = rag_system.get_stats()
            rag_initialized = rag_stats.get('initialized', False)
            
            if hasattr(rag_system, 'get_available_sites'):
                available_sites = rag_system.get_available_sites()
    except Exception as e:
        print(f"Erreur RAG: {e}")
    
    if request.method == "POST":
        # ✅ RÉCUPÉRATION DES DONNÉES DU FORMULAIRE (SEULEMENT RAG)
        question = request.form.get("question", "").strip()
        site_id = request.form.get("site_id", "").strip()
        
        print("=" * 60)
        print("🔍 DEBUG - DONNÉES RAG REÇUES:")
        print(f"Question: {question}")
        print(f"Site ID: {site_id}")
        print("=" * 60)
        
        if not question:
            flash("Veuillez poser une question.", "warning")
            return redirect(url_for("ask_question"))
        
        if not GEMINI_API_KEY:
            flash("Clé API Mistral non configurée.", "danger")
            return redirect(url_for("ask_question"))
        
        if not rag_initialized:
            flash("Système RAG non initialisé. Veuillez d'abord initialiser.", "warning")
            return redirect(url_for("ask_question"))
        
        try:
            rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
            
            # ✅ RÉPONSE RAG SEULEMENT
            print("🤖 Génération de la réponse RAG...")
            answer = rag_system.ask_question(question, site_id if site_id else None)
            print(f"✅ Réponse RAG générée: {len(answer)} caractères")
            
            # ✅ PROFIL CLIENT
            site_info = None
            if site_id and hasattr(rag_system, 'profile_manager'):
                site_info = rag_system.profile_manager.get_profile(site_id)
                print(f"👤 Profil client récupéré: {site_info.get('company_name') if site_info else 'Aucun'}")
            
            # ✅ RETOUR TEMPLATE (SANS IMAGES)
            return render_template("ask.html", 
                                question=question, 
                                answer=answer,
                                site_id=site_id,
                                site_info=site_info,
                                rag_initialized=rag_initialized,
                                rag_stats=rag_stats,
                                available_sites=available_sites,
                                leonardo_available=bool(LEONARDO_API_KEY))
            
        except Exception as e:
            error_msg = f"Erreur: {str(e)}"
            flash(error_msg, "danger")
            print(f"❌ ERREUR GLOBALE: {error_msg}")
            import traceback
            print("📋 TRACEBACK:")
            traceback.print_exc()
            return redirect(url_for("ask_question"))
    
    # ✅ GET - AFFICHER FORMULAIRE
    return render_template("ask.html", 
                          rag_initialized=rag_initialized,
                          rag_stats=rag_stats,
                          available_sites=available_sites,
                          leonardo_available=bool(LEONARDO_API_KEY))
    
@app.route("/rag_search", methods=["POST"])
def rag_search():
    """Endpoint pour rechercher dans le RAG sans génération LLM"""
    if not GEMINI_API_KEY:
        return {"error": "Clé API Mistral non configurée"}, 400
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY,mongo_client)
        stats = rag_system.get_stats()
        
        if not stats.get('initialized', False):
            return {"error": "Système RAG non initialisé"}, 400
        
        query = request.json.get("query", "").strip()
        k = request.json.get("k", 10)
        
        if not query:
            return {"error": "Query manquante"}, 400
        
        # Recherche avec FAISS
        results = rag_system.search(query, k=k)
        
        # Formater les résultats
        formatted_results = []
        for result in results:
            formatted_results.append({
                "document": result['document'],
                "score": result['score'],
                "metadata": result['metadata']
            })
        
        return {
            "query": query,
            "results_count": len(formatted_results),
            "results": formatted_results
        }
        
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/test_leonardo")
def test_leonardo():
    """Teste la connexion à Leonardo AI"""
    try:
        from leonardo_ai import LeonardoAIGenerator
        
        print("🧪 Test Leonardo AI...")
        leonardo = LeonardoAIGenerator(LEONARDO_API_KEY)
        
        # Test de connexion
        result = leonardo.test_connection()
        
        if result.get("success"):
            return f"✅ Leonardo AI connecté! Utilisateur: {result.get('user')}"
        else:
            return f"❌ Erreur connexion: {result.get('error')}"
            
    except Exception as e:
        return f"❌ Erreur: {str(e)}"


@app.route("/debug_rag_data")
def debug_rag_data():
    """Debug complet des données RAG"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        debug_info = {
            "rag_initialized": rag_system.is_initialized if rag_system else False,
            "has_raw_data": hasattr(rag_system, 'raw_data') and rag_system.raw_data is not None,
            "raw_data_keys": list(rag_system.raw_data.keys()) if hasattr(rag_system, 'raw_data') and rag_system.raw_data else [],
            "raw_data_count": len(rag_system.raw_data) if hasattr(rag_system, 'raw_data') and rag_system.raw_data else 0,
            "documents_count": len(rag_system.documents) if hasattr(rag_system, 'documents') else 0,
            "index_size": rag_system.index.ntotal if hasattr(rag_system, 'index') and rag_system.index else 0
        }
        
        return jsonify(debug_info)
        
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/force_reload_rag_data", methods=["POST"])
def force_reload_rag_data():
    """Force le rechargement des données brutes"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Réinitialiser raw_data
        rag_system.raw_data = {}
        
        # Recharger depuis MongoDB
        success = rag_system._load_raw_data_from_mongo()
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Données rechargées: {len(rag_system.raw_data)} sites",
                "sites": list(rag_system.raw_data.keys())
            })
        else:
            return jsonify({
                "success": False,
                "error": "Échec du rechargement"
            })
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/rag_status")
def rag_status():
    """Retourne le statut détaillé du système RAG avec info nouvelles données"""
    try:
        if not GEMINI_API_KEY:
            return jsonify({
                "status": "no_api_key",
                "message": "Clé API Gemini non configurée"
            })
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Vérifier si le système RAG est initialisé
        if not hasattr(rag_system, 'is_initialized') or not rag_system.is_initialized:
            return jsonify({
                "status": "not_initialized",
                "message": "Système RAG non initialisé"
            })
        
        # Obtenir les statistiques CORRECTES
        stats = rag_system.get_stats() if hasattr(rag_system, 'get_stats') else {}
        
        # ✅ CORRECTION: Vérifier aussi les sites dans MongoDB
        sites_in_mongo = list(scrapes_collection.find({}, {"site_id": 1}))
        mongo_site_count = len(sites_in_mongo)
        
        # Utiliser le maximum entre les stats RAG et MongoDB
        total_sites = max(stats.get('total_sites', 0), mongo_site_count)
        
        return jsonify({
            "status": "initialized",
            "has_new_data": False,  # Temporairement désactivé
            "new_sites_count": 0,
            "total_documents": stats.get('total_documents', 0),
            "total_products": stats.get('total_products', 0),
            "total_pages": stats.get('total_pages', 0),
            "total_sites": total_sites,  # ✅ CORRIGÉ
            "index_size": stats.get('index_size', 0),
            "reason": "initialized",
            "initialized": True
        })
    except Exception as e:
        print(f"❌ Erreur dans /rag_status: {e}")
        return jsonify({
            "status": "error", 
            "message": str(e),
            "initialized": False
        })
    
@app.route("/debug_rag")
def debug_rag():
    """Route de debug pour vérifier l'état du RAG"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        debug_info = {
            "gemini_api_key_configured": bool(GEMINI_API_KEY),
            "google_genai_available": GOOGLE_GENAI_AVAILABLE,
            "rag_system_created": rag_system is not None,
            "rag_initialized": hasattr(rag_system, 'is_initialized') and rag_system.is_initialized,
            "has_index": hasattr(rag_system, 'index') and rag_system.index is not None,
            "documents_count": len(rag_system.documents) if hasattr(rag_system, 'documents') else 0,
            "raw_data_count": len(rag_system.raw_data) if hasattr(rag_system, 'raw_data') else 0
        }
        
        return jsonify(debug_info)
        
    except Exception as e:
        return jsonify({"error": str(e)})    
   
   
@app.route("/rebuild_rag", methods=["POST"])
def rebuild_rag():
    """Force la reconstruction complète du RAG"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Nettoyer les anciens index
        rag_system.cleanup_old_index()
        
        # Réinitialiser l'état
        rag_system.is_initialized = False
        rag_system.index = None
        
        # Recharger complètement
        success = rag_system.load_scraped_data()
        
        if success:
            stats = rag_system.get_stats()
            flash(f"🔨 RAG reconstruit: {stats['total_sites']} sites, {stats['total_documents']} documents", "success")
        else:
            flash("❌ Échec de la reconstruction", "danger")
            
    except Exception as e:
        flash(f"❌ Erreur reconstruction: {str(e)}", "danger")
    
    return redirect(url_for("ask_question"))   

 
@app.route("/rebuild_index", methods=["POST"])
def rebuild_index():
    """Reconstruit l'index FAISS"""
    if not GEMINI_API_KEY:
        flash("Clé API Mistral non configurée.", "danger")
        return redirect(url_for("index"))
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY,mongo_client)
        
        # Recharger les données et reconstruire l'index
        rag_system.load_scraped_data()
        
        stats = rag_system.get_stats()
        flash(f"Index FAISS reconstruit avec succès! {stats['index_size']} vecteurs indexés.", "success")
        
    except Exception as e:
        flash(f"Erreur lors de la reconstruction: {str(e)}", "danger")
    
    return redirect(url_for("ask_question"))

@app.route("/save_index", methods=["POST"])
def save_index():
    """Sauvegarde l'index FAISS sur disque"""
    if not GEMINI_API_KEY:
        return {"error": "Clé API non configurée"}, 400
    
    try:
        import faiss
        rag_system = get_rag_system(GEMINI_API_KEY)
        
        if rag_system.index is None:
            return {"error": "Aucun index à sauvegarder"}, 400
        
        # Sauvegarder l'index FAISS
        faiss.write_index(rag_system.index, "faiss_index.bin")
        
        # Sauvegarder les métadonnées
        with open("faiss_metadata.json", "w", encoding="utf-8") as f:
            json.dump({
                "documents": rag_system.documents,
                "metadata": rag_system.metadata
            }, f, ensure_ascii=False, indent=2)
        
        flash("Index FAISS sauvegardé avec succès!", "success")
        return {"status": "success", "index_size": rag_system.index.ntotal}
        
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/rag_debug")
def rag_debug():
    """Debug du système RAG"""
    if not GEMINI_API_KEY:
        flash("Clé API Mistral non configurée.", "danger")
        return redirect(url_for("ask_question"))
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY,mongo_client)
        
        # Vérifier si la méthode debug_indexation existe
        if hasattr(rag_system, 'debug_indexation'):
            rag_system.debug_indexation()
        
        stats = rag_system.get_stats()
        
        return render_template("rag_debug.html", 
                             stats=stats,
                             rag_initialized=stats.get('initialized', False))
        
    except Exception as e:
        flash(f"Erreur lors du debug: {str(e)}", "danger")
        return redirect(url_for("ask_question"))
    

@app.route("/load_index", methods=["POST"])
def load_index():
    """Charge l'index FAISS depuis le disque"""
    if not GEMINI_API_KEY:
        flash("Clé API Mistral non configurée.", "danger")
        return redirect(url_for("index"))
    
    try:
        import faiss
        rag_system = get_rag_system(GEMINI_API_KEY)
        
        if not os.path.exists("faiss_index.bin") or not os.path.exists("faiss_metadata.json"):
            flash("Aucun index sauvegardé trouvé. Initialisez d'abord le RAG.", "warning")
            return redirect(url_for("ask_question"))
        
        # Charger l'index FAISS
        rag_system.index = faiss.read_index("faiss_index.bin")
        
        # Charger les métadonnées
        with open("faiss_metadata.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            rag_system.documents = data["documents"]
            rag_system.metadata = data["metadata"]
        
        # Charger raw_data si nécessaire
        if os.path.exists("last_scrape.json"):
            with open("last_scrape.json", "r", encoding="utf-8") as f:
                rag_system.raw_data = json.load(f)
        
        flash(f"Index FAISS chargé avec succès! {rag_system.index.ntotal} vecteurs.", "success")
        
    except Exception as e:
        flash(f"Erreur lors du chargement: {str(e)}", "danger")
    
    return redirect(url_for("ask_question"))

 
#Route pour l'agent de community management

@app.route("/marketing_analysis", methods=["GET", "POST"])
def marketing_analysis():
    """Analyse marketing approfondie"""
    if request.method == "GET":
        # Afficher le formulaire d'analyse
        return render_template("marketing_analysis.html")
    
    # POST - Traiter l'analyse
    question = request.form.get("question", "").strip()
    
    if not question:
        flash("Veuillez poser une question marketing.", "warning")
        return redirect(url_for("marketing_analysis"))
    
    if not GEMINI_API_KEY:
        flash("Clé API Mistral non configurée.", "danger")
        return redirect(url_for("marketing_analysis"))
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY,mongo_client)
        
        # Utiliser la méthode spécialisée marketing
        if hasattr(rag_system, 'generate_marketing_response'):
            response = rag_system.generate_marketing_response(question)
        else:
            # Fallback vers la méthode standard
            response = rag_system.ask_question(question)
        
        return render_template("marketing_analysis.html", 
                            question=question, 
                            response=response,
                            rag_initialized=True)
        
    except Exception as e:
        flash(f"Erreur lors de l'analyse marketing: {str(e)}", "danger")
        return redirect(url_for("marketing_analysis"))

@app.route("/content_calendar", methods=["GET", "POST"])
def content_calendar():
    """Génère un calendrier de contenu unique"""
    if request.method == "GET":
        return render_template("content_calendar_form.html")
    
    if not GEMINI_API_KEY:
        flash("Clé API Mistral non configurée.", "danger")
        return redirect(url_for("content_calendar"))
    
    try:
        rag_system = get_rag_system(GEMINI_API_KEY,mongo_client)
        duration = int(request.form.get("duration", 7))
        
        if not hasattr(rag_system, 'cm_agent') or rag_system.cm_agent is None:
            rag_system.cm_agent = CommunityManagerAgent(rag_system)
        
        products_data = []
        
        # Chargement des données avec logging
        try:
            all_sites = list(scrapes_collection.find())
            print(f"📊 Sites trouvés dans MongoDB: {len(all_sites)}")
            
            for site_doc in all_sites:
                results = site_doc.get("results", [])
                print(f"📄 Pages dans le site: {len(results)}")
                
                for page in results:
                    # Produits normaux
                    for product in page.get('products', []):
                        if isinstance(product, dict):
                            product['is_promoted'] = False
                            products_data.append(product)
                    # Produits promus
                    for product in page.get('promoted_products', []):
                        if isinstance(product, dict):
                            product['is_promoted'] = True
                            products_data.append(product)
                            
            print(f"✅ Produits chargés depuis MongoDB: {len(products_data)}")
            
        except Exception as e:
            print(f"❌ Erreur MongoDB: {e}")
        
        # Fallback
        if not products_data and os.path.exists("last_scrape.json"):
            try:
                with open("last_scrape.json", "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    print(f"📁 Fichier last_scrape.json chargé: {len(raw_data)} sites")
                    
                    for site_id, site_data in raw_data.items():
                        if isinstance(site_data, dict):
                            results = site_data.get('results', [])
                            for page in results:
                                if isinstance(page, dict):
                                    for product in page.get('products', []):
                                        if isinstance(product, dict):
                                            product['is_promoted'] = False
                                            products_data.append(product)
                                    for product in page.get('promoted_products', []):
                                        if isinstance(product, dict):
                                            product['is_promoted'] = True
                                            products_data.append(product)
                print(f"✅ Produits chargés depuis JSON: {len(products_data)}")
            except Exception as e:
                print(f"❌ Erreur JSON: {e}")
        
        if not products_data:
            flash("❌ Aucune donnée produit trouvée. Effectuez d'abord un scraping.", "warning")
            return redirect(url_for("content_calendar"))
        
        print(f"🎯 Génération calendrier avec {len(products_data)} produits...")
        calendar = rag_system.cm_agent.generate_content_calendar(products_data, duration)
        
        flash(f"✅ Calendrier généré! {duration} jours, {len(products_data)} produits", "success")
        return render_template("content_calendar.html", 
                            calendar=calendar,
                            duration=duration)
        
    except Exception as e:
        print(f"❌ Erreur génération calendrier: {e}")
        import traceback
        print(f"🔍 Détails: {traceback.format_exc()}")
        flash(f"Erreur lors de la génération du calendrier: {str(e)}", "danger")
        return redirect(url_for("content_calendar"))


@app.route("/generate_automated_marketing_package", methods=["POST"])
def generate_automated_marketing_package():
    """Génère un package marketing complet automatisé avec Gemini"""
    try:
        data = request.get_json()
        site_id = data.get("site_id", "").strip()
        
        if not site_id:
            return jsonify({"success": False, "error": "Site ID requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # 1. Générer les ad-copy avec Gemini
        ad_copy_result = rag_system.generate_automated_ad_copy(site_id)
        if not ad_copy_result["success"]:
            return jsonify(ad_copy_result)
        
        # 2. Générer le calendrier avec Gemini
        calendar_result = rag_system.generate_automated_calendar(site_id)
        
        return jsonify({
            "success": True,
            "generated_by": "gemini_automation",
            "ad_copy": ad_copy_result,
            "calendar": calendar_result if calendar_result["success"] else None,
            "summary": f"Package marketing généré automatiquement pour {ad_copy_result['company_context']}"
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route("/generate_automated_ad_copy", methods=["POST"])
def generate_automated_ad_copy():
    """Génère automatiquement un ad-copy basé sur le profil client"""
    try:
        site_id = request.form.get("site_id", "").strip()
        product_context = request.form.get("product_context", "").strip()
        tone = request.form.get("tone", "professional")
        style = request.form.get("style", "marketing")
        
        if not site_id:
            return jsonify({"success": False, "error": "Site ID requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        result = rag_system.generate_automated_ad_copy(site_id, product_context, tone, style)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/generate_automated_calendar", methods=["POST"])
def generate_automated_calendar():
    """Génère un calendrier de contenu complet automatisé"""
    try:
        site_id = request.form.get("site_id", "").strip()
        duration_weeks = int(request.form.get("duration_weeks", 2))
        posts_per_week = int(request.form.get("posts_per_week", 3))
        include_images = request.form.get("include_images", "true") == "true"
        
        if not site_id:
            return jsonify({"success": False, "error": "Site ID requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        result = rag_system.generate_automated_calendar(site_id, duration_weeks, posts_per_week, include_images)
        
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/generate_calendar_with_images", methods=["POST"])
def generate_calendar_with_images():
    """Génère un calendrier complet avec images IA"""
    try:
        data = request.json
        site_id = data.get("site_id", "").strip()
        duration_weeks = data.get("duration_weeks", 2)
        posts_per_week = data.get("posts_per_week", 3)
        
        if not site_id:
            return jsonify({"success": False, "error": "Site ID requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Générer le calendrier
        calendar_result = rag_system.generate_automated_calendar(site_id, duration_weeks, posts_per_week, True)
        
        if not calendar_result["success"]:
            return jsonify(calendar_result)
        
        # Générer les images pour chaque post
        generated_images = []
        calendar_with_images = calendar_result["calendar"].copy()
        
        for week_key, week_data in calendar_with_images.items():
            for day_key, day_posts in week_data["days"].items():
                for post in day_posts:
                    try:
                        # Générer l'image pour ce post
                        image_result = generate_image_for_post(post, site_id)
                        if image_result["success"]:
                            post["generated_image"] = image_result["image_url"]
                            generated_images.append({
                                "post_number": post["post_number"],
                                "image_url": image_result["image_url"],
                                "image_prompt": post["image_prompt"]
                            })
                    except Exception as e:
                        print(f"❌ Erreur génération image pour post {post['post_number']}: {e}")
                        post["generated_image"] = None
        
        calendar_result["generated_images"] = generated_images
        calendar_result["calendar_with_images"] = calendar_with_images
        
        return jsonify(calendar_result)
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

def generate_image_for_post(post: Dict, site_id: str) -> Dict:
    """Génère une image pour un post de calendrier"""
    try:
        if not LEONARDO_API_KEY:
            return {"success": False, "error": "Clé Leonardo AI non configurée"}
        
        from leonardo_ai import LeonardoAIGenerator
        leonardo = LeonardoAIGenerator(LEONARDO_API_KEY)
        
        # Récupérer le contexte client
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        client_context = None
        site_info = rag_system.profile_manager.get_profile(site_id)
        if site_info:
            client_context = {
                'industry': site_info.get('industry', ''),
                'brand_voice': site_info.get('brand_voice', ''),
                'brand_values': site_info.get('brand_values', [])
            }
        
        # Générer l'image
        result = leonardo.generate_without_reference(
            ad_copy=post["image_prompt"],
            tone=post["tone"],
            client_context=client_context,
            style=post["style"]
        )
        
        if result.get("success") and result.get("images"):
            return {
                "success": True,
                "image_url": result["images"][0],
                "generation_time": result.get("generation_time", 0)
            }
        else:
            return {"success": False, "error": result.get("error", "Erreur inconnue")}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.route("/generate_marketing_image", methods=["POST"])
def generate_marketing_image():
    """Route dédiée à la génération d'images - AVEC ou SANS URL de référence"""
    
    # Récupération des données
    question = request.form.get("question", "").strip()
    site_id = request.form.get("site_id", "").strip()
    ad_copy = request.form.get("ad_copy", "").strip()  # C'est le PROMPT pour l'image
    tone = request.form.get("tone", "professional")
    style = request.form.get("style", "marketing")
    image_strength = float(request.form.get("image_strength", 0.5))
    use_reference = request.form.get("use_reference") == "true"
    reference_image_url = request.form.get("reference_image_url", "").strip()
    
    print("🎨 GÉNÉRATION D'IMAGE - DEBUG")
    print(f"Prompt image: {ad_copy}")
    print(f"Use reference: {use_reference}")
    print(f"Reference URL: {reference_image_url}")
    
    # Validation
    if not LEONARDO_API_KEY:
        flash("❌ Clé API Leonardo AI non configurée", "danger")
        return redirect(url_for("ask_question"))
    
    if not ad_copy:
        flash("❌ Veuillez entrer un prompt pour l'image", "danger")
        return redirect(url_for("ask_question"))
    
    if use_reference and not reference_image_url:
        flash("❌ Case 'Utiliser référence' cochée mais aucune URL d'image fournie", "warning")
        return redirect(url_for("ask_question"))
    
    try:
        # Initialisation Leonardo
        from leonardo_ai import LeonardoAIGenerator
        leonardo = LeonardoAIGenerator(LEONARDO_API_KEY)
        
        # Contexte client
        client_context = None
        if site_id:
            rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
            site_info = rag_system.profile_manager.get_profile(site_id)
            if site_info:
                client_context = {
                    'industry': site_info.get('industry', ''),
                    'brand_voice': site_info.get('brand_voice', ''),
                    'brand_values': site_info.get('brand_values', [])
                }
        
        # Choix de la méthode
        if use_reference and reference_image_url:
            print("🔄 Génération AVEC référence (méthode avancée)...")
            result = leonardo.generate_with_reference_image_advanced(
                image_url=reference_image_url,
                prompt=ad_copy,  # Le prompt pour l'image
                tone=tone,
                client_context=client_context,
                style=style
            )
        else:
            print("🔄 Génération SANS référence...")
            result = leonardo.generate_without_reference(
                ad_copy=ad_copy,  # Le prompt pour l'image
                tone=tone,
                client_context=client_context,
                style=style
            )
        
        # Traitement du résultat
        if result and result.get("success"):
            generated_images = result.get("images", [])
            generation_time = result.get("generation_time", 0)
            
            method_text = "avec référence" if use_reference else "sans référence"
            flash(f"✅ Image générée {method_text} avec succès! ({generation_time}s)", "success")
            
            # Retourner le template avec les images
            return render_template("ask.html", 
                                generated_images=generated_images,
                                ad_copy=ad_copy,
                                question=question,
                                answer=None,
                                site_id=site_id,
                                site_info=site_info,
                                tone=tone,
                                style=style,
                                image_strength=image_strength,
                                use_reference=use_reference,
                                reference_image_url=reference_image_url,
                                leonardo_available=True)
        else:
            error_msg = f"❌ Erreur génération: {result.get('error', 'Erreur inconnue')}"
            flash(error_msg, "danger")
            return redirect(url_for("ask_question"))
            
    except Exception as e:
        error_msg = f"❌ Erreur Leonardo AI: {str(e)}"
        flash(error_msg, "danger")
        return redirect(url_for("ask_question"))
    
@app.route("/generation_history")
def generation_history():
    """Affiche l'historique des générations depuis MongoDB"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        stats = rag_system.history_manager.get_generation_stats()
        
        # Récupérer les dernières générations
        recent_generations = list(mongo_db.generation_history.find()
                                 .sort('timestamp', -1)
                                 .limit(20))
        
        return render_template("generation_history.html",
                             stats=stats,
                             generations=recent_generations)
        
    except Exception as e:
        flash(f"Erreur lors de la récupération de l'historique: {str(e)}", "danger")
        return redirect(url_for("index"))
    
# Ajouter ces routes après les routes existantes
@app.route("/test_replicate")
def test_replicate():
    """Teste la connexion à Replicate"""
    try:
        video_gen = VideoGenerator(REPLICATE_API_KEY)
        result = video_gen.test_connection()
        
        if result.get("success"):
            return f"✅ Replicate connecté! {result.get('message')}"
        else:
            return f"❌ Erreur connexion: {result.get('error')}"
            
    except Exception as e:
        return f"❌ Erreur: {str(e)}"

@app.route("/video_generation", methods=["GET", "POST"])
def video_generation():
    """Page de génération de vidéo marketing"""
    if request.method == "GET":
        return render_template("video_generation.html")
    
    # POST - Génération de vidéo
    try:
        # Récupération des données du formulaire
        site_id = request.form.get("site_id", "").strip()
        product_image_url = request.form.get("product_image_url", "").strip()
        tone = request.form.get("tone", "professional")
        social_media = request.form.get("social_media", "instagram")
        product_description = request.form.get("product_description", "").strip()
        
        # Validation
        if not product_image_url:
            flash("❌ URL de l'image produit requise", "danger")
            return redirect(url_for("video_generation"))
        
        if not REPLICATE_API_KEY:
            flash("❌ Clé API Replicate non configurée", "danger")
            return redirect(url_for("video_generation"))
        
        # Récupération du contexte client
        client_context = {}
        if site_id:
            rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
            site_info = rag_system.profile_manager.get_profile(site_id)
            if site_info:
                client_context = {
                    'industry': site_info.get('industry', ''),
                    'brand_voice': site_info.get('brand_voice', ''),
                    'company_name': site_info.get('company_name', ''),
                    'domain': site_info.get('domain', '')
                }
        
        # Génération de la vidéo
        video_gen = VideoGenerator(REPLICATE_API_KEY)
        result = video_gen.generate_product_video(
            product_image_url=product_image_url,
            client_context=client_context,
            tone=tone,
            social_media=social_media,
            product_description=product_description
        )
        
        if result.get("success"):
            flash(f"✅ Vidéo générée avec succès pour {social_media}!", "success")
            return render_template("video_generation.html",
                                video_result=result,
                                site_id=site_id,
                                tone=tone,
                                social_media=social_media,
                                product_description=product_description)
        else:
            flash(f"❌ Erreur: {result.get('error')}", "danger")
            return redirect(url_for("video_generation"))
            
    except Exception as e:
        flash(f"❌ Erreur lors de la génération: {str(e)}", "danger")
        return redirect(url_for("video_generation"))

@app.route("/video_models")
def video_models():
    """Retourne les modèles vidéo disponibles"""
    try:
        video_gen = VideoGenerator(REPLICATE_API_KEY)
        models = video_gen.get_available_models()
        return jsonify({"models": models})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/generate_ad_copy", methods=["POST"])
def generate_ad_copy():
    """Génère un ad-copy avec Gemini basé sur le contexte du site ou produit"""
    try:
        site_id = request.form.get("site_id", "").strip()
        product_context = request.form.get("product_context", "").strip()
        tone = request.form.get("tone", "professional")
        style = request.form.get("style", "marketing")
        
        if not site_id and not product_context:
            return jsonify({"success": False, "error": "Site ID ou contexte produit requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Construire le prompt selon le contexte
        if site_id:
            # Utiliser les données du site scrapé
            site_info = rag_system.profile_manager.get_profile(site_id)
            if site_info:
                prompt = f"""
Génère un ad-copy marketing percutant pour {site_info.get('company_name', 'ce site')} qui est dans l'industrie {site_info.get('industry', '')}.

Ton: {tone}
Style: {style}
Public cible: {', '.join(site_info.get('target_audience', {}).get('demographics', []))}
Positionnement: {site_info.get('market_position', '')}

Crée un message accrocheur, persuasif et adapté aux réseaux sociaux (max 150 caractères).
"""
            else:
                return jsonify({"success": False, "error": "Profil site non trouvé"})
        else:
            # Utiliser le contexte produit fourni
            prompt = f"""
Génère un ad-copy marketing percutant basé sur cette description: {product_context}

Ton: {tone}
Style: {style}

Crée un message accrocheur, persuasif et adapté aux réseaux sociaux (max 150 caractères).
"""
        
        # Générer avec Gemini
        response = rag_system.generate_response(prompt)
        
        # Nettoyer la réponse
        ad_copy = response.strip().replace('"', '').split('\n')[0]
        if len(ad_copy) > 150:
            ad_copy = ad_copy[:147] + "..."
        
        return jsonify({
            "success": True,
            "ad_copy": ad_copy,
            "tone": tone,
            "style": style
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/sites")
def api_sites():
    """Retourne la liste des sites scrapés au format JSON"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        sites = rag_system.get_available_sites() if hasattr(rag_system, 'get_available_sites') else []
        return jsonify({"sites": sites})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ✅ AJOUTER DANS app5.py - Route pour DEBUG
@app.route("/debug_gemini_models", methods=["GET"])
def debug_gemini_models():
    """Debug les modèles disponibles"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        models = []
        if GOOGLE_GENAI_AVAILABLE:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            try:
                # Lister les modèles
                response = client.models.list()
                for model in response:
                    models.append({
                        "name": model.name,
                        "display_name": getattr(model, 'display_name', 'N/A'),
                        "input_token_limit": getattr(model, 'input_token_limit', 'N/A')
                    })
            except Exception as e:
                return jsonify({
                    "error": str(e),
                    "hint": "Vérifiez votre clé API Gemini"
                }), 400
        
        return jsonify({
            "available_models": models,
            "count": len(models),
            "recommended": "models/gemini-2.0-flash-exp ou models/gemini-2.0-flash"
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/available_sites", methods=["GET"])
def api_available_sites():
    """Retourne la liste des sites disponibles en JSON"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        sites = rag_system.get_available_sites()
        return jsonify({"success": True, "sites": sites})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/debug_mongo_data")
def debug_mongo_data():
    """Debug complet des données MongoDB"""
    try:
        # Vérifier les sites dans MongoDB
        sites_in_mongo = list(scrapes_collection.find({}))
        mongo_info = []
        
        for site in sites_in_mongo:
            site_id = site.get("site_id")
            results = site.get("results", [])
            total_products = 0
            total_promoted = 0
            
            for page in results:
                total_products += len(page.get("products", []))
                total_promoted += len(page.get("promoted_products", []))
            
            mongo_info.append({
                "site_id": site_id,
                "start_url": site.get("start_url"),
                "pages_count": len(results),
                "total_products": total_products,
                "total_promoted": total_promoted,
                "scraped_at": site.get("scraped_at")
            })
        
        # Vérifier l'état du RAG
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        rag_stats = rag_system.get_stats() if rag_system else {}
        
        return jsonify({
            "mongo_sites": mongo_info,
            "rag_stats": rag_stats,
            "mongo_total_sites": len(mongo_info),
            "rag_initialized": rag_stats.get('initialized', False)
        })
        
    except Exception as e:
        return jsonify({"error": str(e)})



@app.route("/emergency_reload_rag", methods=["GET", "POST"])
def emergency_reload_rag():
    """Rechargement d'urgence du RAG depuis MongoDB"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        print("🚨 REINITIALISATION URGENCE DU RAG...")
        
        # Réinitialiser complètement
        rag_system.is_initialized = False
        rag_system.index = None
        rag_system.documents = []
        rag_system.metadata = []
        rag_system.raw_data = {}
        
        # Nettoyer les anciens index
        rag_system.cleanup_old_index()
        
        print("📥 Chargement des données depuis MongoDB...")
        
        # Charger directement depuis MongoDB
        success = rag_system.load_scraped_data()
        
        if success:
            stats = rag_system.get_stats()
            message = f"✅ RAG réinitialisé avec succès! {stats['total_sites']} sites, {stats['total_documents']} documents, {stats['total_products']} produits"
            print(message)
            return jsonify({
                "success": True,
                "message": message,
                "stats": stats
            })
        else:
            error_msg = "❌ Échec du rechargement des données"
            print(error_msg)
            return jsonify({
                "success": False,
                "error": error_msg
            })
            
    except Exception as e:
        error_msg = f"❌ Erreur lors de la réinitialisation: {str(e)}"
        print(error_msg)
        import traceback
        print(f"🔍 Détails: {traceback.format_exc()}")
        return jsonify({"success": False, "error": error_msg})
    
    
@app.route("/generate_automated_image_prompt", methods=["POST"])
def generate_automated_image_prompt():
    """Génère automatiquement un prompt d'image basé sur le profil client"""
    try:
        data = request.get_json()
        site_id = data.get("site_id", "").strip()
        product_context = data.get("product_context", "").strip()
        
        if not site_id:
            return jsonify({"success": False, "error": "Site ID requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Récupérer le profil client
        site_info = rag_system.profile_manager.get_profile(site_id)
        if not site_info:
            return jsonify({"success": False, "error": "Profil client non trouvé"})
        
        # Générer le prompt d'image avec Gemini
        prompt = f"""
En tant qu'expert en création visuelle marketing, génère un prompt détaillé pour une IA image (comme Leonardo AI) basé sur ce profil client:

PROFIL CLIENT:
- Entreprise: {site_info.get('company_name', 'Inconnu')}
- Industrie: {site_info.get('industry', 'Inconnu')}
- Type de business: {site_info.get('business_type', 'Inconnu')}
- Positionnement: {site_info.get('market_position', 'Standard')}
- Audience: {', '.join(site_info.get('target_audience', {}).get('demographics', []))}
- Style de marque: {site_info.get('brand_voice', 'Professionnel')}

CONTEXTE PRODUIT: {product_context}

Génère un prompt d'image IA qui:
1. Correspond au style de la marque
2. Est optimisé pour les réseaux sociaux
3. Met en valeur les produits/services
4. Inclut des éléments visuels attractifs
5. Suit les tendances actuelles du design

Format de réponse:
PROMPT_IMAGE: [ton prompt détaillé ici]
STYLE_RECOMMANDE: [style visuel recommandé]
COULEURS: [palette de couleurs suggérée]
"""

        response = rag_system.generate_response(prompt)
        
        # Extraire le prompt de la réponse
        lines = response.split('\n')
        image_prompt = ""
        for line in lines:
            if line.startswith("PROMPT_IMAGE:"):
                image_prompt = line.replace("PROMPT_IMAGE:", "").strip()
                break
        
        if not image_prompt:
            image_prompt = response.strip()
        
        return jsonify({
            "success": True,
            "image_prompt": image_prompt,
            "company_context": site_info.get('company_name'),
            "industry": site_info.get('industry')
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/generate_contextual_ad_copy", methods=["POST"])
def generate_contextual_ad_copy():
    """Génère un ad-copy contextuel basé sur le profil client et le prompt d'image"""
    try:
        data = request.get_json()
        site_id = data.get("site_id", "").strip()
        image_prompt = data.get("image_prompt", "").strip()
        post_type = data.get("post_type", "general")
        
        if not site_id or not image_prompt:
            return jsonify({"success": False, "error": "Site ID et prompt image requis"})
        
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        # Récupérer le profil client
        site_info = rag_system.profile_manager.get_profile(site_id)
        if not site_info:
            return jsonify({"success": False, "error": "Profil client non trouvé"})
        
        # Générer l'ad-copy avec Gemini
        prompt = f"""
En tant qu'expert en copywriting marketing, génère un ad-copy percutant pour accompagner une image IA.

PROFIL CLIENT:
- Entreprise: {site_info.get('company_name', 'Inconnu')}
- Industrie: {site_info.get('industry', 'Inconnu')}
- Voice de marque: {site_info.get('brand_voice', 'Professionnel')}
- Audience: {', '.join(site_info.get('target_audience', {}).get('demographics', []))}

DESCRIPTION DE L'IMAGE: {image_prompt}
TYPE DE POST: {post_type}

Crée un ad-copy qui:
1. Est cohérent avec l'image
2. Correspond à la voice de la marque
3. Engage l'audience cible
4. Inclut un call-to-action
5. Optimisé pour {post_type}

Formats requis:
- Version courte (150 caractères max)
- Version longue (300 caractères max)
- Hashtags pertinents

Réponds uniquement avec le format JSON suivant:
{{
    "short_copy": "texte court",
    "long_copy": "texte long", 
    "hashtags": ["#tag1", "#tag2"],
    "cta": "call to action"
}}
"""

        response = rag_system.generate_response(prompt)
        
        # Essayer de parser la réponse JSON
        try:
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                ad_data = json.loads(json_match.group())
            else:
                # Fallback si pas de JSON valide
                ad_data = {
                    "short_copy": response[:150],
                    "long_copy": response[:300],
                    "hashtags": ["#marketing", "#innovation"],
                    "cta": "Découvrez maintenant !"
                }
        except:
            ad_data = {
                "short_copy": response[:150],
                "long_copy": response[:300],
                "hashtags": ["#marketing", "#innovation"],
                "cta": "Découvrez maintenant !"
            }
        
        return jsonify({
            "success": True,
            "ad_copy": ad_data,
            "post_type": post_type
        })
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route('/generate_complete_calendar', methods=['POST'])
def generate_complete_calendar_route():
    """
    🚀 NOUVELLE ROUTE: Génération automatique complète avec cohérence ad-copy/images
    """
    try:
        data = request.get_json()
        site_id = data.get('site_id', '').strip()
        duration_weeks = int(data.get('duration_weeks', 2))
        posts_per_week = int(data.get('posts_per_week', 3))
        
        if not site_id:
            return jsonify({'success': False, 'error': 'Site ID requis'}), 400
        
        # Vérifier que RAG est initialisé
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        if not rag_system.is_initialized:
            return jsonify({
                'success': False, 
                'error': 'RAG non initialisé. Veuillez d\'abord initialiser le système.'
            }), 400
        
        # Récupérer le profil client
        site_info = rag_system.profile_manager.get_profile(site_id)
        if not site_info:
            return jsonify({
                'success': False,
                'error': 'Profil client non trouvé'
            }), 404
        
        # Vérifier Leonardo AI
        if not LEONARDO_API_KEY:
            return jsonify({
                'success': False,
                'error': 'Leonardo AI non configuré. Images ne pourront pas être générées.'
            }), 400
        
        print(f"🚀 Génération calendrier cohérent pour {site_info.get('company_name')}")
        
        # Initialiser le générateur Leonardo
        from leonardo_ai import LeonardoAIGenerator
        leonardo = LeonardoAIGenerator(LEONARDO_API_KEY)
        
        # Créer le générateur de calendrier amélioré
        calendar_generator = ImprovedCalendarGenerator(
            rag_system=rag_system,
            site_id=site_id,
            site_info=site_info,
            image_generator=leonardo
        )
        
        # Générer le calendrier complet
        result = calendar_generator.generate_complete_calendar(
            duration_weeks=duration_weeks,
            posts_per_week=posts_per_week
        )
        
        if result.get('success'):
            # Nettoyer les données MongoDB avant envoi
            calendar_data = clean_mongo_data(result['calendar'])
            stats = result['stats']
            
            print(f"✅ Calendrier généré: {stats['total_posts']} posts, {stats['images_generated']} images")
            
            return jsonify({
                'success': True,
                'calendar': calendar_data,
                'stats': stats,
                'message': f"Calendrier généré avec succès! {stats['images_generated']}/{stats['total_posts']} images créées."
            })
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Erreur inconnue')
            }), 500
            
    except Exception as e:
        print(f"❌ Erreur génération calendrier cohérent: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def generate_automated_image_prompt_internal(site_id, product_context, site_info):
    """Génère un prompt d'image en interne (sans route API)"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        prompt = f"""
Crée un prompt détaillé pour une IA image (Leonardo AI) pour ce contexte:

PROFIL CLIENT:
- Entreprise: {site_info.get('company_name', 'Inconnu')}
- Industrie: {site_info.get('industry', 'Inconnu')}
- Voice: {site_info.get('brand_voice', 'Professionnel')}

CONTEXTE: {product_context}

Génère un prompt concis et efficace qui décrit:
- Style visuel (moderne, professionnel, créatif)
- Composition (plan large, gros plan, etc.)
- Éclairage et ambiance
- Éléments clés à inclure

Le prompt doit être en français et faire 1-2 phrases maximum.
"""
        
        response = rag_system.generate_response(prompt)
        return response.strip()
        
    except Exception as e:
        print(f"❌ Erreur génération prompt interne: {e}")
        return f"Image marketing professionnelle pour {product_context}"

def generate_contextual_ad_copy_internal(site_id, image_prompt, post_type, site_info):
    """Génère un ad-copy en interne (sans route API)"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        
        prompt = f"""
Crée un ad-copy pour réseaux sociaux basé sur cette image: {image_prompt}

PROFIL:
- Entreprise: {site_info.get('company_name')}
- Industrie: {site_info.get('industry')}
- Type de post: {post_type}

Génère au format JSON:
{{
    "short_copy": "Texte court (120 caractères max)",
    "long_copy": "Texte détaillé (250 caractères max)",
    "hashtags": ["#tag1", "#tag2", "#tag3"],
    "cta": "Call to action"
}}
"""
        
        response = rag_system.generate_response(prompt)
        
        # Parser la réponse
        try:
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
        except:
            pass
        
        # Fallback
        return {
            "short_copy": f"Découvrez notre {post_type} ! 🌟",
            "long_copy": f"Ne manquez pas cette opportunité exclusive. {site_info.get('company_name')} vous propose le meilleur du {site_info.get('industry', 'secteur')}.",
            "hashtags": ["#marketing", "#innovation", site_info.get('industry', '').replace(' ', '')],
            "cta": "En savoir plus ➡️"
        }
        
    except Exception as e:
        print(f"❌ Erreur génération ad-copy interne: {e}")
        return {
            "short_copy": "Contenu exclusif ! 🚀",
            "long_copy": "Découvrez notre nouveau contenu spécialement créé pour vous.",
            "hashtags": ["#marketing", "#content", "#socialmedia"],
            "cta": "Découvrir maintenant"
        }

def create_fallback_strategy(duration_weeks, posts_per_week, site_info):
    """Crée une stratégie de fallback si Gemini échoue"""
    days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    content_types = ["education", "promotion", "inspiration", "engagement"]
    times = ["9:00", "12:00", "15:00", "18:00", "21:00"]
    
    weeks = []
    post_counter = 1
    
    for week_num in range(1, duration_weeks + 1):
        week = {
            "week_number": week_num,
            "theme": f"Semaine {week_num} - {site_info.get('industry', 'Marketing')}",
            "days": []
        }
        
        for day_idx in range(posts_per_week):
            if post_counter > duration_weeks * posts_per_week:
                break
                
            day_data = {
                "day": days[day_idx % len(days)],
                "post_number": post_counter,
                "theme": f"Thème {post_counter} - {site_info.get('industry', 'Expertise')}",
                "content_type": content_types[post_counter % len(content_types)],
                "creative_angle": f"Angle créatif {post_counter}",
                "marketing_goal": f"Objectif {post_counter}",
                "visual_idea": f"Image {site_info.get('industry', 'professionnelle')} {post_counter}",
                "best_time": times[post_counter % len(times)]
            }
            
            week["days"].append(day_data)
            post_counter += 1
        
        weeks.append(week)
    
    return {"weeks": weeks}

@app.route("/debug_profile/<site_id>")
def debug_profile(site_id):
    """Debug d'un profil client"""
    try:
        rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
        profile = rag_system.profile_manager.get_profile(site_id)
        
        if profile:
            cleaned_profile = clean_mongo_data(profile)
            return jsonify({
                "success": True,
                "profile": cleaned_profile,
                "raw_types": {k: str(type(v)) for k, v in profile.items()}
            })
        else:
            return jsonify({"success": False, "error": "Profil non trouvé"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})
    
# ==========================================
# 🛡️ MIDDLEWARE D'AUTHENTIFICATION
# ==========================================

def login_required(f):
    """Décorateur pour vérifier l'authentification"""
    from functools import wraps
    
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash('Veuillez vous connecter d\'abord', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    
    return decorated_function
# ============================================
# 🎨 DASHBOARD DE TESTING
# ============================================
@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    """Affiche le dashboard de test du backend"""
    onboarding_completed = session.get('onboarding_processed', False)
    user_data = {
        'name': session.get('user_name'),
        'email': session.get('user_email'),
        'picture': session.get('user_picture')
    }
    
    return render_template("dashboard.html", 
                         playwright_available=PLAYWRIGHT_AVAILABLE,
                         leonardo_available=bool(LEONARDO_API_KEY),
                         replicate_available=bool(REPLICATE_API_KEY),
                         onboarding_completed=onboarding_completed,
                         user_data=user_data)


# ============================================
# 🚀 FORMULAIRE EN 4 ÉTAPES - VERSION SIMPLIFIÉE
# ============================================

def get_onboarding_data():
    """Récupère les données d'onboarding"""
    return session.get('onboarding_data', {})

def set_onboarding_data(data):
    """Définit les données d'onboarding"""
    session['onboarding_data'] = data

def clear_onboarding_data():
    """Efface les données d'onboarding"""
    session.pop('onboarding_data', None)
    session.pop('onboarding_status', None)
    session.pop('onboarding_processed', None)

@app.route("/onboarding", methods=["GET"])
@login_required
def onboarding():
    """Page d'accueil du formulaire en 4 étapes"""
    # Réinitialiser les données d'onboarding
    clear_onboarding_data()
    
    # Ajouter l'user_id aux données d'onboarding
    user_data = {
        'user_id': session.get('user_id'),
        'user_email': session.get('user_email'),
        'user_name': session.get('user_name')
    }
    set_onboarding_data(user_data)
    
    return render_template("onboarding.html")

@app.route("/onboarding/sector", methods=["GET", "POST"])
@login_required
def onboarding_sector():
    """Étape 1: Choix du secteur"""
    if request.method == "POST":
        sector = request.form.get("sector")
        if not sector:
            flash("Veuillez sélectionner un secteur", "warning")
            return redirect(url_for("onboarding_sector"))
        
        # Stocker en session
        onboarding_data = get_onboarding_data()
        onboarding_data.update({
            'sector': sector,
            'step': 1,
            'started_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        set_onboarding_data(onboarding_data)
        
        return redirect(url_for("onboarding_social"))
    
    return render_template("onboarding_sector.html")

@app.route("/onboarding/social", methods=["GET", "POST"])
def onboarding_social():
    """Étape 2: Connexion réseaux sociaux"""
    # Vérifier que l'étape 1 est complétée
    onboarding_data = get_onboarding_data()
    if not onboarding_data.get('sector'):
        flash("Veuillez d'abord compléter l'étape 1", "warning")
        return redirect(url_for("onboarding_sector"))
    
    if request.method == "POST":
        # Mettre à jour l'étape
        onboarding_data.update({
            'step': 2
        })
        set_onboarding_data(onboarding_data)
        return redirect(url_for("onboarding_sources"))
    
    # Passer les données au template
    return render_template("onboarding_social.html", onboarding_data=onboarding_data)

@app.route("/onboarding/sources", methods=["GET", "POST"])
def onboarding_sources():
    """Étape 3: URL du site"""
    # Vérifier que l'étape 2 est complétée
    onboarding_data = get_onboarding_data()
    if onboarding_data.get('step', 0) < 1:
        flash("Veuillez d'abord compléter les étapes précédentes", "warning")
        return redirect(url_for("onboarding_sector"))
    
    if request.method == "POST":
        website_url = request.form.get("website_url", "").strip()
        
        # Validation de l'URL
        if not website_url:
            flash("Veuillez fournir une URL de site web", "danger")
            return redirect(url_for("onboarding_sources"))
        
        # Normaliser l'URL
        if not website_url.startswith(('http://', 'https://')):
            website_url = 'https://' + website_url
        
        # Test d'accessibilité basique
        try:
            response = requests.head(website_url, timeout=10, allow_redirects=True)
            if response.status_code >= 400:
                flash(f"Le site semble inaccessible (code {response.status_code}) - le scraping sera tenté quand même", "warning")
        except Exception as e:
            flash(f"Attention: Impossible de vérifier l'accès au site - le scraping sera tenté quand même", "warning")
        
        onboarding_data.update({
            'website_url': website_url,
            'step': 3,
            'website_validated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        set_onboarding_data(onboarding_data)
        
        return redirect(url_for("onboarding_identity"))
    
    return render_template("onboarding_sources.html")

@app.route("/onboarding/identity", methods=["GET", "POST"])
def onboarding_identity():
    """Étape 4: Identité visuelle"""
    # Vérifier que l'étape 3 est complétée
    onboarding_data = get_onboarding_data()
    if not onboarding_data.get('website_url'):
        flash("Veuillez d'abord compléter l'étape 3", "warning")
        return redirect(url_for("onboarding_sources"))
    
    if request.method == "POST":
        # Gérer l'upload du logo
        logo_file = request.files.get("logo")
        primary_color = request.form.get("primary_color", "#4361ee")
        secondary_color = request.form.get("secondary_color", "#3a0ca3")
        
        logo_filename = None
        if logo_file and logo_file.filename:
            try:
                # Créer le dossier uploads s'il n'existe pas
                upload_dir = os.path.join('static', 'uploads', 'logos')
                os.makedirs(upload_dir, exist_ok=True)
                
                # Générer un nom de fichier sécurisé
                from werkzeug.utils import secure_filename
                filename = secure_filename(logo_file.filename)
                logo_path = os.path.join(upload_dir, filename)
                logo_file.save(logo_path)
                logo_filename = f"uploads/logos/{filename}"
            except Exception as e:
                flash(f"Erreur lors de l'upload du logo: {str(e)}", "warning")
        
        onboarding_data.update({
            'logo': logo_filename,
            'primary_color': primary_color,
            'secondary_color': secondary_color,
            'step': 4,
            'completed_at': time.strftime("%Y-%m-%d %H:%M:%S"),
            'completed': True
        })
        set_onboarding_data(onboarding_data)
        
        
        
        user_id = session.get('user_id')
        if user_id:
            from bson import ObjectId
            users_collection.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': {'onboarding_completed': True}}
            )
        # ✅ CORRIGER: Passer les données nécessaires à la fonction
        # au lieu de les accéder via session dans le thread
        threading.Thread(
            target=process_onboarding_data_corrected,
            args=(onboarding_data.copy(),),  # ✅ Passer une COPIE des données
            daemon=True
        ).start()
        
        return redirect(url_for("dashboard_loading"))
    
    return render_template("onboarding_identity.html")

def process_onboarding_data_corrected(onboarding_data):
    """
    Traite les données d'onboarding en arrière-plan
    ✅ Les données sont passées EN PARAMÈTRE, pas accédées via session
    """
    try:
        # ✅ Pas besoin d'accéder à session - on a les données en paramètre
        website_url = onboarding_data.get('website_url')
        
        if not website_url:
            store_onboarding_status("error")
            return
        
        print(f"🚀 Lancement du scraping pour {website_url}")
        
        # Simulation du traitement
        steps = [
            "Scraping du site web...",
            "Analyse du contenu...", 
            "Extraction des produits...",
            "Génération de la stratégie...",
            "Préparation du dashboard..."
        ]
        
        for i, step in enumerate(steps, 1):
            print(f"📋 Étape {i}/5: {step}")
            time.sleep(2)
            
            # Stocker la progression
            progress_data = {
                'current_step': i,
                'total_steps': len(steps),
                'step_name': step,
                'progress_percent': (i / len(steps)) * 100
            }
            store_onboarding_progress(progress_data)
        
        # Ajouter les données simulées
        import random
        
        onboarding_data.update({
            'scraped_pages': random.randint(10, 25),
            'products_found': random.randint(30, 60),
            'social_connected': sum([
                1 if onboarding_data.get('facebook_connected') else 0,
                1 if onboarding_data.get('instagram_connected') else 0
            ]),
            'analysis_completed_at': time.strftime("%Y-%m-%d %H:%M:%S"),
            'content_calendar_generated': True,
            'marketing_strategy_ready': True,
            'ai_images_generated': random.randint(5, 15)
        })
        
        # ✅ Sauvegarder les données mises à jour
        # Utiliser un fichier JSON au lieu de session
        with open('onboarding_data_temp.json', 'w', encoding='utf-8') as f:
            json.dump(onboarding_data, f, ensure_ascii=False, indent=2)
        
        # Marquer comme terminé
        store_onboarding_status("completed")
        print("✅ Traitement onboarding terminé avec succès")
        
    except Exception as e:
        print(f"❌ Erreur lors du traitement onboarding: {e}")
        import traceback
        print(f"📌 Détails: {traceback.format_exc()}")
        store_onboarding_status("error")


@app.route("/dashboard/loading")
def dashboard_loading():
    """Page de chargement pendant le traitement"""
    onboarding_data = get_onboarding_data()
    if not onboarding_data.get('completed'):
        flash("Veuillez d'abord compléter le processus d'onboarding", "warning")
        return redirect(url_for("onboarding"))
    
    return render_template("dashboard_loading.html")



def store_onboarding_status(status):
    """Stocke le statut dans un fichier JSON"""
    try:
        status_file = "onboarding_status.json"
        data = {
            'status': status,
            'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        print(f"📝 Statut sauvegardé: {status}")
        
    except Exception as e:
        print(f"❌ Erreur sauvegarde statut: {e}")

def store_onboarding_progress(progress_data):
    """Stocke la progression dans un fichier JSON"""
    try:
        progress_file = "onboarding_progress.json"
        
        with open(progress_file, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
            
    except Exception as e:
        print(f"❌ Erreur sauvegarde progression: {e}")

def get_onboarding_status_from_file():
    """Récupère le statut depuis le fichier"""
    try:
        status_file = "onboarding_status.json"
        if os.path.exists(status_file):
            with open(status_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('status', 'processing')
        return 'processing'
        
    except Exception as e:
        print(f"❌ Erreur lecture statut: {e}")
        return 'processing'

def get_onboarding_progress_from_file():
    """Récupère la progression depuis le fichier"""
    try:
        progress_file = "onboarding_progress.json"
        if os.path.exists(progress_file):
            with open(progress_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
        
    except Exception as e:
        print(f"❌ Erreur lecture progression: {e}")
        return {}


def process_onboarding_data():
    """Traite les données d'onboarding en arrière-plan"""
    try:
        # Utiliser le contexte de l'application
        with app.app_context():
            onboarding_data = get_onboarding_data()
            website_url = onboarding_data.get('website_url')
            
            if not website_url:
                store_onboarding_status("error")
                return
            
            print(f"🚀 Lancement du scraping pour {website_url}")
            
            # Simulation du traitement
            steps = [
                "Scraping du site web...",
                "Analyse du contenu...", 
                "Extraction des produits...",
                "Génération de la stratégie...",
                "Préparation du dashboard..."
            ]
            
            for i, step in enumerate(steps, 1):
                print(f"📋 Étape {i}/5: {step}")
                time.sleep(2)
                
                # Stocker la progression
                progress_data = {
                    'current_step': i,
                    'total_steps': len(steps),
                    'step_name': step,
                    'progress_percent': (i / len(steps)) * 100
                }
                store_onboarding_progress(progress_data)
            
            # Ajouter les données simulées
            import random
            
            onboarding_data.update({
                'scraped_pages': random.randint(10, 25),
                'products_found': random.randint(30, 60),
                'social_connected': sum([
                    1 if onboarding_data.get('facebook_connected') else 0,
                    1 if onboarding_data.get('instagram_connected') else 0
                ]),
                'analysis_completed_at': time.strftime("%Y-%m-%d %H:%M:%S"),
                'content_calendar_generated': True,
                'marketing_strategy_ready': True,
                'ai_images_generated': random.randint(5, 15)
            })
            
            set_onboarding_data(onboarding_data)
            
            # Marquer comme terminé
            store_onboarding_status("completed")
            print("✅ Traitement onboarding terminé avec succès")
            
    except Exception as e:
        print(f"❌ Erreur lors du traitement onboarding: {e}")
        store_onboarding_status("error")

@app.route("/onboarding/summary")
def onboarding_summary():
    """Page de récapitulatif avec possibilité de modification"""
    onboarding_data = get_onboarding_data()
    
    # Vérifier que l'onboarding est complété
    if not onboarding_data.get('completed'):
        flash("Veuillez d'abord compléter le processus d'onboarding", "warning")
        return redirect(url_for("onboarding"))
    
    return render_template("onboarding_summary.html", onboarding_data=onboarding_data)

@app.route("/onboarding/update", methods=["POST"])
def onboarding_update():
    """Met à jour les données d'onboarding"""
    try:
        onboarding_data = get_onboarding_data()
        
        # Mettre à jour les données
        onboarding_data.update({
            'sector': request.form.get('sector'),
            'website_url': request.form.get('website_url'),
            'primary_color': request.form.get('primary_color', '#4361ee'),
            'secondary_color': request.form.get('secondary_color', '#3a0ca3'),
            'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        
        set_onboarding_data(onboarding_data)
        flash("Configuration mise à jour avec succès!", "success")
        
    except Exception as e:
        flash(f"Erreur lors de la mise à jour: {str(e)}", "error")
    
    return redirect(url_for("onboarding_summary"))


# ✅ ALTERNATIVE: Utiliser un stockage persistant pour les données
@app.route("/onboarding/status")
def onboarding_status():
    """Retourne le statut du traitement"""
    status = get_onboarding_status_from_file()
    progress = get_onboarding_progress_from_file()
    
    # Charger les données mises à jour depuis le fichier temporaire
    onboarding_data_updated = {}
    if os.path.exists('onboarding_data_temp.json'):
        try:
            with open('onboarding_data_temp.json', 'r', encoding='utf-8') as f:
                onboarding_data_updated = json.load(f)
        except:
            pass
    
    return jsonify({
        'status': status,
        'progress': progress,
        'onboarding_data': onboarding_data_updated  # ✅ Retourner les données mises à jour
    })
    
@app.route("/onboarding/reset")
def onboarding_reset():
    """Réinitialise l'onboarding"""
    clear_onboarding_data()
    flash("Configuration réinitialisée", "info")
    return redirect(url_for("onboarding"))

@app.route("/onboarding/data")
def onboarding_data():
    """Debug: Affiche les données d'onboarding (à supprimer en production)"""
    return jsonify({
        'onboarding_data': get_onboarding_data(),
        'onboarding_status': session.get('onboarding_status'),
        'onboarding_processed': session.get('onboarding_processed')
    })

# ============================================
# 🔐 ROUTES AUTHENTIFICATION RÉSEAUX SOCIAUX
# ============================================

@app.route('/auth/facebook')
def auth_facebook():
    """Démarre l'authentification Facebook"""
    redirect_uri = url_for('auth_facebook_callback', _external=True)
    return facebook.authorize_redirect(redirect_uri)

@app.route('/auth/facebook/callback')
def auth_facebook_callback():
    """Callback Facebook OAuth"""
    try:
        token = facebook.authorize_access_token()
        if not token:
            flash('Erreur d\'authentification Facebook', 'error')
            return redirect(url_for('onboarding_social'))
        
        # Récupérer les informations utilisateur
        resp = facebook.get('me?fields=id,name,email,picture')
        user_info = resp.json()
        
        # Récupérer les pages Facebook
        pages_resp = facebook.get('me/accounts')
        pages_info = pages_resp.json()
        
        # Stocker dans la session
        onboarding_data = get_onboarding_data()
        onboarding_data.update({
            'facebook_connected': True,
            'facebook_user_id': user_info.get('id'),
            'facebook_name': user_info.get('name'),
            'facebook_email': user_info.get('email'),
            'facebook_picture': user_info.get('picture', {}).get('data', {}).get('url'),
            'facebook_pages': pages_info.get('data', []),
            'facebook_access_token': token.get('access_token'),
            'facebook_connected_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        set_onboarding_data(onboarding_data)
        
        flash('Facebook connecté avec succès!', 'success')
        return redirect(url_for('onboarding_social'))
        
    except Exception as e:
        flash(f'Erreur de connexion Facebook: {str(e)}', 'error')
        return redirect(url_for('onboarding_social'))

@app.route('/auth/facebook/callback_js', methods=['POST'])
def auth_facebook_callback_js():
    """Traite les données Facebook envoyées par le SDK JavaScript"""
    try:
        data = request.get_json()
        user_data = data.get('user', {})
        pages_data = data.get('pages', {})
        
        # Stocker dans la session
        onboarding_data = get_onboarding_data()
        onboarding_data.update({
            'facebook_connected': True,
            'facebook_user_id': user_data.get('id'),
            'facebook_name': user_data.get('name'),
            'facebook_email': user_data.get('email'),
            'facebook_picture': user_data.get('picture', {}).get('data', {}).get('url'),
            'facebook_pages': pages_data.get('data', []),
            'facebook_connected_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        set_onboarding_data(onboarding_data)
        
        return jsonify({
            'success': True,
            'message': f'Facebook connecté avec succès! Bienvenue {user_data.get("name")}'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Erreur de traitement: {str(e)}'
        }), 500


@app.route('/auth/instagram')
def auth_instagram():
    """Démarre l'authentification Instagram"""
    redirect_uri = url_for('auth_instagram_callback', _external=True)
    return oauth.instagram.authorize_redirect(redirect_uri)

@app.route('/auth/instagram/callback')
def auth_instagram_callback():
    """Callback Instagram OAuth"""
    try:
        token = oauth.instagram.authorize_access_token()
        if not token:
            flash('Erreur d\'authentification Instagram', 'error')
            return redirect(url_for('onboarding_social'))
        
        # Récupérer les informations utilisateur
        user_resp = oauth.instagram.get('me?fields=id,username,account_type,media_count')
        user_info = user_resp.json()
        
        # Récupérer les médias
        media_resp = oauth.instagram.get('me/media?fields=id,caption,media_type,media_url,permalink,timestamp')
        media_info = media_resp.json()
        
        # Stocker dans la session
        onboarding_data = get_onboarding_data()
        onboarding_data.update({
            'instagram_connected': True,
            'instagram_user_id': user_info.get('id'),
            'instagram_username': user_info.get('username'),
            'instagram_account_type': user_info.get('account_type'),
            'instagram_media_count': user_info.get('media_count'),
            'instagram_recent_media': media_info.get('data', []),
            'instagram_access_token': token.get('access_token'),
            'instagram_connected_at': time.strftime("%Y-%m-%d %H:%M:%S")
        })
        set_onboarding_data(onboarding_data)
        
        flash('Instagram connecté avec succès!', 'success')
        return redirect(url_for('onboarding_social'))
        
    except Exception as e:
        flash(f'Erreur de connexion Instagram: {str(e)}', 'error')
        return redirect(url_for('onboarding_social'))

@app.route('/auth/disconnect/<platform>')
def auth_disconnect(platform):
    """Déconnecte un réseau social"""
    onboarding_data = get_onboarding_data()
    
    if platform == 'facebook':
        keys_to_remove = [k for k in onboarding_data.keys() if k.startswith('facebook_')]
        for key in keys_to_remove:
            onboarding_data.pop(key, None)
        flash('Facebook déconnecté', 'info')
    
    elif platform == 'instagram':
        keys_to_remove = [k for k in onboarding_data.keys() if k.startswith('instagram_')]
        for key in keys_to_remove:
            onboarding_data.pop(key, None)
        flash('Instagram déconnecté', 'info')
    
    set_onboarding_data(onboarding_data)
    return redirect(url_for('onboarding_social'))


# ==========================================
# 🔑 ROUTES D'AUTHENTIFICATION
# ==========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Page de connexion"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        
        if not email or not password:
            flash('Email et mot de passe requis', 'danger')
            return redirect(url_for('login'))
        
        user = get_user_by_email(email)
        
        if not user:
            flash('Email ou mot de passe incorrect', 'danger')
            return redirect(url_for('login'))
        
        # Vérifier le mot de passe
        if not user.get('password'):
            flash('Veuillez utiliser Google pour vous connecter', 'info')
            return redirect(url_for('login'))
        
        if not check_password_hash(user['password'], password):
            flash('Email ou mot de passe incorrect', 'danger')
            return redirect(url_for('login'))
        
        # Connexion réussie
        session['user_id'] = user['_id']
        session['user_email'] = user['email']
        session['user_name'] = user['name']
        session['user_picture'] = user.get('picture', '')
        
        update_last_login(user['_id'])
        
        flash(f'Bienvenue {user["name"]}!', 'success')
        
        # ✅ CORRECTION : Vérifier si l'onboarding est complété
        if not user.get('onboarding_completed', False):
            return redirect(url_for('onboarding'))
        else:
            return redirect(url_for('dashboard'))
    
    return render_template('login.html')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    """Page d'inscription"""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        password_confirm = request.form.get('password_confirm', '')
        name = request.form.get('name', '').strip()
        
        # Validations
        if not email or not password or not name:
            flash('Tous les champs sont requis', 'danger')
            return redirect(url_for('signup'))
        
        if len(password) < 8:
            flash('Le mot de passe doit contenir au moins 8 caractères', 'danger')
            return redirect(url_for('signup'))
        
        if password != password_confirm:
            flash('Les mots de passe ne correspondent pas', 'danger')
            return redirect(url_for('signup'))
        
        # Vérifier si l'email existe
        if get_user_by_email(email):
            flash('Cet email est déjà utilisé', 'danger')
            return redirect(url_for('signup'))
        
        # Créer l'utilisateur
        result = create_user(
            email=email,
            password=password,
            name=name,
            provider='email'
        )
        
        if result['success']:
            flash('Inscription réussie! Vous pouvez maintenant vous connecter.', 'success')
            return redirect(url_for('login'))
        else:
            flash(f"Erreur: {result['error']}", 'danger')
            return redirect(url_for('signup'))
    
    return render_template('signup.html')


@app.route('/auth/google')
def auth_google():
    """Lance l'authentification Google"""
    redirect_uri = url_for('auth_google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def auth_google_callback():
    """Callback Google OAuth"""
    try:
        token = google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            flash('Erreur authentification Google', 'danger')
            return redirect(url_for('login'))
        
        email = user_info.get('email')
        name = user_info.get('name')
        picture = user_info.get('picture')
        google_id = user_info.get('sub')
        
        # Chercher ou créer l'utilisateur
        user = get_user_by_email(email)
        
        if not user:
            # Créer un nouvel utilisateur
            result = create_user(
                email=email,
                name=name,
                picture=picture,
                provider='google',
                provider_id=google_id
            )
            
            if not result['success']:
                flash(f"Erreur création compte: {result['error']}", 'danger')
                return redirect(url_for('login'))
            
            user_id = result['user_id']
            is_new_user = True
        else:
            user_id = user['_id']
            is_new_user = False
            
            # Mettre à jour les infos Google si nécessaire
            if not user.get('picture') and picture:
                users_collection.update_one(
                    {'email': email},
                    {'$set': {'picture': picture}}
                )
        
        # Connexion réussie
        session['user_id'] = user_id
        session['user_email'] = email
        session['user_name'] = name
        session["user_picture"] = picture
        
        update_last_login(user_id)
        
        # ✅ CORRECTION : Récupérer l'utilisateur complet pour vérifier l'onboarding
        user_check = get_user_by_id(user_id)
        
        if is_new_user:
            flash(f'Bienvenue {name}! Complétez votre configuration.', 'success')
            return redirect(url_for('onboarding'))
        else:
            flash(f'Bienvenue {name}!', 'success')
            # ✅ Si l'onboarding n'est pas complété, rediriger vers onboarding
            if user_check and not user_check.get('onboarding_completed', False):
                return redirect(url_for('onboarding'))
            # ✅ Sinon, vers le dashboard
            return redirect(url_for('dashboard'))
        
    except Exception as e:
        print(f"❌ Erreur callback Google: {e}")
        flash(f'Erreur authentification: {str(e)}', 'danger')
        return redirect(url_for('login'))


@app.route('/logout')
def logout():
    """Déconnexion"""
    session.clear()
    flash('Vous avez été déconnecté', 'info')
    return redirect(url_for('login'))



# ==========================================
# 📊 API POUR VÉRIFIER L'AUTHENTIFICATION
# ==========================================

@app.route('/api/auth/status')
def auth_status():
    """Retourne le statut d'authentification"""
    if 'user_id' not in session:
        return jsonify({'authenticated': False})
    
    return jsonify({
        'authenticated': True,
        'user_id': session.get('user_id'),
        'user_name': session.get('user_name'),
        'user_email': session.get('user_email'),
        'user_picture': session.get('user_picture')
    })


@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    """Déconnexion via API"""
    session.clear()
    return jsonify({'success': True, 'message': 'Déconnecté'})

# Ajouter après mongo_db = mongo_client["scraping_db"]
whatsapp_messages_collection = mongo_db["whatsapp_messages"]
whatsapp_conversations_collection = mongo_db["whatsapp_conversations"]


# ==========================================
# 🤖 FONCTION POUR GÉNÉRER LES RÉPONSES
# ==========================================

def generate_whatsapp_response(user_message, user_id):
    """
    Génère une réponse automatique pour le chat WhatsApp
    Utilise Gemini pour les réponses intelligentes
    """
    try:
        # Récupérer le contexte utilisateur
        user = get_user_by_id(user_id)
        if not user:
            return "Erreur: Utilisateur non trouvé"
        
        user_name = user.get('name', 'Utilisateur')
        
        # Utiliser Gemini pour générer une réponse intelligente
        if GOOGLE_GENAI_AVAILABLE and GEMINI_API_KEY:
            rag_system = get_rag_system(GEMINI_API_KEY, mongo_client)
            
            # Prompt contextualisé
            prompt = f"""
            Tu es un assistant support client pour Marketing AI.
            Utilisateur: {user_name}
            Message: {user_message}
            
            Réponds de manière professionnelle mais amicale en moins de 200 caractères.
            Utilise des emojis appropriés.
            Si c'est une question sur nos services, sois utile et clair.
            """
            
            response = rag_system.generate_response(prompt)
            return response.strip()
        
        else:
            # Réponses par défaut si Gemini n'est pas disponible
            return generate_default_response(user_message)
    
    except Exception as e:
        print(f"❌ Erreur génération réponse: {e}")
        return "Désolé, je n'ai pas pu traiter votre message. Réessayez."


def generate_default_response(message):
    """
    Génère une réponse par défaut basée sur des mots-clés
    """
    responses = {
        'bonjour': '🙋‍♂️ Bonjour! Comment puis-je vous aider?',
        'salut': '👋 Salut! Qu\'est-ce que je peux faire pour vous?',
        'aide': '💪 Je suis là pour vous aider! Que souhaitez-vous faire?',
        'scraping': '🌐 Pour scraper un site:\n1. Allez dans "Scraping & RAG"\n2. Entrez l\'URL\n3. Lancez l\'analyse',
        'calendrier': '📅 Pour générer un calendrier:\n1. Sélectionnez un site\n2. Définissez la durée\n3. Générez les posts',
        'image': '🎨 Pour générer des images:\n1. Décrivez votre image\n2. Choisissez le style\n3. Générez en secondes',
        'prix': '💰 Pour connaître nos tarifs, contactez: sales@marketingai.com',
        'contact': '☎️ Contact: support@marketingai.com | +33 1 XX XX XX',
        'merci': '😊 De rien! Avez-vous d\'autres questions?',
        'oui': '✅ Excellent! Que puis-je faire pour vous?',
        'non': '❌ D\'accord! Y a-t-il autre chose?',
    }
    
    message_lower = message.lower()
    for keyword, response in responses.items():
        if keyword in message_lower:
            return response
    
    return '🤔 Interesting! Comment puis-je vous aider davantage?'


# ==========================================
# 📝 ROUTES API WHATSAPP
# ==========================================

@app.route('/api/whatsapp/message', methods=['POST'])
@login_required
def whatsapp_message_api():
    """
    Reçoit un message du chat WhatsApp et retourne une réponse
    """
    try:
        data = request.get_json()
        user_message = data.get('message', '').strip()
        user_id = session.get('user_id')
        
        if not user_message:
            return jsonify({
                'success': False,
                'error': 'Message vide'
            }), 400
        
        # Sauvegarder le message utilisateur
        whatsapp_messages_collection.insert_one({
            'user_id': user_id,
            'message': user_message,
            'direction': 'user',
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
            'read': True
        })
        
        # Générer la réponse
        response = generate_whatsapp_response(user_message, user_id)
        
        # Sauvegarder la réponse bot
        whatsapp_messages_collection.insert_one({
            'user_id': user_id,
            'message': response,
            'direction': 'bot',
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
            'read': False
        })
        
        # Sauvegarder la conversation
        whatsapp_conversations_collection.update_one(
            {'user_id': user_id},
            {
                '$set': {
                    'last_message': response,
                    'last_message_at': time.strftime("%Y-%m-%d %H:%M:%S"),
                    'unread_count': 0
                },
                '$inc': {'message_count': 2}
            },
            upsert=True
        )
        
        return jsonify({
            'success': True,
            'response': response,
            'timestamp': time.strftime("%H:%M")
        })
    
    except Exception as e:
        print(f"❌ Erreur API WhatsApp: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/whatsapp/history', methods=['GET'])
@login_required
def whatsapp_history():
    """
    Retourne l'historique des messages WhatsApp
    """
    try:
        user_id = session.get('user_id')
        limit = request.args.get('limit', 50, type=int)
        
        messages = list(
            whatsapp_messages_collection.find(
                {'user_id': user_id}
            ).sort('_id', -1).limit(limit)
        )
        
        # Convertir les ObjectId en string
        for msg in messages:
            msg['_id'] = str(msg['_id'])
        
        # Inverser pour avoir chronologie correcte
        messages.reverse()
        
        return jsonify({
            'success': True,
            'messages': messages,
            'count': len(messages)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/whatsapp/status', methods=['GET'])
@login_required
def whatsapp_status():
    """
    Retourne le statut du chat WhatsApp (messages non lus, etc.)
    """
    try:
        user_id = session.get('user_id')
        
        # Compter les messages non lus
        unread = whatsapp_messages_collection.count_documents({
            'user_id': user_id,
            'direction': 'bot',
            'read': False
        })
        
        # Dernier message
        last_msg = whatsapp_messages_collection.find_one(
            {'user_id': user_id},
            sort=[('_id', -1)]
        )
        
        return jsonify({
            'success': True,
            'unread_count': unread,
            'is_available': is_support_available(),
            'last_message': last_msg['message'] if last_msg else None,
            'last_message_at': last_msg['timestamp'] if last_msg else None
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/whatsapp/mark-read', methods=['POST'])
@login_required
def whatsapp_mark_read():
    """
    Marque tous les messages bot comme lus
    """
    try:
        user_id = session.get('user_id')
        
        whatsapp_messages_collection.update_many(
            {'user_id': user_id, 'direction': 'bot'},
            {'$set': {'read': True}}
        )
        
        # Mettre à jour la conversation
        whatsapp_conversations_collection.update_one(
            {'user_id': user_id},
            {'$set': {'unread_count': 0}}
        )
        
        return jsonify({'success': True})
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/whatsapp/clear', methods=['POST'])
@login_required
def whatsapp_clear():
    """
    Efface l'historique des messages WhatsApp
    """
    try:
        user_id = session.get('user_id')
        
        whatsapp_messages_collection.delete_many({'user_id': user_id})
        whatsapp_conversations_collection.delete_one({'user_id': user_id})
        
        return jsonify({'success': True})
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==========================================
# 🟢 FONCTION UTILITAIRE
# ==========================================

def is_support_available():
    """
    Vérifie si le support est disponible
    """
    now = datetime.datetime.now()
    day = now.weekday()  # 0 = Lundi, 6 = Dimanche
    hour = now.hour
    
    # Fermé dimanche (6) et samedi (5)
    if day >= 5:
        return False
    
    # Ouvert de 9h à 18h
    return 9 <= hour < 18


# ==========================================
# 📊 ROUTE POUR LES ANALYTICS
# ==========================================

@app.route('/api/analytics', methods=['POST'])
@login_required
def analytics_track():
    """
    Enregistre les événements d'analytics
    """
    try:
        data = request.get_json()
        event = data.get('event')
        event_data = data.get('data', {})
        user_id = session.get('user_id')
        
        analytics_collection = mongo_db["analytics"]
        
        analytics_collection.insert_one({
            'user_id': user_id,
            'event': event,
            'data': event_data,
            'timestamp': time.strftime("%Y-%m-%d %H:%M:%S"),
            'user_agent': request.headers.get('User-Agent')
        })
        
        return jsonify({'success': True})
    
    except Exception as e:
        print(f"❌ Erreur analytics: {e}")
        return jsonify({'success': False}), 500


# ==========================================
# 📲 ROUTE POUR ENVOYER PAR WHATSAPP
# ==========================================

@app.route('/api/whatsapp/send-external', methods=['POST'])
@login_required
def whatsapp_send_external():
    """
    Envoie un message via WhatsApp Web ou API
    Ouvre le lien WhatsApp Web
    """
    try:
        data = request.get_json()
        phone = data.get('phone', '+33123456789')
        message = data.get('message', 'Bonjour, j\'aurais une question...')
        
        # Nettoyer le numéro
        phone = ''.join(filter(str.isdigit, phone))
        
        # Créer l'URL WhatsApp
        whatsapp_url = f"https://wa.me/{phone}?text={urllib.parse.quote(message)}"
        
        return jsonify({
            'success': True,
            'url': whatsapp_url,
            'message': 'Cliquez pour ouvrir WhatsApp'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==========================================
# 📋 ROUTE POUR LES STATISTIQUES
# ==========================================

@app.route('/api/whatsapp/stats', methods=['GET'])
@login_required
def whatsapp_stats():
    """
    Retourne les statistiques du chat WhatsApp
    """
    try:
        user_id = session.get('user_id')
        
        # Compter les messages
        total_messages = whatsapp_messages_collection.count_documents(
            {'user_id': user_id}
        )
        
        user_messages = whatsapp_messages_collection.count_documents(
            {'user_id': user_id, 'direction': 'user'}
        )
        
        bot_messages = whatsapp_messages_collection.count_documents(
            {'user_id': user_id, 'direction': 'bot'}
        )
        
        # Récupérer l'info de conversation
        conversation = whatsapp_conversations_collection.find_one(
            {'user_id': user_id}
        )
        
        return jsonify({
            'success': True,
            'total_messages': total_messages,
            'user_messages': user_messages,
            'bot_messages': bot_messages,
            'conversation_started_at': conversation.get('_id') if conversation else None,
            'last_message_at': conversation.get('last_message_at') if conversation else None
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==========================================
# 🔍 FONCTION DE VALIDATION
# ==========================================

def allowed_file(filename, file_type='image'):
    """Vérifie si un fichier est autorisé"""
    if '.' not in filename:
        return False
    
    ext = filename.rsplit('.', 1)[1].lower()
    
    if file_type == 'image':
        return ext in ALLOWED_IMAGES
    elif file_type == 'video':
        return ext in ALLOWED_VIDEOS
    
    return False


def get_file_size_mb(filepath):
    """Obtient la taille du fichier en MB"""
    try:
        size_bytes = os.path.getsize(filepath)
        size_mb = size_bytes / (1024 * 1024)
        return round(size_mb, 2)
    except:
        return 0


def get_file_info(filepath, filename):
    """Obtient les informations du fichier"""
    try:
        size_mb = get_file_size_mb(filepath)
        ext = filename.rsplit('.', 1)[1].lower()
        mime_type, _ = mimetypes.guess_type(filepath)
        
        return {
            'filename': filename,
            'extension': ext,
            'size_mb': size_mb,
            'mime_type': mime_type or 'unknown',
            'upload_date': time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        print(f"❌ Erreur info fichier: {e}")
        return None


# ==========================================
# 📤 ROUTES UPLOAD IMAGES
# ==========================================

@app.route('/api/upload/image', methods=['POST'])
@login_required
def upload_image():
    """Upload an image"""
    try:
        user_id = session.get('user_id')

        # Check if file exists
        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'No image file found'}), 400
        
        file = request.files['image']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400
        
        # Check extension
        if not allowed_file(file.filename, 'image'):
            return jsonify({
                'success': False,
                'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_IMAGES)}'
            }), 400
        
        # Check size (max 50MB)
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        if file_length > 50 * 1024 * 1024:
            return jsonify({'success': False, 'error': 'Image too large (max 50MB)'}), 400
        
        file.seek(0)
        
        # Save file
        filename = secure_filename(file.filename)
        timestamp = int(time.time())
        filename = f"{timestamp}_{filename}"

        filepath = os.path.join(IMAGES_FOLDER, filename)
        file.save(filepath)
        
        # Build file info
        file_info = get_file_info(filepath, filename)
        file_info['user_id'] = str(user_id)          # 🔥 FIX: Convert to string
        file_info['file_type'] = 'image'
        file_info['file_path'] = filepath
        file_info['download_url'] = f'/uploads/images/{filename}'
        
        # Insert into MongoDB
        result = uploads_collection.insert_one(file_info)

        # 🔥 Make file_info JSON safe
        serializable_file_info = {
            k: str(v) if isinstance(v, ObjectId) else v
            for k, v in file_info.items()
        }

        return jsonify({
            'success': True,
            'message': 'Image uploaded successfully',
            'file_id': str(result.inserted_id),          # 🔥 FIX
            'filename': filename,
            'download_url': file_info['download_url'],
            'size_mb': file_info['size_mb'],
            'file_info': serializable_file_info          # 🔥 FIX
        })

    except Exception as e:
        print(f"❌ Image upload error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500



# ==========================================
# 🎥 ROUTES UPLOAD VIDÉOS
# ==========================================

@app.route('/api/upload/video', methods=['POST'])
@login_required
def upload_video():
    """Upload a video"""
    try:
        user_id = session.get('user_id')

        if 'video' not in request.files:
            return jsonify({'success': False, 'error': 'No video file found'}), 400
        
        file = request.files['video']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Empty filename'}), 400

        # Check extension
        if not allowed_file(file.filename, 'video'):
            return jsonify({
                'success': False,
                'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_VIDEOS)}'
            }), 400

        # Check file size
        file.seek(0, os.SEEK_END)
        file_length = file.tell()
        if file_length > 200 * 1024 * 1024:  # 200MB limit
            return jsonify({'success': False, 'error': 'Video too large (max 200MB)'}), 400

        file.seek(0)

        # Save video
        filename = secure_filename(file.filename)
        timestamp = int(time.time())
        filename = f"{timestamp}_{filename}"

        filepath = os.path.join(VIDEOS_FOLDER, filename)
        file.save(filepath)

        # Build info
        file_info = get_file_info(filepath, filename)
        file_info['user_id'] = str(user_id)       # 🔥 convert to string
        file_info['file_type'] = 'video'
        file_info['file_path'] = filepath
        file_info['download_url'] = f"/uploads/videos/{filename}"

        # Save in MongoDB
        result = uploads_collection.insert_one(file_info)

        # 🔥 Convert all ObjectId inside file_info
        serializable_file_info = {
            k: str(v) if isinstance(v, ObjectId) else v
            for k, v in file_info.items()
        }

        return jsonify({
            'success': True,
            'message': 'Video uploaded successfully',
            'file_id': str(result.inserted_id),         # 🔥 FIX
            'filename': filename,
            'download_url': file_info['download_url'],
            'size_mb': file_info.get('size_mb', None),
            'file_info': serializable_file_info         # 🔥 FIX
        })

    except Exception as e:
        print(f"❌ Video upload error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# 📋 ROUTE LISTER LES UPLOADS
# ==========================================

@app.route('/api/uploads', methods=['GET'])
@login_required
def get_uploads():
    """Liste tous les uploads de l'utilisateur"""
    try:
        user_id = session.get('user_id')
        file_type = request.args.get('type', 'all')  # all, image, video
        
        # Filtrer par type
        query = {'user_id': user_id}
        if file_type != 'all':
            query['file_type'] = file_type
        
        uploads = list(
            uploads_collection.find(query)
            .sort('_id', -1)
            .limit(100)
        )
        
        # Convertir ObjectId en string
        for upload in uploads:
            upload['_id'] = str(upload['_id'])
        
        return jsonify({
            'success': True,
            'uploads': uploads,
            'count': len(uploads)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==========================================
# 🗑️ ROUTE SUPPRIMER UN UPLOAD
# ==========================================

@app.route('/api/upload/<upload_id>', methods=['DELETE'])
@login_required
def delete_upload(upload_id):
    """Supprime un upload"""
    try:
        from bson import ObjectId
        
        user_id = session.get('user_id')
        
        # Trouver le fichier
        upload = uploads_collection.find_one({
            '_id': ObjectId(upload_id),
            'user_id': user_id
        })
        
        if not upload:
            return jsonify({
                'success': False,
                'error': 'Fichier non trouvé'
            }), 404
        
        # Supprimer le fichier du disque
        filepath = upload.get('file_path')
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            print(f"✅ Fichier supprimé: {filepath}")
        
        # Supprimer de MongoDB
        uploads_collection.delete_one({'_id': ObjectId(upload_id)})
        
        return jsonify({
            'success': True,
            'message': 'Fichier supprimé avec succès'
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==========================================
# 📥 ROUTES DE TÉLÉCHARGEMENT
# ==========================================

@app.route('/uploads/images/<filename>', methods=['GET'])
@login_required
def download_image(filename):
    """Télécharge une image"""
    try:
        filepath = os.path.join(IMAGES_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/uploads/videos/<filename>', methods=['GET'])
@login_required
def download_video(filename):
    """Télécharge une vidéo"""
    try:
        filepath = os.path.join(VIDEOS_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        return send_file(
            filepath,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==========================================
# 👁️ ROUTES DE VISIONNAGE
# ==========================================

@app.route('/view/image/<filename>', methods=['GET'])
@login_required
def view_image(filename):
    """Affiche une image"""
    try:
        filepath = os.path.join(IMAGES_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        return send_file(filepath)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/view/video/<filename>', methods=['GET'])
@login_required
def view_video(filename):
    """Affiche une vidéo"""
    try:
        filepath = os.path.join(VIDEOS_FOLDER, secure_filename(filename))
        
        if not os.path.exists(filepath):
            return jsonify({'error': 'Fichier non trouvé'}), 404
        
        return send_file(filepath)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ==========================================
# 📊 ROUTE STATISTIQUES UPLOAD
# ==========================================

@app.route('/api/upload/stats', methods=['GET'])
@login_required
def upload_stats():
    """Statistiques des uploads"""
    try:
        user_id = session.get('user_id')
        
        # Compter les uploads
        total_uploads = uploads_collection.count_documents({'user_id': user_id})
        image_count = uploads_collection.count_documents({'user_id': user_id, 'file_type': 'image'})
        video_count = uploads_collection.count_documents({'user_id': user_id, 'file_type': 'video'})
        
        # Calculer la taille totale
        uploads = list(uploads_collection.find({'user_id': user_id}))
        total_size_mb = sum(u.get('size_mb', 0) for u in uploads)
        
        return jsonify({
            'success': True,
            'total_uploads': total_uploads,
            'image_count': image_count,
            'video_count': video_count,
            'total_size_mb': round(total_size_mb, 2),
            'storage_limit_mb': 5000
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/user/profile')
def get_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    user = users_collection.find_one({"_id": ObjectId(user_id)})
    
    return jsonify({
        "success": True,
        "user": {
            "name": user.get("name", ""),
            "email": user.get("email", ""),
            "picture": user.get("picture", "")
        }
    })

@app.route('/api/user/profile/update', methods=['POST'])
def update_profile():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"success": False, "error": "Not logged in"}), 401

    name = request.form.get("name")
    email = request.form.get("email")
    picture_file = request.files.get("picture")

    updates = {
        "name": name,
        "email": email
    }

    # Handle picture upload
    if picture_file:
        filename = secure_filename(picture_file.filename)
        filename = f"{int(time.time())}_{filename}"
        save_path = os.path.join("static/uploads/profile", filename)

        os.makedirs("static/uploads/profile", exist_ok=True)
        picture_file.save(save_path)

        picture_url = f"/static/uploads/profile/{filename}"
        updates["picture"] = picture_url

        # Update session
        session["user_picture"] = picture_url

    # Update MongoDB
    users_collection.update_one(
        {"_id": ObjectId(user_id)},
        {"$set": updates}
    )

    session["user_name"] = name
    session["user_email"] = email

    return jsonify({"success": True})


@app.route('/api/user/profile/delete', methods=['POST'])
def delete_profile():
    user_id = session.get('user_id')

    users_collection.delete_one({"_id": ObjectId(user_id)})

    session.clear()

    return jsonify({"success": True})

@app.route('/api/users')
@login_required
def get_users():
    """Retourne la liste des utilisateurs (sauf mot de passe)"""
    try:
        users = list(users_collection.find(
            {}, 
            {'password': 0}  # Exclure le mot de passe
        ).sort('name', 1))
        
        # Convertir ObjectId en string
        for user in users:
            user['_id'] = str(user['_id'])
        
        return jsonify({
            'success': True,
            'users': users
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/switch_user/<user_id>', methods=['POST'])
@login_required
def switch_user(user_id):
    """Change l'utilisateur connecté"""
    try:
        from bson import ObjectId
        
        # Vérifier que l'utilisateur existe
        user = users_collection.find_one({'_id': ObjectId(user_id)})
        if not user:
            return jsonify({'success': False, 'error': 'Utilisateur non trouvé'}), 404
        
        # Mettre à jour la session
        session['user_id'] = str(user['_id'])
        session['user_email'] = user['email']
        session['user_name'] = user['name']
        session['user_picture'] = user.get('picture', '')
        
        # Mettre à jour le dernier login
        update_last_login(user['_id'])
        
        return jsonify({
            'success': True,
            'message': f'Connecté en tant que {user["name"]}',
            'name': user['name']
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)