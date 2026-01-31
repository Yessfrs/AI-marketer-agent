import json
import os
import sys
import requests
from typing import List, Dict, Any
import numpy as np
import faiss
sys.path.append('/app')
from sentence_transformers import SentenceTransformer
import re
import hashlib
from pymongo import MongoClient
import time
import google.generativeai as genai
from collections import deque
from customer_profiles import CustomerProfileManager
from advanced_adcopy_generator import AdvancedAdCopyGenerator

from dotenv import load_dotenv
load_dotenv()



# Connexion MongoDB
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
mongo_client = MongoClient(MONGO_URI)
mongo_db = mongo_client["scraping_db"]
scrapes_collection = mongo_db["scraped_sites"]

# Import de la nouvelle API Google GenAI
try:
    from google import genai
    GOOGLE_GENAI_AVAILABLE = True
except ImportError:
    GOOGLE_GENAI_AVAILABLE = False
    print("❌ Bibliothèque google-genai non installée. Installez-la avec: pip install google-genai")
    
    
class RAGSystem:
    def __init__(self, gemini_api_key: str, mongo_client=None):
        self.gemini_api_key = gemini_api_key
        
        # Initialiser TOUS les attributs d'abord
        self.embedding_model = None
        self.embedding_dim = 384
        self.embedding_model_loaded = False
        
        # Index FAISS - initialiser à None d'abord
        self.index = None
        self.documents = []
        self.metadata = []
        self.raw_data = None
        self.data_hash = None
        self.last_loaded_file = None
        self.is_initialized = False
        
        self.adcopy_generator_class = AdvancedAdCopyGenerator
        self.cm_agent = None
        
        # Initialisation du client Google GenAI
        if GOOGLE_GENAI_AVAILABLE:
            self.client = genai.Client(api_key=gemini_api_key)
            print("✅ Client Google GenAI initialisé avec succès")
        else:
            self.client = None
            print("❌ Client Google GenAI non disponible")
        
        # Maintenant initialiser les autres composants
        self.history_manager = GenerationHistory(mongo_client)
        self.profile_manager = CustomerProfileManager(mongo_client)
        
        # Charger l'index FAISS existant
        self.load_faiss_index()
        
    def _load_embedding_model(self):
        """Charge le modèle d'embeddings seulement si nécessaire"""
        if not self.embedding_model_loaded or self.embedding_model is None:
            print("📦 Chargement du modèle d'embeddings...")
            try:
                self.embedding_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
                self.embedding_model_loaded = True
                print("✅ Modèle d'embeddings chargé avec succès")
            except Exception as e:
                print(f"❌ Erreur lors du chargement du modèle d'embeddings: {e}")
                raise e
        
    def check_data_changes(self) -> Dict:
        """Vérifie RAPIDEMENT s'il y a de nouveaux sites sans charger les données"""
        try:
            if not self.is_initialized:
                # Première initialisation - compter tous les sites
                all_sites = list(scrapes_collection.find({}, {"site_id": 1}))
                return {
                    'has_changes': True,
                    'new_sites_count': len(all_sites),
                    'total_sites': len(all_sites),
                    'indexed_sites': 0,
                    'reason': 'not_initialized'
                }
            
            # Récupérer UNIQUEMENT les site_id depuis MongoDB 
            mongo_site_ids = set(
                site['site_id'] 
                for site in scrapes_collection.find({}, {"site_id": 1})
            )
            
            # Comparer avec les sites déjà indexés
            indexed_site_ids = self.get_indexed_site_ids()
            new_sites = mongo_site_ids - indexed_site_ids
            
            return {
                'has_changes': len(new_sites) > 0,
                'new_sites_count': len(new_sites),
                'total_sites': len(mongo_site_ids),
                'indexed_sites': len(indexed_site_ids),
                'new_site_ids': list(new_sites),
                'reason': f'{len(new_sites)} nouveaux sites' if new_sites else 'no_changes'
            }
            
        except Exception as e:
            print(f"Erreur check_data_changes: {e}")
            return {'has_changes': True, 'reason': f'error: {str(e)}'}
       
       
    def save_faiss_index(self, path="faiss_index.bin"):
        """Sauvegarde l'index FAISS et les métadonnées"""
        if self.index is not None:
            faiss.write_index(self.index, path)
            with open("faiss_metadata.json", "w", encoding="utf-8") as f:
                json.dump(self.metadata, f, ensure_ascii=False, indent=2)
            print("💾 Index FAISS et métadonnées sauvegardés.")

    def load_faiss_index(self, path="faiss_index.bin"):
        """Recharge l'index FAISS et les métadonnées s'ils existent"""
        try:
            if os.path.exists(path) and os.path.exists("faiss_metadata.json"):
                print("📦 Chargement de l'index FAISS existant...")
                self.index = faiss.read_index(path)
                with open("faiss_metadata.json", "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)
                
                # Reconstruire la liste self.documents à partir des métadonnées
                self.documents = [meta.get('content', '') for meta in self.metadata if 'content' in meta]
                
                self.is_initialized = True
                print(f"✅ Index FAISS rechargé ({self.index.ntotal} vecteurs)")
            else:
                print("ℹ️ Aucun index FAISS existant trouvé - création d'un nouvel index")
                self.index = None
                self.is_initialized = False
                
        except Exception as e:
            print(f"❌ Erreur lors du chargement de l'index FAISS: {e}")
            self.index = None
            self.is_initialized = False

       
        
    def load_scraped_data(self, file_path: str = None):
        """Charge UNIQUEMENT les nouvelles données (incrémental optimisé)"""
        
        # Charger le modèle si nécessaire
        self._load_embedding_model()
        
        # Vérifier les changements SANS charger les données
        changes = self.check_data_changes()
        
        if not changes['has_changes'] and self.is_initialized:
            print("Aucun nouveau site - RAG déjà à jour")
            return True
        
        # Première initialisation - tout charger
        if not self.is_initialized or self.index is None:
            print("Première initialisation - Chargement complet...")
            return self._load_all_data_from_mongo()
        
        # Chargement incrémental - UNIQUEMENT les nouveaux sites
        new_site_ids = changes.get('new_site_ids', [])
        if not new_site_ids:
            print("Aucun nouveau site à charger")
            return True
        
        print(f"Chargement de {len(new_site_ids)} NOUVEAUX sites uniquement...")
        return self._load_incremental_sites(new_site_ids)

    def _load_incremental_sites(self, new_site_ids: list) -> bool:
        """Charge UNIQUEMENT les sites spécifiés (ultra-rapide)"""
        try:
            new_documents = []
            new_metadata = []
            
            # Récupérer UNIQUEMENT les nouveaux sites depuis MongoDB
            new_sites = list(scrapes_collection.find({"site_id": {"$in": new_site_ids}}))
            
            print(f"📝 Traitement de {len(new_sites)} nouveaux sites...")
            
            for site_doc in new_sites:
                site_id = site_doc["site_id"]
                
                # Stocker dans raw_data
                if self.raw_data is None:
                    self.raw_data = {}
                self.raw_data[site_id] = site_doc
                
                results = site_doc.get("results", [])
                print(f"📄 Site {site_id}: {len(results)} pages")
                
                for i, page in enumerate(results):
                    # Pages
                    page_documents = self._create_page_documents(page, i, site_id)
                    for doc in page_documents:
                        new_documents.append(doc['content'])
                        new_metadata.append(doc['metadata'])
                    
                    # Produits normaux
                    for j, product in enumerate(page.get("products", [])):
                        product_data = self._create_product_document(
                            product, page.get("url", ""), site_id, i, j, "normal"
                        )
                        if product_data:
                            new_documents.append(product_data['content'])
                            new_metadata.append(product_data['metadata'])
                    
                    # Produits promus
                    for j, product in enumerate(page.get("promoted_products", [])):
                        product_data = self._create_product_document(
                            product, page.get("url", ""), site_id, i, j, "promoted"
                        )
                        if product_data:
                            new_documents.append(product_data['content'])
                            new_metadata.append(product_data['metadata'])
            
            if not new_documents:
                print("ℹ️ Aucun nouveau document à indexer")
                return True
            
            print(f"🔄 Génération de {len(new_documents)} nouveaux embeddings...")
            
            new_embeddings = self.embedding_model.encode(
                new_documents,
                show_progress_bar=True,
                batch_size=32,
                normalize_embeddings=True
            ).astype('float32')
            
            # Créer l'index s'il n'existe pas encore
            if self.index is None:
                print("🔨 Création d'un nouvel index FAISS...")
                self.index = faiss.IndexFlatIP(self.embedding_dim)
            
            # Ajouter à l'index FAISS existant
            self.index.add(new_embeddings)
            
            # Étendre les listes
            self.documents.extend(new_documents)
            self.metadata.extend(new_metadata)
            
            print(f"✅ {len(new_documents)} nouveaux documents indexés")
            print(f"📊 Total index: {self.index.ntotal} vecteurs")
            
            # Sauvegarde après l'ajout incrémental
            self.save_faiss_index()
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur chargement incrémental: {e}")
            import traceback
            traceback.print_exc()
            return False

    
    def generate_complete_calendar_improved(self, site_id: str, duration_weeks: int = 2, posts_per_week: int = 3):
        """Version améliorée avec ad-copy contextuel"""
        
        try:
            # Récupérer le profil client
            site_info = self.profile_manager.get_profile(site_id)
            if not site_info:
                return {'success': False, 'error': 'Profil client non trouvé'}
            
            # Initialiser le générateur d'ad-copy avancé
            adcopy_generator = self.adcopy_generator_class(self, site_id, site_info)
            
            # Analyser les produits UNE FOIS
            print("🔍 Analyse complète des produits...")
            product_analysis = adcopy_generator.analyze_products_for_context()
            
            print(f"📊 Catégories trouvées: {', '.join(product_analysis.get('product_categories', []))}")
            print(f"💰 Plage de prix: {product_analysis['price_ranges'].get('min')} - {product_analysis['price_ranges'].get('max')}")
            print(f"⭐ USP: {', '.join(product_analysis.get('unique_selling_points', []))}")
            
            # Générer la stratégie
            strategy_prompt = f"""
            EN TANT QUE PLANIFICATEUR DE CONTENU EXPERT:
            
            Crée une stratégie de calendrier pour {duration_weeks} semaines avec {posts_per_week} posts/semaine
            pour une entreprise dans l'industrie: {site_info.get('industry')}
            
            Produits: {', '.join(product_analysis.get('product_categories', []))}
            Plage de prix: {product_analysis['price_ranges'].get('min')} - {product_analysis['price_ranges'].get('max')}
            
            Génère UNIQUEMENT du JSON avec structure weeks -> days -> posts
            """
            
            strategy_response = self.generate_response(strategy_prompt)
            
            # Parser la stratégie
            json_match = re.search(r'\{[\s\S]*\}', strategy_response, re.DOTALL)
            if json_match:
                calendar_strategy = json.loads(json_match.group())
            else:
                calendar_strategy = self._create_fallback_strategy(duration_weeks, posts_per_week)
            
            # Générer les posts avec ad-copy précis
            calendar_with_content = {
                'strategy': calendar_strategy,
                'generated_posts': [],
                'product_analysis': product_analysis,
                'company_info': {
                    'company_name': site_info.get('company_name'),
                    'industry': site_info.get('industry')
                }
            }
            
            total_posts = duration_weeks * posts_per_week
            post_count = 0
            
            for week in calendar_strategy.get('weeks', []):
                for day in week.get('days', []):
                    if post_count >= total_posts:
                        break
                    
                    # GÉNÉRER L'AD-COPY CONTEXTUEL
                    adcopy_data_str = adcopy_generator.generate_contextual_adcopy(day)
                    try:
                        adcopy_data = json.loads(adcopy_data_str)
                    except:
                        adcopy_data = json.loads(adcopy_generator._generate_fallback_adcopy(day))
                    
                    post = {
                        'week': week.get('week_number'),
                        'day': day.get('day'),
                        'post_number': day.get('post_number'),
                        'theme': day.get('theme'),
                        'content_type': day.get('content_type'),
                        'creative_angle': day.get('creative_angle'),
                        'marketing_goal': day.get('marketing_goal'),
                        'best_time': day.get('best_time'),
                        # AD-COPY PRÉCIS ET CONTEXTUEL
                        'short_copy': adcopy_data.get('short_copy'),
                        'medium_copy': adcopy_data.get('medium_copy'),
                        'long_copy': adcopy_data.get('long_copy'),
                        'hashtags': adcopy_data.get('hashtags', []),
                        'cta_variations': adcopy_data.get('cta_variations', []),
                        'emoji_suggestion': adcopy_data.get('emoji_suggestion'),
                        'platform_tips': adcopy_data.get('platform_tips')
                    }
                    
                    calendar_with_content['generated_posts'].append(post)
                    post_count += 1
            
            return {
                'success': True,
                'calendar': calendar_with_content,
                'stats': {
                    'total_posts': len(calendar_with_content['generated_posts']),
                    'analysis_completed': True,
                    'product_categories': product_analysis.get('product_categories', []),
                    'price_range': f"{product_analysis['price_ranges'].get('min')} - {product_analysis['price_ranges'].get('max')}"
                }
            }
            
        except Exception as e:
            print(f"❌ Erreur génération calendrier amélioré: {e}")
            import traceback
            print(f"🔙 Détails: {traceback.format_exc()}")
            return {'success': False, 'error': str(e)}
    
    # HELPER POUR FALLBACK
    def _create_fallback_strategy(self, duration_weeks: int, posts_per_week: int) -> Dict:
        """Crée une stratégie de fallback"""
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
        content_types = ["education", "promotion", "inspiration", "engagement"]
        times = ["9:00", "12:00", "15:00", "18:00", "21:00"]
        
        weeks = []
        post_counter = 1
        
        for week_num in range(1, duration_weeks + 1):
            week = {
                "week_number": week_num,
                "theme": f"Semaine {week_num}",
                "days": []
            }
            
            for day_idx in range(posts_per_week):
                if post_counter > duration_weeks * posts_per_week:
                    break
                    
                day_data = {
                    "day": days[day_idx % len(days)],
                    "post_number": post_counter,
                    "theme": f"Post {post_counter}",
                    "content_type": content_types[post_counter % len(content_types)],
                    "creative_angle": f"Angle {post_counter}",
                    "marketing_goal": f"Objectif {post_counter}",
                    "best_time": times[post_counter % len(times)]
                }
                
                week["days"].append(day_data)
                post_counter += 1
            
            weeks.append(week)
        
        return {"weeks": weeks}

    def _load_all_data_from_mongo(self) -> bool:
        """Charge TOUTES les données depuis MongoDB (première initialisation)"""
        try:
            print("Chargement complet depuis MongoDB...")
            
            all_sites = list(scrapes_collection.find())
            
            if not all_sites:
                print("Aucune donnée dans MongoDB")
                return False
            
            self.raw_data = {}
            self.documents = []
            self.metadata = []
            
            total_products = 0
            total_promoted = 0
            
            for site_doc in all_sites:
                site_id = site_doc["site_id"]
                self.raw_data[site_id] = site_doc
                
                results = site_doc.get("results", [])
                for i, page in enumerate(results):
                    # Pages
                    page_documents = self._create_page_documents(page, i, site_id)
                    for doc in page_documents:
                        self.documents.append(doc['content'])
                        self.metadata.append(doc['metadata'])
                    
                    # Produits
                    for j, product in enumerate(page.get("products", [])):
                        product_data = self._create_product_document(
                            product, page.get("url", ""), site_id, i, j, "normal"
                        )
                        if product_data:
                            self.documents.append(product_data['content'])
                            self.metadata.append(product_data['metadata'])
                            total_products += 1
                    
                    for j, product in enumerate(page.get("promoted_products", [])):
                        product_data = self._create_product_document(
                            product, page.get("url", ""), site_id, i, j, "promoted"
                        )
                        if product_data:
                            self.documents.append(product_data['content'])
                            self.metadata.append(product_data['metadata'])
                            total_promoted += 1
            
            print(f"{len(self.documents)} documents chargés")
            print(f"{total_products} normaux, {total_promoted} promus, {len(all_sites)} sites")
            
            # Construire l'index
            if self.documents:
                self._build_faiss_index()
                self.is_initialized = True
                return True
            
            return False
            
        except Exception as e:
            print(f"Erreur chargement complet: {e}")
            return False

    
    def get_performance_stats(self) -> Dict:
        """Statistiques de performance du chargement"""
        return {
            'total_sites': len(self.raw_data) if self.raw_data else 0,
            'total_documents': len(self.documents),
            'index_size': self.index.ntotal if self.index else 0,
            'indexed_sites': len(self.get_indexed_site_ids()),
            'memory_usage_mb': len(str(self.documents)) / (1024 * 1024) if self.documents else 0
        }
    

    def _load_all_data(self, all_sites):
        """Charge toutes les données (première initialisation)"""
        self.raw_data = {}
        self.documents = []
        self.metadata = []

        total_products = 0
        total_promoted_products = 0

        for site_doc in all_sites:
            site_id = site_doc["site_id"]
            self.raw_data[site_id] = site_doc

            results = site_doc.get("results", [])
            for i, page in enumerate(results):
                page_documents = self._create_page_documents(page, i, site_id)
                for doc in page_documents:
                    self.documents.append(doc['content'])
                    self.metadata.append(doc['metadata'])

                normal_products = page.get("products", [])
                for j, product in enumerate(normal_products):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "normal"
                    )
                    if product_data:
                        self.documents.append(product_data['content'])
                        self.metadata.append(product_data['metadata'])
                        total_products += 1

                promoted_products = page.get("promoted_products", [])
                for j, product in enumerate(promoted_products):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "promoted"
                    )
                    if product_data:
                        self.documents.append(product_data['content'])
                        self.metadata.append(product_data['metadata'])
                        total_promoted_products += 1

        print(f"✅ {len(self.documents)} documents chargés depuis MongoDB")
        print(f"📊 {total_products} produits normaux, {total_promoted_products} produits promus, {len(all_sites)} sites")

        # Construire l'index FAISS
        if self.documents:
            self._build_faiss_index()
            self.is_initialized = True
            return True
        else:
            self.is_initialized = False
            return False

    def _load_incremental_data(self, all_sites):
        """Charge uniquement les nouveaux sites"""
        # Récupérer les IDs déjà indexés
        indexed_site_ids = set(m.get("site_id") for m in self.metadata if "site_id" in m)
        new_sites = [s for s in all_sites if s["site_id"] not in indexed_site_ids]

        if not new_sites:
            print("✅ Aucun nouveau site à indexer. RAG déjà à jour.")
            return True

        print(f"🆕 {len(new_sites)} nouveaux sites détectés. Mise à jour incrémentielle...")

        # Préparer les nouveaux documents
        new_documents, new_metadata = [], []

        for site_doc in new_sites:
            site_id = site_doc["site_id"]
            self.raw_data[site_id] = site_doc  # Ajouter aux données brutes

            results = site_doc.get("results", [])
            for i, page in enumerate(results):
                page_documents = self._create_page_documents(page, i, site_id)
                for doc in page_documents:
                    new_documents.append(doc['content'])
                    new_metadata.append(doc['metadata'])

                normal_products = page.get("products", [])
                for j, product in enumerate(normal_products):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "normal"
                    )
                    if product_data:
                        new_documents.append(product_data['content'])
                        new_metadata.append(product_data['metadata'])

                promoted_products = page.get("promoted_products", [])
                for j, product in enumerate(promoted_products):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "promoted"
                    )
                    if product_data:
                        new_documents.append(product_data['content'])
                        new_metadata.append(product_data['metadata'])

        print(f"📄 {len(new_documents)} nouveaux documents à indexer...")

        if not new_documents:
            print("ℹ️ Aucun nouveau document à ajouter")
            return True

        # Générer les embeddings pour les nouveaux documents
        new_embeddings = self.embedding_model.encode(
            new_documents,
            show_progress_bar=True,
            batch_size=32,
            normalize_embeddings=True
        ).astype('float32')

        # Ajouter les nouveaux embeddings à FAISS
        self.index.add(new_embeddings)
        print(f"✅ {self.index.ntotal} vecteurs dans l'index après mise à jour")

        # Étendre les listes locales
        self.documents.extend(new_documents)
        self.metadata.extend(new_metadata)

        self.is_initialized = True
        print(f"🎉 Mise à jour incrémentielle terminée! {len(new_documents)} nouveaux documents ajoutés.")
        return True

        
    def _calculate_data_hash(self, data_content: str) -> str:
        """Calcule un hash MD5 du contenu des données"""
        return hashlib.md5(data_content.encode('utf-8')).hexdigest()
    
    def is_up_to_date(self, file_path: str = "last_scrape.json") -> bool:
        """Vérifie si le RAG est à jour avec les données"""
        if not os.path.exists(file_path):
            return False
            
        if self.data_hash is None or self.index is None:
            return False
        
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
            current_hash = self._calculate_data_hash(file_content)
        
        return (self.data_hash == current_hash and 
                self.last_loaded_file == file_path)
    
    def _build_faiss_index(self):
        """Construit l'index FAISS à partir des documents"""
        print("🔨 Construction de l'index FAISS...")
        
        # Générer les embeddings pour tous les documents
        print("🧮 Génération des embeddings...")
        embeddings = self.embedding_model.encode(
            self.documents,
            show_progress_bar=True,
            batch_size=32,
            normalize_embeddings=True  # Normalisation pour utiliser cosine similarity
        )
        
        # Créer l'index FAISS
        # IndexFlatIP pour Inner Product (équivalent à cosine similarity avec vecteurs normalisés)
        self.index = faiss.IndexFlatIP(self.embedding_dim)
        
        # Ajouter les embeddings à l'index
        self.index.add(embeddings.astype('float32'))
        
        print(f"✅ Index FAISS créé avec {self.index.ntotal} vecteurs")
        
   
    def _create_page_documents(self, page: Dict, page_index: int, site_id: str) -> List[Dict]:
        """Crée les documents pour une page"""
        documents = []
        page_url = page.get('url', '')
        
        # Document principal de la page
        text_parts = []
        if page.get('title'):
            text_parts.append(f"TITRE_PAGE: {page['title']}")
        if page.get('url'):
            text_parts.append(f"URL: {page['url']}")
        if page.get('meta_description'):
            text_parts.append(f"DESCRIPTION: {page['meta_description']}")
        if page.get('h1'):
            text_parts.append(f"H1: {page['h1']}")
        if page.get('excerpt'):
            excerpt = page['excerpt'][:1000] if len(page['excerpt']) > 1000 else page['excerpt']
            text_parts.append(f"CONTENU: {excerpt}")
        
        text_parts.append(f"PROFONDEUR: {page.get('depth', 0)}")
        text_parts.append(f"NOMBRE_PRODUITS: {len(page.get('products', []))}")
        text_parts.append(f"NOMBRE_IMAGES: {len(page.get('images', []))}")
        
        if text_parts:
            documents.append({
                'content': " | ".join(text_parts),
                'metadata': {
                    'type': 'page',
                    'site_id': site_id,
                    'url': page_url,
                    'title': page.get('title', ''),
                    'page_index': page_index,
                    'category': 'page_metadata'
                }
            })
        
        return documents
    
    def _create_products_documents(self, page: Dict, page_index: int, site_id: str) -> List[Dict]:
        """Crée les documents pour TOUS les produits d'une page (normaux + promus)"""
        documents = []
        page_url = page.get('url', '')
        
        # Produits normaux
        normal_products = page.get('products', [])
        if not isinstance(normal_products, list):
            normal_products = []
        
        # Produits promus
        promoted_products = page.get('promoted_products', [])
        if not isinstance(promoted_products, list):
            promoted_products = []
        
        print(f"📦 Page {page_url}: {len(normal_products)} produits normaux, {len(promoted_products)} produits promus")
        
        # Traiter les produits normaux
        for j, product in enumerate(normal_products):
            if not isinstance(product, dict):
                continue
                
            product_data = self._create_product_document(product, page_url, site_id, page_index, j, "normal")
            if product_data:
                documents.append(product_data)
        
        # Traiter les produits promus
        for j, product in enumerate(promoted_products):
            if not isinstance(product, dict):
                continue
                
            product_data = self._create_product_document(product, page_url, site_id, page_index, j, "promoted")
            if product_data:
                documents.append(product_data)
        
        return documents

    def _create_product_document(self, product: Dict, page_url: str, site_id: str, page_index: int, product_index: int, product_type: str) -> Dict:
        """Crée un document pour un produit (normal ou promu)"""
        text_parts = []
        
        # Informations produit de base
        if product.get('name'):
            text_parts.append(f"PRODUIT_NOM: {product['name']}")
        
        if product.get('price'):
            price_text = product['price'].replace('€', ' euro ').replace('$', ' dollar ')
            text_parts.append(f"PRIX: {price_text}")
            text_parts.append(f"PRIX_NUMERIQUE: {price_text}")
        
        if product.get('description'):
            desc = product['description'][:500] if len(product['description']) > 500 else product['description']
            text_parts.append(f"DESCRIPTION: {desc}")
        
        if product.get('sku'):
            text_parts.append(f"REFERENCE: {product['sku']}")
        
        if product.get('image'):
            text_parts.append("IMAGE_DISPONIBLE: oui")
        
        if product.get('product_url'):
            text_parts.append(f"URL_PRODUIT: {product['product_url']}")
        
        # Type de produit
        text_parts.append(f"TYPE_PRODUIT: {product_type}")
        if product_type == "promoted":
            text_parts.append("PROMU: oui")
            text_parts.append("PRODUIT_EN_AVANT: oui")
            promotion_indicators = product.get('promotion_indicators', [])
            if promotion_indicators:
                text_parts.append(f"INDICATEURS_PROMOTION: {', '.join(promotion_indicators)}")
        else:
            text_parts.append("PROMU: non")
        
        # Mots-clés pour améliorer la recherche
        text_parts.append("CATEGORIE: produit")
        text_parts.append("E_COMMERCE: oui")
        
        if text_parts:
            return {
                'content': " | ".join(text_parts),
                'metadata': {
                    'type': 'product',
                    'product_type': product_type,
                    'site_id': site_id,
                    'page_url': page_url,
                    'product_name': product.get('name', ''),
                    'price': product.get('price', ''),
                    'page_index': page_index,
                    'product_index': product_index,
                    'category': 'product',
                    'is_promoted': (product_type == "promoted")
                }
            }
        
        return None
    
    def _create_footer_documents(self, page: Dict, page_index: int, site_id: str) -> List[Dict]:
        """Crée les documents pour le footer"""
        documents = []
        footer = page.get('footer', {})
        if not footer or not isinstance(footer, dict):
            return documents
        
        page_url = page.get('url', '')
        
        # Footer textuel
        if footer.get('text'):
            footer_text = footer['text'][:800] if len(footer['text']) > 800 else footer['text']
            documents.append({
                'content': f"FOOTER: {footer_text}",
                'metadata': {
                    'type': 'footer',
                    'site_id': site_id,
                    'url': page_url,
                    'page_index': page_index,
                    'category': 'footer'
                }
            })
        
        # Liens du footer
        links = footer.get('links', [])
        if links and isinstance(links, list):
            links_text = " | ".join([f"{link.get('text', '')} -> {link.get('url', '')}" 
                                    for link in links[:5] if isinstance(link, dict)])
            if links_text:
                documents.append({
                    'content': f"LIENS_FOOTER: {links_text}",
                    'metadata': {
                        'type': 'footer_links',
                        'site_id': site_id,
                        'url': page_url,
                        'links_count': len(links),
                        'page_index': page_index,
                        'category': 'footer'
                    }
                })
        
        return documents
    
    def search(self, query: str, k: int = 20) -> List[Dict]:
        """Recherche améliorée avec FAISS"""
        if self.index is None or not self.documents:
            return []
        
        try:
            # Générer l'embedding de la requête
            query_embedding = self.embedding_model.encode(
                [query],
                normalize_embeddings=True
            ).astype('float32')
            
            # Rechercher dans l'index FAISS
            scores, indices = self.index.search(query_embedding, k)
            
            # Construire les résultats
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < len(self.documents) and score > 0.3:  # Seuil de similarité
                    results.append({
                        'document': self.documents[idx],
                        'metadata': self.metadata[idx],
                        'score': float(score)
                    })
            
            # Trier par pertinence avec boosting
            results = self._sort_by_relevance(results, query)
            
            return results
            
        except Exception as e:
            print(f"❌ Erreur recherche: {e}")
            return []

    
    def update_rag_incremental(self):
        """Met à jour automatiquement le RAG avec uniquement les nouveaux sites ajoutés dans MongoDB"""
        print("🔄 Mise à jour incrémentielle du RAG...")

        # Charger modèle si nécessaire
        self._load_embedding_model()

        # Connexion Mongo
        MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        mongo_client = MongoClient(MONGO_URI)
        db = mongo_client["scraping_db"]
        collection = db["scraped_sites"]

        # Récupérer tous les sites
        all_sites = list(collection.find())
        if not all_sites:
            print("⚠️ Aucune donnée dans MongoDB.")
            return False

        # Récupérer les IDs déjà indexés
        indexed_site_ids = set(m.get("site_id") for m in self.metadata if "site_id" in m)
        new_sites = [s for s in all_sites if s["site_id"] not in indexed_site_ids]

        if not new_sites:
            print("✅ Aucun nouveau site à indexer. RAG déjà à jour.")
            return True

        print(f"🆕 {len(new_sites)} nouveaux sites détectés. Mise à jour de l'index FAISS...")

        # Préparer les nouveaux documents
        new_documents, new_metadata = [], []
        new_embeddings_data = []

        for site_doc in new_sites:
            site_id = site_doc["site_id"]
            results = site_doc.get("results", [])

            for i, page in enumerate(results):
                page_documents = self._create_page_documents(page, i, site_id)
                for doc in page_documents:
                    new_documents.append(doc['content'])
                    new_metadata.append(doc['metadata'])

                for j, product in enumerate(page.get("products", [])):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "normal"
                    )
                    if product_data:
                        new_documents.append(product_data['content'])
                        new_metadata.append(product_data['metadata'])

                for j, product in enumerate(page.get("promoted_products", [])):
                    product_data = self._create_product_document(
                        product, page.get("url", ""), site_id, i, j, "promoted"
                    )
                    if product_data:
                        new_documents.append(product_data['content'])
                        new_metadata.append(product_data['metadata'])

        print(f"📄 {len(new_documents)} nouveaux documents à indexer...")

        # Générer les embeddings pour les nouveaux documents
        new_embeddings = self.embedding_model.encode(
            new_documents,
            show_progress_bar=True,
            batch_size=32,
            normalize_embeddings=True
        ).astype('float32')

        # Ajouter les nouveaux embeddings à FAISS
        if self.index is None:
            print("⚠️ Index FAISS non trouvé, création initiale...")
            self.index = faiss.IndexFlatIP(self.embedding_dim)

        self.index.add(new_embeddings)
        print(f"✅ {self.index.ntotal} vecteurs dans l'index après mise à jour")

        # Étendre les listes locales
        self.documents.extend(new_documents)
        self.metadata.extend(new_metadata)

        self.is_initialized = True
        return True

    
    
    def _sort_by_relevance(self, results, query):
        """Trie les résultats par pertinence pour la requête"""
        query_lower = query.lower()
        
        def relevance_score(item):
            score = item['score']
            metadata = item['metadata']
            document = item['document'].lower()
            
            # Booster les produits
            if metadata.get('category') == 'product' or metadata.get('type') == 'product':
                score *= 1.5
            
            # Booster si la requête contient des mots spécifiques
            if 'promo' in query_lower and metadata.get('is_promoted'):
                score *= 2.0
            if 'prix' in query_lower and 'PRIX:' in document:
                score *= 1.5
            if 'description' in query_lower and 'DESCRIPTION:' in document:
                score *= 1.3
                
            return score
        
        return sorted(results, key=relevance_score, reverse=True)
    
    def generate_response(self, query: str, context: List[Dict] = None) -> str:
        """Génère une réponse basée sur TOUTES les données disponibles avec l'API REST Gemini"""
        if context is None:
            context = self.search(query)
        
        context_text = self._format_context(context)
        
        system_prompt = """TU ES UN EXPERT EN MARKETING DIGITAL ET COMMUNITY MANAGEMENT.

TU ES UN EXPERT SENIOR EN DIGITAL MARKETING & E-COMMERCE avec 15 ans d'expérience.

# DOMAINES D'EXPERTISE
- Stratégie de contenu et calendrier éditorial
- Analyse de sites e-commerce et optimisation CRO
- Community Management et gestion des réseaux sociaux
- Stratégies de contenu viral et engagement
- Analyse des produits et pricing
- Marketing des promotions et campagnes
- Expérience utilisateur (UX) et fidélisation
- Analytics et performance marketing

# COMPÉTENCES SPÉCIFIQUES COMMUNITY MANAGEMENT
- Création de calendriers de publication uniques
- Stratégie de contenu par plateforme (Instagram, Facebook, Twitter, LinkedIn, TikTok)
- Techniques d'engagement et de croissance communautaire
- Analyse du sentiment et gestion de réputation
- Création de campagnes virales
- Gestion des influenceurs et partenariats

# CONTEXTE DES DONNÉES
Tu as accès à des données scrapées de sites e-commerce contenant :
• PRODUITS NORMALS → Catalogue standard
• PRODUITS PROMUS → Mise en avant spéciale (promotions, vedettes)
• MÉTADONNÉES → Titres, descriptions, prix, images
• STRUCTURE SITE → Pages, navigation, contenu

# DIRECTIVES DE RÉPONSE
- Réponds toujours en français ou en anglais 
- Sois exhaustif, précis et actionnable
- Propose des stratégies concrètes et mesurables
- Différencie clairement produits normaux et promus
- Organise les réponses par site et par pertinence
- Pour les calendriers de publication, propose des créneaux optimaux et variés"""

        user_prompt = f"""QUESTION: {query}

CONTEXTE COMPLET (toutes les données scrapées de tous les sites):
{context_text}

En tant qu'expert en marketing digital et community management, analyse toutes les données ci-dessus et fournis une réponse COMPLÈTE, STRUCTURÉE et ACTIONNABLE. 

Si la question concerne un calendrier de publication, assure-toi de proposer des créneaux variés et une stratégie de contenu différenciée.

RÉPONSE DÉTAILLÉE:"""
        
        full_prompt = f"{system_prompt}\n\n{user_prompt}"
        
        try:
            print("🔄 Génération de la réponse avec Google GenAI...")
            
            if not GOOGLE_GENAI_AVAILABLE or self.client is None:
                return "❌ API Google GenAI non disponible. Installez: pip install google-genai"
            
            # Essayer différents modèles dans l'ordre de préférence
            models_to_try = [
                "gemini-2.0-flash",
                "gemini-1.5-flash", 
                "gemini-1.5-pro",
                "gemini-1.0-pro"
            ]
            
            for model_name in models_to_try:
                try:
                    print(f"🔄 Essai avec le modèle: {model_name}")
                    
                    response = self.client.models.generate_content(
                        model=model_name,
                        contents=full_prompt,
                        config={
                            "temperature": 0.1,
                            "max_output_tokens": 2000,
                            "top_p": 0.8,
                            "top_k": 40
                        }
                    )
                    
                    final_response = response.text
                    
                    # Enregistrer dans MongoDB via GenerationHistory
                    if hasattr(self, 'history_manager') and self.history_manager:
                        self.history_manager.add_generation(query, final_response, "general")

                    print(f"✅ Réponse générée avec succès using {model_name}")
                    return final_response
                    
                except Exception as model_error:
                    print(f"❌ Échec avec {model_name}: {model_error}")
                    continue
            
            # Si tous les modèles échouent
            return "❌ Tous les modèles Gemini ont échoué. Vérifiez votre clé API."
            
        except Exception as e:
            error_msg = f"❌ Erreur API Google GenAI: {str(e)}"
            print(error_msg)
            return f"❌ Erreur API Google GenAI: {str(e)}"
    
    def _format_context(self, context: List[Dict]) -> str:
        """Formate le contexte pour le prompt"""
        if not context:
            return "AUCUNE DONNÉE PERTINENTE TROUVÉE DANS LES SITES SCRAPÉS"
        
        # Grouper par site pour meilleure organisation
        sites_data = {}
        
        for item in context:
            site_id = item['metadata'].get('site_id', 'unknown')
            if site_id not in sites_data:
                sites_data[site_id] = {
                    'products': [],
                    'pages': [],
                    'footers': [],
                    'site_info': []
                }
            
            category = item['metadata'].get('category')
            doc_type = item['metadata'].get('type')
            
            if category == 'product' or doc_type == 'product':
                sites_data[site_id]['products'].append(item)
            elif category == 'page_metadata' or doc_type == 'page':
                sites_data[site_id]['pages'].append(item)
            elif category == 'footer' or doc_type in ['footer', 'footer_links']:
                sites_data[site_id]['footers'].append(item)
            elif doc_type == 'site_info':
                sites_data[site_id]['site_info'].append(item)
        
        context_parts = ["=== DONNÉES COMPLÈTES (TOUS LES SITES) ==="]
        
        # Afficher les données par site
        for site_id, site_data in sites_data.items():
            context_parts.append(f"\n🏠 SITE: {site_id}")
            
            # Informations du site
            if site_data['site_info']:
                for info in site_data['site_info']:
                    context_parts.append(f"📋 Info Site: {info['document']}")
            
            # Produits (priorité)
            if site_data['products']:
                context_parts.append(f"\n🎯 PRODUITS TROUVÉS ({len(site_data['products'])}):")
                for i, item in enumerate(site_data['products'], 1):
                    context_parts.append(f"\n--- Produit {i} (pertinence: {item['score']:.3f}) ---")
                    context_parts.append(f"{item['document']}")
            
            # Pages
            if site_data['pages']:
                context_parts.append(f"\n📄 PAGES ({len(site_data['pages'])}):")
                for i, item in enumerate(site_data['pages'], 1):
                    context_parts.append(f"\n--- Page {i} (pertinence: {item['score']:.3f}) ---")
                    context_parts.append(f"{item['document']}")
            
            # Footers
            if site_data['footers']:
                context_parts.append(f"\n🦶 FOOTERS ({len(site_data['footers'])}):")
                for i, item in enumerate(site_data['footers'], 1):
                    context_parts.append(f"\n--- Footer {i} (pertinence: {item['score']:.3f}) ---")
                    context_parts.append(f"{item['document']}")
        
        context_parts.append("\n=== FIN DES DONNÉES ===")
        return "\n".join(context_parts)
    
    def ask_question(self, question: str, site_id: str = None) -> str:
        """Pose une question sur TOUTES les données avec contexte client optionnel"""
        if not self.documents:
            return "❌ Aucune donnée chargée. Effectuez d'abord un scraping et initialisez le système RAG."
        
        print(f"🔍 Recherche dans les données: '{question}'")
        
        # Contexte client si disponible
        customer_context = ""
        customer_info = None
        if site_id:
            customer_context = self.profile_manager.generate_context_prompt(site_id)
            customer_info = self.profile_manager.get_profile(site_id)
            print(f"🎯 Contexte client chargé: {site_id}")
        
        # Recherche standard dans les données scrapées
        relevant_docs = self.search(question, k=15)
        
        # Enrichir avec le contexte client
        if customer_context:
            relevant_docs = self._enhance_with_customer_context(relevant_docs, customer_context)
        
        if not relevant_docs:
            return "❌ Aucune information pertinente trouvée dans les données scrapées."
        
        # Générer la réponse
        print("🤖 Génération de la réponse...")
        response = self.generate_response(question, relevant_docs)
        
        return response

    def _enhance_with_customer_context(self, search_results: List[Dict], customer_context: str) -> List[Dict]:
        """Enrichit les résultats de recherche avec le contexte client"""
        # Créer un document spécial pour le contexte client
        customer_doc = {
            'document': customer_context,
            'metadata': {
                'type': 'customer_profile',
                'category': 'customer_context',
                'relevance_score': 1.0,
                'priority': 'high'
            },
            'score': 1.0
        }
        
        # Ajouter en tête des résultats pour priorité maximale
        return [customer_doc] + search_results
    
    def get_available_sites(self) -> List[Dict]:
        """Retourne la liste des sites disponibles avec leurs profils"""
        sites = []
        
        # Récupérer tous les sites scrapés
        scraped_sites = list(mongo_db.scraped_sites.find({}, {
            "site_id": 1, 
            "start_url": 1, 
            "scraped_at": 1,
            "scraped_count": 1
        }))
        
        for site in scraped_sites:
            # Générer ou récupérer le profil
            profile = self.profile_manager.get_profile(site["site_id"])
            if profile:
                site_info = {
                    "site_id": site["site_id"],
                    "domain": profile.get("domain", ""),
                    "company_name": profile.get("company_name", ""),
                    "industry": profile.get("industry", ""),
                    "business_type": profile.get("business_type", ""),
                    "scraped_count": site.get("scraped_count", 0),
                    "scraped_at": site.get("scraped_at", ""),
                    "profile_completeness": profile.get("profile_completeness", 0)
                }
                sites.append(site_info)
        
        return sites
    
    #Optimisation du process de l'initialisation du RAG
    
    def get_indexed_site_ids(self) -> set:
        """Récupérer les IDs des sites déjà indexés"""
        indexed_sites = set()
        for meta in self.metadata:
            if 'site_id' in meta:
                indexed_sites.add(meta['site_id'])
        return indexed_sites
    
    
    
    
    def get_stats(self) -> Dict:
        """Retourne les statistiques complètes"""
        if not self.raw_data:
            return {'initialized': False}
        
        total_sites = len(self.raw_data)
        total_pages = 0
        total_products = 0
        total_promoted_products = 0
        
        # Compter par site avec plus de détails
        sites_details = {}
        for site_id, site_data in self.raw_data.items():
            if isinstance(site_data, dict):
                results = site_data.get('results', [])
                site_pages = len(results) if isinstance(results, list) else 0
                site_products = 0
                site_promoted_products = 0
                
                if isinstance(results, list):
                    for page in results:
                        if isinstance(page, dict):
                            products = page.get('products', [])
                            if isinstance(products, list):
                                site_products += len(products)
                            promoted_products = page.get('promoted_products', [])
                            if isinstance(promoted_products, list):
                                site_promoted_products += len(promoted_products)
                
                total_pages += site_pages
                total_products += site_products
                total_promoted_products += site_promoted_products
                
                sites_details[site_id] = {
                    'pages': site_pages,
                    'products': site_products,
                    'promoted_products': site_promoted_products,
                    'start_url': site_data.get('start_url', 'N/A')
                }
        
        # Compter par catégorie dans les documents
        product_docs = len([m for m in self.metadata if m.get('category') == 'product' or m.get('type') == 'product'])
        page_docs = len([m for m in self.metadata if m.get('category') == 'page_metadata' or m.get('type') == 'page'])
        footer_docs = len([m for m in self.metadata if m.get('category') == 'footer' or m.get('type') in ['footer', 'footer_links']])
        
        stats = {
            'initialized': self.index is not None,
            'total_sites': total_sites,
            'total_pages': total_pages,
            'total_products': total_products,
            'total_documents': len(self.documents),
            'product_documents': product_docs,
            'page_documents': page_docs,
            'footer_documents': footer_docs,
            'sites': list(self.raw_data.keys()) if self.raw_data else [],
            'index_size': self.index.ntotal if self.index else 0
        }
        return stats

    def list_sites(self) -> List[Dict]:
        """Liste tous les sites disponibles avec leurs statistiques"""
        if not self.raw_data:
            return []
        
        sites_list = []
        for site_id, site_data in self.raw_data.items():
            if isinstance(site_data, dict):
                sites_list.append({
                    'site_id': site_id,
                    'start_url': site_data.get('start_url', 'N/A'),
                    'scraped_count': site_data.get('scraped_count', 0),
                    'max_pages': site_data.get('max_pages', 0),
                    'max_depth': site_data.get('max_depth', 0)
                })
        
        return sites_list
    
    def generate_marketing_response(self, query: str, context: List[Dict] = None) -> str:
        """Génère une réponse spécialisée marketing avec contrôle d'historique"""
        if context is None:
            context = self.search(query)
        
        # Vérifier si c'est une demande de calendrier
        is_calendar_request = any(keyword in query.lower() for keyword in 
                                ['calendrier', 'publication', 'programmation', 'éditorial', 'planning'])
        
        if is_calendar_request:
            return self._generate_unique_calendar(query, context)
        else:
            response = self.generate_response(query, context)
            self.history_manager.add_generation(query, response, "marketing")
            return response

    def _generate_unique_calendar(self, query: str, context: List[Dict], max_attempts: int = 3) -> str:
        """Génère un calendrier de publication unique"""
        for attempt in range(max_attempts):
            response = self.generate_response(query, context)
            
            # Vérifier l'unicité du calendrier
            if not self.history_manager.is_similar_calendar(response):
                self.history_manager.add_generation(query, response, "calendar")
                return response
            
            # Si trop similaire, ajouter une variation dans le prompt
            variation_prompt = f"{query} - IMPORTANT: Propose un calendrier COMPLÈTEMENT DIFFÉRENT des précédents, avec des créneaux horaires variés et des types de contenu innovants."
            response = self.generate_response(variation_prompt, context)
        
        # Fallback après plusieurs tentatives
        self.history_manager.add_generation(query, response, "calendar")
        return response
    

