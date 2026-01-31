import json
import re
from typing import Dict, List, Any
from datetime import datetime, timedelta

class ImprovedCalendarGenerator:
    """Générateur de calendrier avec ad-copy et images cohérentes"""
    
    def __init__(self, rag_system, site_id: str, site_info: Dict, image_generator):
        self.image_generator = image_generator
        self.rag_system = rag_system
        self.site_id = site_id
        self.site_info = site_info
        self.profile_manager = rag_system.profile_manager
        
    def generate_complete_calendar(self, duration_weeks: int = 2, posts_per_week: int = 3) -> Dict:
        """
        Génère un calendrier complet avec ad-copy cohérent et image de référence pour chaque post
        """
        try:
            print(f"🎯 Génération calendrier complet ({duration_weeks} semaines, {posts_per_week} posts/semaine)")
            
            # 1. Analyser les produits
            product_analysis = self._analyze_products()
            print(f"✅ Analyse: {len(product_analysis['products'])} produits")
            
            # 2. Identifier les offres
            offers_data = self._identify_offers(product_analysis)
            print(f"🎁 {len(offers_data)} offres détectées")
            
            # 3. Générer la stratégie
            strategy = self._generate_strategy_with_gemini(
                duration_weeks, 
                posts_per_week, 
                product_analysis, 
                offers_data
            )
            
            # 4. Générer les posts avec ad-copy ET images cohérentes
            calendar = self._generate_posts_with_coherent_content(
                strategy, 
                product_analysis, 
                offers_data
            )
            
            return {
                'success': True,
                'calendar': calendar,
                'stats': {
                    'total_posts': len(calendar['posts']),
                    'duration_weeks': duration_weeks,
                    'posts_per_week': posts_per_week,
                    'products_featured': len(product_analysis['products']),
                    'offers_featured': len(offers_data),
                    'images_generated': sum(1 for p in calendar['posts'] if p.get('image_url') != 'N/A')
                }
            }
            
        except Exception as e:
            print(f"❌ Erreur génération calendrier: {e}")
            import traceback
            traceback.print_exc()
            return {'success': False, 'error': str(e)}
    
    def _generate_posts_with_coherent_content(
        self, 
        strategy: Dict, 
        product_analysis: Dict,
        offers_data: List[Dict]
    ) -> Dict:
        """
        🔑 FONCTION CLÉE: Génère les posts avec ad-copy et images COHÉRENTES
        """
        posts = []
        products_list = product_analysis['products']
        offers_list = offers_data
        
        product_idx = 0
        offer_idx = 0
        
        for week in strategy.get('weeks', []):
            for day in week.get('days', []):
                print(f"\n📝 Génération Post {day.get('post_number')}")
                
                target_content = day.get('target_content', 'produit')
                content_type = day.get('content_type', 'product_showcase')
                
                # ÉTAPE 1: Sélectionner le contenu source
                selected_item, reference_image_url = self._select_content_and_image(
                    target_content, 
                    products_list, 
                    offers_list,
                    product_idx,
                    offer_idx
                )
                
                if target_content == 'offre':
                    offer_idx += 1
                elif target_content == 'produit':
                    product_idx += 1
                
                # ÉTAPE 2: Générer l'ad-copy basé sur le contenu sélectionné
                ad_copy_data = self._generate_contextual_adcopy(
                    selected_item,
                    target_content,
                    day
                )
                
                # ÉTAPE 3: Créer le prompt d'image COHÉRENT avec l'ad-copy
                image_prompt = self._create_coherent_image_prompt(
                    ad_copy_data,
                    selected_item,
                    target_content,
                    day
                )
                
                print(f"   📸 Prompt image: {image_prompt[:80]}...")
                print(f"   🖼️ Image référence: {reference_image_url[:50] if reference_image_url else 'Aucune'}...")
                
                # ÉTAPE 4: Générer l'image avec cohérence
                image_result = self._generate_coherent_image(
                    image_prompt,
                    reference_image_url,
                    ad_copy_data,
                    selected_item
                )
                
                # ÉTAPE 5: Assembler le post complet
                post = {
                    'week': week.get('week_number'),
                    'day': day.get('day'),
                    'post_number': day.get('post_number'),
                    'theme': day.get('theme'),
                    'content_type': content_type,
                    'target_content': target_content,
                    'optimal_time': day.get('optimal_time', '12:00'),
                    'platforms': day.get('platforms', ['instagram', 'facebook']),
                    
                    # Ad-copy
                    **ad_copy_data,
                    
                    # Image cohérente
                    'image_url': image_result.get('image_url', 'N/A'),
                    'image_prompt': image_prompt,
                    'reference_image': reference_image_url or 'Aucune',
                    'image_status': image_result.get('status', 'PENDING'),
                    
                    # Métadonnées
                    'selected_item_name': selected_item.get('name', 'N/A') if isinstance(selected_item, dict) else 'N/A',
                    'coherence_score': self._calculate_coherence_score(ad_copy_data, image_result)
                }
                
                posts.append(post)
                print(f"   ✅ Post {post['post_number']} généré (cohérence: {post['coherence_score']})")
        
        return {
            'posts': posts,
            'strategy': strategy.get('content_distribution', {}),
            'generated_at': datetime.now().isoformat(),
            'total_posts': len(posts)
        }
    
    def _select_content_and_image(
        self,
        target_content: str,
        products_list: List[Dict],
        offers_list: List[Dict],
        product_idx: int,
        offer_idx: int
    ) -> tuple:
        """
        Sélectionne le contenu source ET son image de référence
        Retourne: (selected_item, reference_image_url)
        """
        reference_image_url = None
        selected_item = None
        
        if target_content == 'offre' and offers_list:
            # Offre
            offer = offers_list[offer_idx % len(offers_list)]
            selected_item = offer
            
            if offer['type'] == 'product_offer':
                # Offre basée sur un produit -> utiliser l'image du produit
                reference_image_url = offer['product'].get('image')
                print(f"   🎁 Offre sélectionnée: {offer['product'].get('name', 'N/A')[:40]}")
            else:
                # Offre de catégorie -> pas d'image de référence spécifique
                print(f"   🏷️ Offre catégorie: {offer.get('category')}")
        
        elif target_content == 'actualité':
            # Actualité -> pas d'image de référence, génération pure
            selected_item = {'type': 'news'}
            print("   📰 Actualité sélectionnée")
        
        else:  # produit
            if products_list:
                product = products_list[product_idx % len(products_list)]
                selected_item = product
                reference_image_url = product.get('image')
                print(f"   📦 Produit: {product.get('name', 'N/A')[:40]}")
            else:
                selected_item = {'type': 'generic'}
                print("   ⚠️ Pas de produit disponible, contenu générique")
        
        return selected_item, reference_image_url
    
    def _generate_contextual_adcopy(
        self,
        selected_item: Dict,
        target_content: str,
        day: Dict
    ) -> Dict:
        """
        Génère un ad-copy contextuel basé sur l'item sélectionné
        """
        if target_content == 'offre':
            return self._generate_offer_adcopy(selected_item, day)
        elif target_content == 'actualité':
            # Utiliser le contexte du jour pour l'actualité
            return self._generate_news_adcopy(day, {})
        elif target_content == 'produit':
            if isinstance(selected_item, dict) and selected_item.get('name'):
                return self._generate_product_adcopy(selected_item, day)
        
        # Fallback
        return self._generate_generic_adcopy(day)
    
    def _create_coherent_image_prompt(
        self,
        ad_copy_data: Dict,
        selected_item: Dict,
        target_content: str,
        day: Dict
    ) -> str:
        """
        🎨 Crée un prompt d'image COHÉRENT avec l'ad-copy
        """
        # Récupérer les éléments clés de l'ad-copy
        short_copy = ad_copy_data.get('short_copy', '')
        visual_theme = ad_copy_data.get('visual_theme', '')
        emoji = ad_copy_data.get('emoji', '')
        
        # Base du prompt
        base_elements = []
        
        # 1. Contexte du contenu
        if target_content == 'offre':
            base_elements.append("Promotion marketing professionnelle")
            base_elements.append("design attractif et commercial")
            if 'Flash' in short_copy:
                base_elements.append("style urgence et dynamique")
        elif target_content == 'actualité':
            base_elements.append("Image éditoriale moderne")
            base_elements.append("style magazine professionnel")
        else:  # produit
            base_elements.append("Product photography professionnelle")
            base_elements.append("mise en valeur du produit")
        
        # 2. Informations produit si disponible
        if isinstance(selected_item, dict):
            item_name = selected_item.get('name', '')
            item_category = selected_item.get('category', '')
            
            if item_name:
                # Extraire les mots-clés importants du nom
                keywords = self._extract_keywords_from_text(item_name)
                if keywords:
                    base_elements.append(f"focus sur {', '.join(keywords[:3])}")
            
            if item_category and item_category != 'Autres produits':
                base_elements.append(f"style {item_category.lower()}")
        
        # 3. Style visuel adapté au thème
        visual_styles = {
            'promotion_banner': 'banner design with bold text overlay, promotional style',
            'product_spotlight': 'clean background, professional lighting, product focus',
            'category_showcase': 'collection display, multiple items, organized layout',
            'editorial_article': 'magazine style, editorial photography, modern layout',
            'general_store': 'e-commerce style, clean and professional'
        }
        
        if visual_theme in visual_styles:
            base_elements.append(visual_styles[visual_theme])
        
        # 4. Ton de la marque
        brand_voice = self.site_info.get('brand_voice', 'professional')
        tone_styles = {
            'professional': 'corporate style, elegant, sophisticated',
            'casual': 'friendly style, approachable, modern',
            'luxury': 'premium style, high-end, exclusive',
            'playful': 'fun style, vibrant, colorful'
        }
        base_elements.append(tone_styles.get(brand_voice, 'professional style'))
        
        # 5. Qualité et optimisations
        base_elements.extend([
            'high quality 4K',
            'professional photography',
            'social media optimized',
            'square format 1:1',
            'excellent composition',
            'perfect lighting'
        ])
        
        # Assembler le prompt
        prompt = ", ".join(base_elements)
        
        return prompt
    
    def _extract_keywords_from_text(self, text: str) -> List[str]:
        """Extrait les mots-clés importants d'un texte"""
        # Mots à ignorer
        stop_words = {'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'pour', 'avec', 'en', 'et'}
        
        # Nettoyer et diviser
        words = text.lower().replace('-', ' ').replace('_', ' ').split()
        
        # Filtrer et garder les mots significatifs
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]
        
        return keywords[:5]  # Maximum 5 mots-clés
    
    def _generate_coherent_image(
        self,
        image_prompt: str,
        reference_image_url: str,
        ad_copy_data: Dict,
        selected_item: Dict
    ) -> Dict:
        """
        Génère une image COHÉRENTE avec l'ad-copy et la référence
        """
        tone = self.site_info.get('brand_voice', 'professional')
        industry = self.site_info.get('industry', 'e-commerce')
        
        client_context = {
            'industry': industry,
            'brand_voice': tone,
            'brand_values': self.site_info.get('brand_values', [])
        }
        
        # Déterminer le style selon le thème visuel
        visual_theme = ad_copy_data.get('visual_theme', 'general_store')
        style = self._map_visual_theme_to_style(visual_theme)
        
        try:
            if reference_image_url and reference_image_url.startswith('http'):
                # Génération Image2Image (avec référence)
                print(f"   🖼️ Génération Image2Image avec référence")
                result = self.image_generator.generate_with_reference_image_advanced(
                    image_url=reference_image_url,
                    prompt=image_prompt,
                    tone=tone,
                    client_context=client_context,
                    style=style
                )
            else:
                # Génération Text2Image (sans référence)
                print(f"   🎨 Génération Text2Image pure")
                result = self.image_generator.generate_without_reference(
                    ad_copy=image_prompt,
                    tone=tone,
                    client_context=client_context,
                    style=style
                )
            
            if result.get("success") and result.get("images"):
                return {
                    'image_url': result['images'][0],
                    'status': 'SUCCESS',
                    'method': 'image2image' if reference_image_url else 'text2image',
                    'generation_time': result.get('generation_time', 0)
                }
            else:
                return {
                    'image_url': 'N/A',
                    'status': 'FAILED',
                    'error': result.get('error', 'Unknown error')
                }
                
        except Exception as e:
            print(f"   ❌ Erreur génération image: {e}")
            return {
                'image_url': 'N/A',
                'status': 'CRITICAL_FAILED',
                'error': str(e)
            }
    
    def _map_visual_theme_to_style(self, visual_theme: str) -> str:
        """Map le thème visuel vers un style Leonardo AI"""
        theme_style_map = {
            'promotion_banner': 'marketing',
            'product_spotlight': 'product',
            'category_showcase': 'banner',
            'editorial_article': 'social_media',
            'general_store': 'marketing'
        }
        return theme_style_map.get(visual_theme, 'marketing')
    
    def _calculate_coherence_score(self, ad_copy_data: Dict, image_result: Dict) -> str:
        """
        Calcule un score de cohérence entre ad-copy et image
        """
        score = 0
        
        # L'image a été générée avec succès
        if image_result.get('status') == 'SUCCESS':
            score += 50
        
        # Utilisation d'une référence image
        if image_result.get('method') == 'image2image':
            score += 30
        
        # Ad-copy complet
        if ad_copy_data.get('short_copy') and ad_copy_data.get('long_copy'):
            score += 20
        
        # Classification
        if score >= 80:
            return "🟢 Excellent"
        elif score >= 60:
            return "🟡 Bon"
        elif score >= 40:
            return "🟠 Moyen"
        else:
            return "🔴 Faible"
    
    # ============================================
    # FONCTIONS UTILITAIRES (gardées inchangées)
    # ============================================
    
    def _analyze_products(self) -> Dict:
        """Analyse les produits (inchangé)"""
        products = []
        offers = []
        categories = set()
        prices = []
        
        if not self.rag_system.raw_data:
            return {
                'products': [], 
                'offers': [], 
                'categories': ['Général'],
                'price_range': {'min': 'N/A', 'max': 'N/A', 'avg': 'N/A', 'range': 'Variable'},
                'total_count': 0
            }
        
        site_data = self.rag_system.raw_data.get(self.site_id, {})
        results = site_data.get('results', [])
        
        for page in results:
            if not isinstance(page, dict):
                continue
            
            for product in page.get('products', []):
                if isinstance(product, dict):
                    product_info = self._extract_product_info(product, page.get('url', ''))
                    products.append(product_info)
                    categories.add(product_info['category'])
                    
                    price_num = self._extract_price_number(product.get('price', ''))
                    if price_num:
                        prices.append(price_num)
            
            for product in page.get('promoted_products', []):
                if isinstance(product, dict):
                    product_info = self._extract_product_info(product, page.get('url', ''), is_promoted=True)
                    offers.append(product_info)
                    products.append(product_info)
                    categories.add(product_info['category'])
                    
                    price_num = self._extract_price_number(product.get('price', ''))
                    if price_num:
                        prices.append(price_num)
        
        price_stats = self._calculate_price_stats(prices)
        
        return {
            'products': products,
            'offers': offers,
            'categories': list(categories) if categories else ['Général'],
            'price_range': price_stats,
            'total_count': len(products)
        }
    
    def _extract_product_info(self, product: Dict, page_url: str, is_promoted: bool = False) -> Dict:
        """Extrait info produit (inchangé)"""
        name = product.get('name', 'Produit')
        description = product.get('description', '')[:200]
        price = product.get('price', 'N/A')
        
        category = self._categorize_product(name, description)
        
        has_promotion = bool(
            product.get('is_promoted') or 
            is_promoted or
            'promotion' in description.lower()
        )
        
        return {
            'name': name,
            'description': description,
            'price': price,
            'category': category,
            'has_promotion': has_promotion,
            'url': product.get('product_url', page_url),
            'image': product.get('image', ''),
            'promotion_type': self._detect_promotion_type(product)
        }
    
    def _categorize_product(self, name: str, description: str) -> str:
        """Catégorise produit (inchangé)"""
        text = (name + ' ' + description).lower()
        
        categories_keywords = {
            'Électronique': ['téléphone', 'ordinateur', 'laptop', 'pc', 'tech'],
            'Mode': ['vêtement', 'robe', 'chaussure', 'sac'],
            'Beauté': ['cosmétique', 'maquillage', 'soin', 'parfum'],
            'Maison': ['meuble', 'décoration', 'cuisine'],
            'Sport': ['sport', 'fitness', 'équipement'],
            'Alimentation': ['aliment', 'boisson', 'café'],
        }
        
        for category, keywords in categories_keywords.items():
            if any(kw in text for kw in keywords):
                return category
        
        return 'Autres produits'
    
    def _detect_promotion_type(self, product: Dict) -> str:
        """Détecte type promo (inchangé)"""
        text = (product.get('name', '') + ' ' + product.get('description', '')).lower()
        
        if any(kw in text for kw in ['flash', 'urgent', 'limité']):
            return 'Flash Sale'
        elif any(kw in text for kw in ['réduction', 'solde', 'discount']):
            return 'Réduction'
        elif any(kw in text for kw in ['nouveau', 'new']):
            return 'Nouveauté'
        
        return 'Promotion'
    
    def _extract_price_number(self, price_str: str) -> float:
        """Extrait prix (inchangé)"""
        try:
            numbers = re.findall(r'\d+[.,]\d+|\d+', price_str)
            if numbers:
                return float(numbers[0].replace(',', '.'))
        except:
            pass
        return None
    
    def _calculate_price_stats(self, prices: List[float]) -> Dict:
        """Calcule stats prix (inchangé)"""
        if not prices:
            return {'min': 'N/A', 'max': 'N/A', 'avg': 'N/A', 'range': 'Variable'}
        
        prices_sorted = sorted(prices)
        min_price = prices_sorted[0]
        max_price = prices_sorted[-1]
        avg_price = sum(prices) / len(prices)
        
        if max_price > 500:
            range_type = 'Premium'
        elif max_price > 100:
            range_type = 'Moyen'
        else:
            range_type = 'Économique'
        
        return {
            'min': f"{min_price:.2f}€",
            'max': f"{max_price:.2f}€",
            'avg': f"{avg_price:.2f}€",
            'range': range_type
        }
    
    def _identify_offers(self, product_analysis: Dict) -> List[Dict]:
        """Identifie offres (inchangé)"""
        offers = []
        
        for product in product_analysis['offers']:
            offers.append({
                'type': 'product_offer',
                'product': product,
                'priority': 'high' if product['has_promotion'] else 'medium',
                'promotion_type': product['promotion_type']
            })
        
        return offers
    
    def _generate_strategy_with_gemini(self, duration_weeks: int, posts_per_week: int, product_analysis: Dict, offers_data: List[Dict]) -> Dict:
        """Génère stratégie (simplifié pour l'exemple)"""
        # Implémentation similaire à l'original
        # ...
        return self._create_fallback_strategy(duration_weeks, posts_per_week)
    
    def _create_fallback_strategy(self, duration_weeks: int, posts_per_week: int) -> Dict:
        """Stratégie fallback (inchangé)"""
        days = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
        content_types = ["product_showcase", "offer", "news", "engagement"]
        
        weeks = []
        post_counter = 1
        
        for week_num in range(1, duration_weeks + 1):
            week = {
                "week_number": week_num,
                "theme": f"Semaine {week_num}",
                "days": []
            }
            
            for day_idx in range(posts_per_week):
                content_type = content_types[(post_counter - 1) % len(content_types)]
                target_content = 'produit' if content_type in ['product_showcase', 'engagement'] else ('offre' if content_type == 'offer' else 'actualité')
                
                day_data = {
                    "day": days[day_idx % len(days)],
                    "post_number": post_counter,
                    "content_type": content_type,
                    "theme": f"Post {post_counter}",
                    "target_content": target_content,
                    "optimal_time": ["9:00", "12:00", "18:00"][day_idx % 3]
                }
                
                week["days"].append(day_data)
                post_counter += 1
            
            weeks.append(week)
        
        return {"weeks": weeks}
    
    def _generate_offer_adcopy(self, offer: Dict, day: Dict) -> Dict:
        """Génère ad-copy offre"""
        if offer['type'] == 'product_offer':
            product = offer['product']
            return {
                'short_copy': f"🎁 {product['name'][:40]}... À ne pas manquer! 🔥",
                'medium_copy': f"💰 Offre exclusive!\n\n{product['name']}\n{product['price']}\n\n⏰ Limitée!",
                'long_copy': f"🔥 OFFRE SPÉCIALE!\n\n{product['name']}\n\n{product['description']}\n\nPrix: {product['price']}\n\n✅ Qualité garantie\n👉 Commander",
                'hashtags': ['#Promotion', '#Offre', '#Shopping'],
                'cta': "Profiter de l'offre",
                'emoji': '🎁',
                'visual_theme': 'promotion_banner'
            }
        return self._generate_generic_adcopy(day)
    
    def _generate_product_adcopy(self, product: Dict, day: Dict) -> Dict:
        """Génère ad-copy produit"""
        return {
            'short_copy': f"✨ {product['name'][:40]}... Découvrez! 👇",
            'medium_copy': f"✨ EN VEDETTE: {product['name']}\n\n{product['category']}\n{product['price']}\n\n👉 Voir",
            'long_copy': f"🌟 {product['name'].upper()}\n\n📋 {product['description']}\n\nPrix: {product['price']}\n\n✅ Qualité\n📦 Livraison rapide\n👉 Commander",
            'hashtags': ['#Produit', f"#{product['category']}", '#Shopping'],
            'cta': "Voir le produit",
            'emoji': '⭐',
            'visual_theme': 'product_spotlight'
        }
    
    def _generate_news_adcopy(self, day: Dict, product_analysis: Dict) -> Dict:
        """Génère ad-copy actualité"""
        return {
            'short_copy': "📰 Les tendances du moment à découvrir",
            'medium_copy': "📢 ACTUALITÉS\n\nDécouvrez les dernières tendances\n\n👉 Lire",
            'long_copy': "📰 ACTUALITÉ\n\nPlongez dans l'univers des tendances actuelles!\n\n💡 Conseils experts\n📚 Ressources\n👉 Découvrir",
            'hashtags': ['#Tendances', '#Actualité', '#Conseils'],
            'cta': "Lire l'article",
            'emoji': '📰',
            'visual_theme': 'editorial_article'
        }
    
    def _generate_generic_adcopy(self, day: Dict) -> Dict:
        """Génère ad-copy générique"""
        return {
            'short_copy': "🛍️ Découvrez notre sélection",
            'medium_copy': "Explorez nos meilleurs produits!",
            'long_copy': "Visitez notre boutique pour découvrir nos produits.",
            'hashtags': ['#Shopping', '#Découverte'],
            'cta': "Découvrir",
            'emoji': '🛍️',
            'visual_theme': 'general_store'
        }