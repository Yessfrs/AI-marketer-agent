
# advanced_adcopy_generator.py
import os
import re
import json
from typing import List, Dict
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

class AdvancedAdCopyGenerator:
    """Génère des ad-copy précis et contextualisés pour le calendrier"""
    
    def __init__(self, rag_system, site_id: str, site_info: Dict):
        self.rag_system = rag_system
        self.site_id = site_id
        self.site_info = site_info
        self.product_analysis = None
        
    def analyze_products_for_context(self) -> Dict:
        """Analyse les produits pour extraire le contexte précis"""
        try:
            # 1. Récupérer les produits depuis MongoDB
            all_products = self._fetch_products_from_mongo()
            
            if not all_products:
                print("⚠️ Aucun produit trouvé")
                return self._get_default_context()
            
            # 2. Catégoriser les produits
            analysis = {
                'total_products': len(all_products),
                'price_ranges': self._analyze_price_ranges(all_products),
                'top_products': self._extract_top_products(all_products),
                'product_categories': self._categorize_products(all_products),
                'product_features': self._extract_key_features(all_products),
                'unique_selling_points': self._extract_usp(all_products),
                'promoted_products': [p for p in all_products if p.get('is_promoted')],
                'seasonal_opportunities': self._identify_seasonal_opportunities(all_products),
                'pain_points': self._analyze_pain_points(all_products),
                'customer_journey_stage': self._map_customer_journey(all_products)
            }
            
            self.product_analysis = analysis
            return analysis
            
        except Exception as e:
            print(f"❌ Erreur analyse produits: {e}")
            return self._get_default_context()
    
    def _fetch_products_from_mongo(self) -> List[Dict]:
        """Récupère tous les produits du site depuis MongoDB"""
        try:
            from pymongo import MongoClient
            MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
            mongo_client = MongoClient(MONGO_URI)
            mongo_db = mongo_client["scraping_db"]
            scrapes_collection = mongo_db["scraped_sites"]
            
            # Trouver le site
            site_doc = scrapes_collection.find_one({"site_id": self.site_id})
            if not site_doc:
                return []
            
            all_products = []
            results = site_doc.get("results", [])
            
            for page in results:
                if isinstance(page, dict):
                    # Produits normaux
                    for product in page.get("products", []):
                        if isinstance(product, dict):
                            product['is_promoted'] = False
                            all_products.append(product)
                    
                    # Produits promus
                    for product in page.get("promoted_products", []):
                        if isinstance(product, dict):
                            product['is_promoted'] = True
                            all_products.append(product)
            
            return all_products
            
        except Exception as e:
            print(f"❌ Erreur récupération produits: {e}")
            return []
    
    def _analyze_price_ranges(self, products: List[Dict]) -> Dict:
        """Analyse les plages de prix"""
        prices = []
        for p in products:
            price_str = p.get('price', '')
            # Extraire le nombre
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
    
    def _extract_top_products(self, products: List[Dict]) -> List[Dict]:
        """Extrait les meilleurs produits"""
        # Trier par pertinence (promus + avec prix + avec description)
        scored_products = []
        
        for p in products:
            score = 0
            score += 5 if p.get('is_promoted') else 0
            score += 3 if p.get('price') else 0
            score += 2 if p.get('description') else 0
            score += 1 if p.get('image') else 0
            
            scored_products.append((score, p))
        
        scored_products.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored_products[:5]]
    
    def _categorize_products(self, products: List[Dict]) -> List[str]:
        """Catégorise automatiquement les produits"""
        categories = set()
        
        for product in products:
            name = product.get('name', '').lower()
            desc = product.get('description', '').lower()
            text = name + " " + desc
            
            # Détection basique de catégories
            category_keywords = {
                'électronique': ['téléphone', 'laptop', 'pc', 'ordinateur', 'électronique', 'tech'],
                'mode': ['vetement', 'robe', 'chaussures', 'sac', 'accessoires', 'mode'],
                'beauté': ['cosmétique', 'maquillage', 'soins', 'parfum', 'beauté'],
                'maison': ['meuble', 'décoration', 'cuisine', 'salle', 'maison'],
                'sport': ['sport', 'fitness', 'équipement', 'chaussures sport'],
                'alimentation': ['aliment', 'nourriture', 'boisson', 'café', 'chocolat'],
            }
            
            for category, keywords in category_keywords.items():
                if any(kw in text for kw in keywords):
                    categories.add(category)
        
        return list(categories) if categories else ['e-commerce']
    
    def _extract_key_features(self, products: List[Dict]) -> List[str]:
        """Extrait les caractéristiques clés des produits"""
        features = set()
        
        for product in products:
            name = product.get('name', '')
            desc = product.get('description', '')
            text = (name + " " + desc).lower()
            
            # Mots-clés de caractéristiques
            feature_keywords = [
                'gratuit', 'livraison', 'nouveau', 'stock', 'limité', 
                'exclusif', 'promo', 'remise', 'garantie', 'premium',
                'luxe', 'écologique', 'bio', 'naturel', 'personnalisé'
            ]
            
            for feature in feature_keywords:
                if feature in text:
                    features.add(feature.capitalize())
        
        return list(features)[:8]
    
    def _extract_usp(self, products: List[Dict]) -> List[str]:
        """Extrait les propositions uniques de vente"""
        usp = []
        
        # Basé sur le profil client
        brand_voice = self.site_info.get('brand_voice', '').lower()
        market_position = self.site_info.get('market_position', '').lower()
        
        if 'premium' in market_position:
            usp.append('Qualité exceptionnelle et sélection exclusive')
        if 'accessibilité' in brand_voice:
            usp.append('Prix compétitifs et accessibles')
        if 'innovation' in brand_voice:
            usp.append('Produits innovants et tendances')
        
        # Basé sur les données produits
        if len([p for p in products if p.get('is_promoted')]) > 0:
            usp.append('Offres spéciales et promotions exclusives')
        
        return usp if usp else ['Meilleure sélection du marché']
    
    def _identify_seasonal_opportunities(self, products: List[Dict]) -> Dict:
        """Identifie les opportunités saisonnières"""
        return {
            'back_to_school': any('scolaire' in p.get('name', '').lower() for p in products),
            'holiday': any('noël' in p.get('name', '').lower() or 'fête' in p.get('name', '').lower() for p in products),
            'summer': any('été' in p.get('name', '').lower() or 'plage' in p.get('name', '').lower() for p in products),
            'black_friday': any('black' in p.get('name', '').lower() or 'cyber' in p.get('name', '').lower() for p in products),
        }
    
    def _analyze_pain_points(self, products: List[Dict]) -> List[str]:
        """Analyse les points de douleur clients potentiels"""
        pain_points = []
        
        # Basé sur les prix
        avg_price = self._analyze_price_ranges(products)['avg']
        if 'Budget' in avg_price:
            pain_points.append('Budget limité')
        
        # Basé sur les descriptions
        all_text = " ".join([p.get('description', '') for p in products]).lower()
        
        if 'livraison' in all_text:
            pain_points.append('Livraison rapide recherchée')
        if 'garantie' in all_text:
            pain_points.append('Besoin de garantie et assurance')
        if 'retour' in all_text:
            pain_points.append('Flexibilité sur les retours')
        
        return pain_points if pain_points else ['Qualité et fiabilité']
    
    def _map_customer_journey(self, products: List[Dict]) -> str:
        """Mappe l'étape du parcours client"""
        # Basé sur les types de produits
        if len(products) > 20:
            return 'acquisition'  # Beaucoup de produits = phase d'acquisition
        elif len([p for p in products if p.get('is_promoted')]) > 5:
            return 'engagement'  # Beaucoup de promus = engagement
        else:
            return 'retention'  # Phase de rétention
    
    def _get_default_context(self) -> Dict:
        """Contexte par défaut"""
        return {
            'product_categories': ['e-commerce'],
            'price_ranges': {'min': 'N/A', 'max': 'N/A'},
            'top_products': [],
            'unique_selling_points': ['Meilleure sélection'],
            'promoted_products': [],
        }
    
    def generate_contextual_adcopy(self, post_data: Dict) -> str:
        """Génère un ad-copy contextuel et précis"""
        
        # Analyser les produits une fois
        if not self.product_analysis:
            self.analyze_products_for_context()
        
        # Construire le prompt spécialisé
        prompt = self._build_specialized_prompt(post_data)
        
        # Générer avec Gemini
        try:
            response = self.rag_system.generate_response(prompt)
            return self._clean_adcopy(response)
        except Exception as e:
            print(f"❌ Erreur génération ad-copy: {e}")
            return self._generate_fallback_adcopy(post_data)
    
    def _build_specialized_prompt(self, post_data: Dict) -> str:
        """Construit un prompt spécialisé basé sur le contexte complet"""
        
        analysis = self.product_analysis or self._get_default_context()
        company_name = self.site_info.get('company_name', 'Notre marque')
        industry = self.site_info.get('industry', 'e-commerce')
        brand_voice = self.site_info.get('brand_voice', 'professionnel')
        target_audience = self.site_info.get('target_audience', {})
        
        prompt = f"""
EN TANT QUE COPYWRITER EXPÉRIMENTÉ EN E-COMMERCE & COMMUNITY MANAGEMENT:

## CONTEXTE CLIENT
- **Entreprise**: {company_name}
- **Industrie**: {industry}
- **Voice de marque**: {brand_voice}
- **Audience cible**: {target_audience.get('demographics', ['General'])}
- **Positionnement**: {self.site_info.get('market_position', 'Standard')}

## ANALYSE PRODUITS PRÉCISE
- **Catégories**: {', '.join(analysis.get('product_categories', ['Divers']))}
- **Plage de prix**: {analysis['price_ranges'].get('min')} - {analysis['price_ranges'].get('max')} (Segment: {analysis['price_ranges'].get('range')})
- **Caractéristiques clés**: {', '.join(analysis.get('product_features', ['Qualité']))}
- **Propositions uniques**: {', '.join(analysis.get('unique_selling_points', ['Meilleure sélection']))}
- **Points de douleur clients**: {', '.join(analysis.get('pain_points', ['Qualité']))}
- **Étape du parcours client**: {analysis.get('customer_journey_stage', 'acquisition')}

## DÉTAILS DU POST À PROMOUVOIR
- **Thème**: {post_data.get('theme', 'Produit')}
- **Type de contenu**: {post_data.get('content_type', 'general')}
- **Angle créatif**: {post_data.get('creative_angle', 'Standard')}
- **Objectif marketing**: {post_data.get('marketing_goal', 'Engagement')}
- **Plateforme**: {post_data.get('platform', 'Multi-plateforme')}
- **Heure de publication**: {post_data.get('best_time', '12:00')}

## DIRECTIVES DE COPYWRITING AVANCÉ

### Format à générer (JSON):
{{
    "short_copy": "COURT (120 caractères max) - Hook accrocheur + CTA",
    "medium_copy": "MOYEN (250 caractères max) - Contexte + bénéfice + CTA",
    "long_copy": "LONG (500 caractères max) - Story complète + objections traitées + CTA",
    "hashtags": ["#tag1", "#tag2", "#tag3"],
    "cta_variations": ["CTA 1", "CTA 2", "CTA 3"],
    "emoji_suggestion": "emoji_approprié",
    "platform_tips": "Conseils spécifiques à la plateforme"
}}

### Principes appliqués:
1. **CLARTÉ**: Chaque copy doit être immédiatement compris
2. **PERTINENCE**: Utiliser les insights produits réels
3. **URGENCE**: Créer une raison d'agir maintenant
4. **SOCIAL PROOF**: Référencer popularité/promotion si applicable
5. **AUDIENCE ALIGNMENT**: Adapter au ton de la marque
6. **PLATFORM-SPECIFIC**: Optimiser pour la plateforme cible
7. **SEO-SOCIAL**: Inclure mots-clés pertinents

### Stratégie par type de contenu:
- **Education**: Apporter de la valeur d'abord, vendre implicitement
- **Promotion**: Mettre en avant le bénéfice principal immédiatement
- **Inspiration**: Émotionnel, aspirationnel, lifestyle
- **Engagement**: Poser question, créer conversation, interactif
- **Social Proof**: Utiliser témoignages, chiffres, popularité

## EXEMPLE DE QUALITÉ ATTENDUE:

Thème: "Produits premium été"
Industrie: "Vêtements de luxe"

SHORT: "Été 2024 ✨ Découvrez notre collection exclusive limited edition. Qualité premium, livraison gratuite → Lien"
MEDIUM: "L'été c'est l'occasion de se reinventer 🌞 Nos pièces signature combinent style intemporel et confort ultime. Seulement 50 pièces par modèle. Commande avant épuisement → Lien"
LONG: "Vous rêvez d'une garde-robe de rêve pour cet été? 👗 Nos designers ont créé une collection exclusive qui capture l'essence de l'été premium. Tissu 100% coton bio, coupes étudiées pour flatter toutes les silhouettes, et durabilité garantie. Les clients nous disent que c'est un investissement qui dure des années. Limited Edition: seulement 50 pièces par modèle. Ne manquez pas cette opportunité d'exception → Commander maintenant"

## GÉNÉRATION RÉELLE:
Sur la base de TOUS les éléments ci-dessus, génère maintenant un ad-copy PRÉCIS, CONTEXTUALISÉ et ACTIONNABLE.
Assure-toi que:
- Chaque copy utilise les insights produits réels
- Le ton correspond exactement à la marque
- Les CTA sont adaptés à l'objectif marketing
- Les hashtags sont pertinents et populaires actuellement
"""
        
        return prompt
    
    def _clean_adcopy(self, response: str) -> str:
        """Nettoie et valide la réponse"""
        try:
            import re
            import json
            
            # Extraire le JSON
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return json.dumps(data, ensure_ascii=False)
            
            return response
        except:
            return response
    
    def _generate_fallback_adcopy(self, post_data: Dict) -> str:
        """Génère un ad-copy de secours"""
        import json
        
        theme = post_data.get('theme', 'Découvrez nos produits')
        categories = ', '.join(self.product_analysis.get('product_categories', ['produits'])) if self.product_analysis else 'produits'
        
        return json.dumps({
            'short_copy': f"✨ {theme} - Qualité premium, livraison gratuite → Découvrir",
            'medium_copy': f"Trouvez exactement ce que vous cherchez dans notre sélection de {categories}. Qualité garantie et satisfaction client assurée.",
            'long_copy': f"Bienvenue chez {self.site_info.get('company_name', 'nous')}! Notre sélection exclusive de {categories} combine qualité, style et accessibilité. Découvrez pourquoi des milliers de clients nous font confiance.",
            'hashtags': ['#ecommerce', '#shopping', '#qualité'],
            'cta_variations': ['Découvrir maintenant', 'Explorer la collection', 'En savoir plus'],
            'emoji_suggestion': '✨',
            'platform_tips': 'Utiliser des images haute qualité avec ce texte'
        }, ensure_ascii=False)