# Singleton pour le système RAG
_rag_instance = None

class GenerationHistory:
    """Gère l'historique des générations dans MongoDB"""
    
    def __init__(self, mongo_client: str = None, max_history: int = 100):
        self.max_history = max_history
        
        # Toujours définir MONGO_URI avant le if
        MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        
        try:
            # Si un client externe est fourni, on l'utilise
            if mongo_client is not None:
                self.mongo_client = mongo_client
            else:
                self.mongo_client = MongoClient(MONGO_URI)
            
            self.db = self.mongo_client["scraping_db"]
            self.collection = self.db["generation_history"]
            
            # Créer un index TTL pour expiration automatique après 30 jours
            self.collection.create_index("timestamp", expireAfterSeconds=2592000)
            print("✅ Connexion MongoDB établie pour l'historique des générations")
            
        except Exception as e:
            print(f"❌ Erreur connexion MongoDB pour l'historique: {e}")
            self.collection = None

    
    def add_generation(self, query: str, response: str, category: str = "general"):
        """Ajoute une génération à l'historique MongoDB"""
        if self.collection is None:
            print("⚠️ Collection MongoDB non disponible - historique non sauvegardé")
            return
            
        try:
            entry = {
                'timestamp': time.time(),
                'query': query,
                'response_hash': hashlib.md5(response.encode()).hexdigest(),
                'category': category,
                'date': time.strftime("%Y-%m-%d %H:%M:%S"),
                'response_preview': response[:200] + "..." if len(response) > 200 else response
            }
            
            # Insérer dans MongoDB
            self.collection.insert_one(entry)
            
            # Maintenir la limite d'historique
            self._enforce_history_limit()
            
        except Exception as e:
            print(f"❌ Erreur sauvegarde historique MongoDB: {e}")
    
    def _enforce_history_limit(self):
        """Supprime les entrées les plus anciennes si on dépasse la limite"""
        if self.collection is None:
            return
            
        try:
            count = self.collection.count_documents({})
            if count > self.max_history:
                # Trouver les documents les plus anciens à supprimer
                oldest_entries = self.collection.find().sort("timestamp", 1).limit(count - self.max_history)
                ids_to_delete = [entry['_id'] for entry in oldest_entries]
                
                if ids_to_delete:
                    self.collection.delete_many({"_id": {"$in": ids_to_delete}})
                    print(f"🗑️ {len(ids_to_delete)} anciennes entrées supprimées de l'historique")
                    
        except Exception as e:
            print(f"❌ Erreur limitation historique: {e}")
    
    def is_similar_calendar(self, new_response: str, threshold: float = 0.8) -> bool:
        """Vérifie si un calendrier est trop similaire à un précédent dans MongoDB"""
        if self.collection is None:
            return False
            
        try:
            new_hash = hashlib.md5(new_response.encode()).hexdigest()
            
            # Rechercher les calendriers récents (7 derniers jours)
            cutoff_time = time.time() - (7 * 24 * 60 * 60)
            
            recent_calendars = self.collection.find({
                'category': 'calendar',
                'timestamp': {'$gte': cutoff_time}
            }).sort('timestamp', -1)
            
            for entry in recent_calendars:
                similarity = self._calculate_similarity(new_response, entry['response_hash'])
                if similarity > threshold:
                    return True
                    
            return False
            
        except Exception as e:
            print(f"❌ Erreur vérification similarité: {e}")
            return False
    
    def _calculate_similarity(self, response1: str, hash2: str) -> float:
        """Calcule la similarité entre deux réponses"""
        if self.collection is None:
            return 0.0
            
        try:
            # Récupérer la réponse originale depuis MongoDB
            original_entry = self.collection.find_one({'response_hash': hash2})
            if not original_entry:
                return 0.0
                
            response2 = original_entry.get('response_preview', '')
            
            # Similarité basée sur les mots-clés des calendriers
            calendar_keywords = ['lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi', 'dimanche',
                               'matin', 'midi', 'après-midi', 'soir', 'publication', 'post', 'contenu',
                               'instagram', 'facebook', 'twitter', 'tiktok', 'linkedin']
            
            response1_lower = response1.lower()
            response2_lower = response2.lower()
            
            matching_keywords = 0
            for keyword in calendar_keywords:
                if keyword in response1_lower and keyword in response2_lower:
                    matching_keywords += 1
            
            return matching_keywords / len(calendar_keywords)
            
        except Exception as e:
            print(f"❌ Erreur calcul similarité: {e}")
            return 0.0
    
    def get_recent_calendars(self, days: int = 7) -> List[Dict]:
        """Récupère les calendriers récents depuis MongoDB"""
        if self.collection is None:
            return []
            
        try:
            cutoff_time = time.time() - (days * 24 * 60 * 60)
            
            calendars = self.collection.find({
                'category': 'calendar',
                'timestamp': {'$gte': cutoff_time}
            }).sort('timestamp', -1)
            
            return list(calendars)
            
        except Exception as e:
            print(f"❌ Erreur récupération calendriers: {e}")
            return []
    
    def get_generation_stats(self) -> Dict:
        """Retourne les statistiques de génération"""
        if self.collection is None:
            return {
                'total_generations': 0,
                'calendar_generations': 0,
                'marketing_generations': 0,
                'last_generation': None
            }
            
        try:
            total_count = self.collection.count_documents({})
            calendar_count = self.collection.count_documents({'category': 'calendar'})
            marketing_count = self.collection.count_documents({'category': 'marketing'})
            
            # Dernière génération
            last_generation = self.collection.find_one(sort=[('timestamp', -1)])
            
            return {
                'total_generations': total_count,
                'calendar_generations': calendar_count,
                'marketing_generations': marketing_count,
                'last_generation': last_generation.get('date') if last_generation else None
            }
            
        except Exception as e:
            print(f"❌ Erreur statistiques génération: {e}")
            return {}

def get_rag_system(api_key: str = None, mongo_client=None):
    """
    Récupère ou initialise une instance du système RAG avec Google GenAI.
    """
    global _rag_instance
    if _rag_instance is None:
        if api_key is None:
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        
        if not api_key:
            print("⚠️ Aucune clé Gemini détectée — le RAG fonctionnera en mode local sans LLM.")
            api_key = "LOCAL_MODE"

        try:
            _rag_instance = RAGSystem(api_key, mongo_client)
            print(f"✅ Système RAG avec Google GenAI initialisé avec succès")
            
            # Test de connexion
            if GOOGLE_GENAI_AVAILABLE and api_key != "LOCAL_MODE":
                test_result = _test_gemini_connection(api_key)
                if test_result:
                    print("✅ Connexion à l'API Gemini vérifiée")
                else:
                    print("❌ Échec de la connexion à l'API Gemini")
                    
        except Exception as e:
            print(f"❌ Erreur lors de la création du RAGSystem avec Google GenAI : {e}")
            import traceback
            print(f"🔍 Détails: {traceback.format_exc()}")
            _rag_instance = None

    return _rag_instance


def _test_gemini_connection(api_key: str) -> bool:
    """Teste la connexion à l'API Gemini"""
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        
        # Test avec un modèle simple
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents="Dis bonjour en français en une phrase."
        )
        
        print(f"✅ Test réussi: {response.text}")
        return True
        
    except Exception as e:
        print(f"❌ Test échoué: {e}")
        return False