# ==========================================
# 🔧 INTÉGRATION DANS LE CALENDRIER
# ==========================================

def generate_complete_calendar_improved(self, site_id: str, duration_weeks: int = 2, posts_per_week: int = 3):
    """Version améliorée avec ad-copy contextuel"""
    
    try:
        # Récupérer le profil client
        site_info = self.profile_manager.get_profile(site_id)
        if not site_info:
            return {'success': False, 'error': 'Profil client non trouvé'}
        
        # Initialiser le générateur d'ad-copy avancé
        adcopy_generator = AdvancedAdCopyGenerator(self, site_id, site_info)
        
        # Analyser les produits UNE FOIS
        print("🔍 Analyse complète des produits...")
        product_analysis = adcopy_generator.analyze_products_for_context()
        
        print(f"📊 Catégories trouvées: {', '.join(product_analysis.get('product_categories', []))}")
        print(f"💰 Plage de prix: {product_analysis['price_ranges'].get('min')} - {product_analysis['price_ranges'].get('max')}")
        print(f"⭐ USP: {', '.join(product_analysis.get('unique_selling_points', []))}")
        
        # Générer la stratégie
        strategy_prompt = self._build_calendar_strategy_prompt(site_info, product_analysis)
        strategy_response = self.generate_response(strategy_prompt)
        
        # Parser la stratégie
        import re, json
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
        for week in calendar_strategy.get('weeks', []):
            for day in week.get('days', []):
                if len(calendar_with_content['generated_posts']) >= total_posts:
                    break
                
                # Générer l'ad-copy contextuel
                adcopy_data = json.loads(
                    adcopy_generator.generate_contextual_adcopy(day)
                )
                
                post = {
                    'week': week.get('week_number'),
                    'day': day.get('day'),
                    'post_number': day.get('post_number'),
                    'theme': day.get('theme'),
                    'content_type': day.get('content_type'),
                    'creative_angle': day.get('creative_angle'),
                    'marketing_goal': day.get('marketing_goal'),
                    'best_time': day.get('best_time'),
                    # ✅ AD-COPY PRÉCIS ET CONTEXTUEL
                    'short_copy': adcopy_data.get('short_copy'),
                    'medium_copy': adcopy_data.get('medium_copy'),
                    'long_copy': adcopy_data.get('long_copy'),
                    'hashtags': adcopy_data.get('hashtags', []),
                    'cta_variations': adcopy_data.get('cta_variations', []),
                    'emoji_suggestion': adcopy_data.get('emoji_suggestion'),
                    'platform_tips': adcopy_data.get('platform_tips')
                }
                
                calendar_with_content['generated_posts'].append(post)
        
        return {
            'success': True,
            'calendar': calendar_with_content,
            'stats': {
                'total_posts': len(calendar_with_content['generated_posts']),
                'analysis_completed': True
            }
        }
        
    except Exception as e:
        print(f"❌ Erreur génération calendrier amélioré: {e}")
        import traceback
        print(f"🔙 Détails: {traceback.format_exc()}")
        return {'success': False, 'error': str(e)}

def _build_calendar_strategy_prompt(self, site_info: Dict, product_analysis: Dict) -> str:
    """Construit un prompt de stratégie amélioré"""
    # ... (implémentation similaire)
    pass