def initialize_rag(self, file_path: str = "last_scrape.json") -> bool:
        """Initialise manuellement le RAG seulement si demandé"""
        print("🚀 Initialisation manuelle du RAG demandée...")
        
        # Charger le modèle d'embeddings
        self._load_embedding_model()
        
        # Vérifier les changements
        changes = self.check_data_changes()
        
        if not changes['has_changes'] and self.is_initialized:
            print("✅ RAG déjà à jour - Pas besoin de réinitialisation")
            return True
        
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Fichier {file_path} non trouvé")
        
        # Charger les données
        with open(file_path, 'r', encoding='utf-8') as f:
            file_content = f.read()
            self.raw_data = json.loads(file_content)
            self.data_hash = self._calculate_data_hash(file_content)
            self.last_loaded_file = file_path
        
        self.documents = []
        self.metadata = []
        
        print("📥 Chargement des données depuis last_scrape.json...")
        
        total_products = 0
        total_pages = 0
        total_promoted_products = 0
        
        for site_id, site_data in self.raw_data.items():
            if not isinstance(site_data, dict):
                continue
                
            # 1. Informations générales du site
            start_url = site_data.get('start_url', 'Inconnu')
            scraped_count = site_data.get('scraped_count', 0)
            
            general_info = f"SITE_{site_id}: URL={start_url} | Pages={scraped_count}"
            self.documents.append(general_info)
            self.metadata.append({
                'type': 'site_info',
                'site_id': site_id,
                'category': 'metadata'
            })
            
            # 2. Traiter chaque page
            results = site_data.get('results', [])
            if not isinstance(results, list):
                continue
                
            for i, page in enumerate(results):
                if not isinstance(page, dict):
                    continue
                    
                total_pages += 1
                
                # A. Métadonnées de la page
                page_documents = self._create_page_documents(page, i, site_id)
                for doc in page_documents:
                    self.documents.append(doc['content'])
                    self.metadata.append(doc['metadata'])
                
                # B. PRODUITS NORMAUX
                normal_products = page.get('products', [])
                for j, product in enumerate(normal_products):
                    if isinstance(product, dict):
                        product_data = self._create_product_document(
                            product, page.get('url', ''), site_id, i, j, "normal"
                        )
                        if product_data:
                            self.documents.append(product_data['content'])
                            self.metadata.append(product_data['metadata'])
                            total_products += 1
                
                # C. PRODUITS PROMUS
                promoted_products = page.get('promoted_products', [])
                for j, product in enumerate(promoted_products):
                    if isinstance(product, dict):
                        product_data = self._create_product_document(
                            product, page.get('url', ''), site_id, i, j, "promoted"
                        )
                        if product_data:
                            self.documents.append(product_data['content'])
                            self.metadata.append(product_data['metadata'])
                            total_promoted_products += 1
                
                # D. Footer
                footer_documents = self._create_footer_documents(page, i, site_id)
                for doc in footer_documents:
                    self.documents.append(doc['content'])
                    self.metadata.append(doc['metadata'])
        
        print(f"✅ {len(self.documents)} documents chargés")
        print(f"📊 Statistiques: {total_products} produits normaux, {total_promoted_products} produits promus, {total_pages} pages")
        
        # Construire l'index FAISS
        if self.documents:
            self._build_faiss_index()
            self.is_initialized = True
            print("🎉 RAG initialisé avec succès!")
            return True
        else:
            print("⚠️ Aucun document à indexer")
            return False
    
def can_answer_questions(self) -> bool:
    """Vérifie si le système peut répondre aux questions"""
    return self.is_initialized and self.index is not None and len(self.documents) > 0

def initialize_rag_system(api_key: str = None, force_reload: bool = False):
    """Initialise le système RAG avec Google GenAI"""
    try:
        rag_system = get_rag_system(api_key)
        
        print("🚀 Initialisation du système RAG avec Google GenAI...")
        
        # Méthode directe : charger toutes les données depuis MongoDB
        rag_system.load_scraped_data()
        
        # Vérifier que le système est bien initialisé
        if rag_system.is_initialized and rag_system.index is not None:
            total_docs = len(rag_system.documents) if rag_system.documents else 0
            total_sites = len(rag_system.raw_data) if rag_system.raw_data else 0
            
            print(f"✅ RAG avec Google GenAI initialisé avec succès: {total_sites} sites, {total_docs} documents")
            return True, f"✅ RAG avec Google GenAI initialisé avec {total_sites} sites et {total_docs} documents"
        else:
            return False, "❌ Échec de l'initialisation du RAG avec Google GenAI"
            
    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation RAG avec Google GenAI: {str(e)}")
        import traceback
        print(f"🔍 Détails: {traceback.format_exc()}")
        return False, f"❌ Erreur lors de l'initialisation avec Google GenAI: {str(e)}"