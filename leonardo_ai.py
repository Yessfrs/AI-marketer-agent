# leonardo_ai.py
import os
import requests
import time
import base64
from typing import Dict, Optional, List
from dotenv import load_dotenv
load_dotenv()


class LeonardoAIGenerator:
    """Générateur d'images via Leonardo AI API - AVEC URL d'image de référence"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("LEONARDO_API_KEY", "1c895e8a-aad0-4a9a-bf84-9f802d729319")
        self.base_url = "https://cloud.leonardo.ai/api/rest/v1"
        
        if not self.api_key:
            raise ValueError("❌ Clé API Leonardo AI non configurée")
        
        print(f"🎨 Leonardo AI initialisé (avec URL d'image de référence)")
    
    def download_image_from_url(self, image_url: str) -> Optional[str]:
        """
        Télécharge une image depuis une URL et retourne le chemin temporaire
        Version améliorée
        """
        try:
            print(f"📥 Téléchargement image depuis: {image_url}")
            
            # Créer un répertoire temporaire
            temp_dir = "temp_uploads"
            if not os.path.exists(temp_dir):
                os.makedirs(temp_dir)
            
            # Générer un nom de fichier unique
            timestamp = int(time.time())
            filename = f"reference_{timestamp}.jpg"
            temp_path = os.path.join(temp_dir, filename)
            
            # Headers pour éviter les blocages
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
                'Referer': 'https://www.google.com/'
            }
            
            # Télécharger l'image
            response = requests.get(image_url, headers=headers, timeout=30, stream=True)
            response.raise_for_status()
            
            # Vérifier le type de contenu
            content_type = response.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                print(f"⚠️ URL ne pointe pas vers une image: {content_type}")
                # Essayer quand même de sauvegarder
                print("🔄 Tentative de sauvegarde malgré le content-type...")
            
            # Sauvegarder l'image
            with open(temp_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            # Vérifier la taille
            file_size = os.path.getsize(temp_path)
            if file_size > 10 * 1024 * 1024:  # 10MB max
                print(f"❌ Image trop volumineuse: {file_size} bytes")
                os.remove(temp_path)
                return None
            
            print(f"✅ Image téléchargée: {temp_path} ({file_size} bytes)")
            return temp_path
            
        except Exception as e:
            print(f"❌ Erreur téléchargement image: {e}")
            return None
    
    def upload_init_image(self, image_path: str) -> Optional[str]:
        """
        Upload une image via l'endpoint init-image de Leonardo
        Retourne l'ID de l'image uploadée
        """
        try:
            print(f"📤 Upload init image: {image_path}")
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "accept": "application/json"
            }
            
            # Lire et encoder l'image
            with open(image_path, 'rb') as img_file:
                files = {
                    'file': (os.path.basename(image_path), img_file, 'image/jpeg')
                }
                data = {
                    'extension': 'jpg'
                }
                
                response = requests.post(
                    f"{self.base_url}/init-image",
                    files=files,
                    data=data,
                    headers=headers,
                    timeout=30
                )
            
            print(f"📥 Réponse upload: {response.status_code}")
            
            if response.status_code == 200:
                upload_data = response.json()
                init_image_id = upload_data.get("uploadInitImage", {}).get("id")
                print(f"✅ Image uploadée - ID: {init_image_id}")
                return init_image_id
            else:
                print(f"❌ Erreur upload: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ Erreur upload init image: {e}")
            return None
    
    def generate_with_reference_image(self, image_url: str, ad_copy: str, tone: str = "professional",
                                client_context: Dict = None, image_strength: float = 0.5,
                                style: str = "marketing") -> Dict:
        """
        Génère une image en utilisant une URL d'image de référence
        Version améliorée avec fallback
        """
        
        # Essayer d'abord la méthode avancée avec imagePrompt
        print("🔄 Essai méthode avancée (imagePrompt)...")
        result = self.generate_with_reference_image_advanced(image_url, ad_copy, tone, client_context, style)
        
        if result.get("success"):
            return result
        
        # Fallback vers l'ancienne méthode si échec
        print("🔄 Fallback vers méthode standard...")
        return self._generate_with_reference_fallback(image_url, ad_copy, tone, client_context, image_strength, style)

    def generate_with_reference_image_advanced(self, image_url: str, prompt: str, tone: str = "professional", 
                                        client_context: Dict = None, style: str = "marketing") -> Dict:
        """
        Génère une image en utilisant une URL d'image de référence (version avancée)
        Utilise l'approche imagePrompt de l'API Leonardo
        """
        print("🎨 DÉBUT generate_with_reference_image_advanced")
        print(f"🌐 Image URL: {image_url}")
        print(f"📝 Prompt: {prompt}")
        
        try:
            # Étape 1: Télécharger l'image depuis l'URL
            print("📥 Téléchargement de l'image...")
            temp_image_path = self.download_image_from_url(image_url)
            
            if not temp_image_path:
                return {"success": False, "error": "Échec du téléchargement de l'image"}
            
            # Étape 2: Uploader vers Leonardo
            print("📤 Upload vers Leonardo...")
            init_response = requests.post(
                f"{self.base_url}/init-image",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={"extension": "jpg"}
            )
            
            if init_response.status_code != 200:
                return {"success": False, "error": f"Erreur upload: {init_response.text}"}
            
            upload_data = init_response.json()["uploadInitImage"]
            
            # Gérer les fields (peut être string ou dict)
            fields = upload_data["fields"]
            if isinstance(fields, str):
                import json
                fields = json.loads(fields)
            
            # Upload vers S3
            with open(temp_image_path, "rb") as f:
                files = {"file": f}
                s3_response = requests.post(
                    upload_data["url"],
                    data=fields,
                    files=files
                )
                s3_response.raise_for_status()
            
            image_id = upload_data["id"]
            print(f"✅ Image uploadée. ID: {image_id}")
            
            # Étape 3: Construire le prompt amélioré
            final_prompt = self._build_prompt(prompt, tone, client_context, style)
            
            # Étape 4: Génération avec imagePrompt
            print("🎨 Génération avec imagePrompt...")
            payload = {
                "height": 1024,
                "width": 1024,
                "modelId": "6bef9f1b-29cb-40c7-b9df-32b51c1f67d3",  # Modèle spécifique pour imagePrompt
                "prompt": final_prompt,
                "imagePrompts": [image_id],  # ← CLÉ : utiliser imagePrompts au lieu de init_image_id
                "num_images": 1,
                "presetStyle": "CINEMATIC",
                "guidance_scale": 7,
                "num_inference_steps": 30
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "accept": "application/json"
            }
            
            response = requests.post(
                f"{self.base_url}/generations",
                json=payload,
                headers=headers,
                timeout=60
            )
            
            print(f"📥 Réponse génération: {response.status_code}")
            
            if response.status_code != 200:
                return {"success": False, "error": f"Erreur API: {response.status_code} - {response.text}"}
            
            generation_data = response.json()
            generation_id = generation_data.get("sdGenerationJob", {}).get("generationId")
            
            if not generation_id:
                return {"success": False, "error": "ID de génération non reçu"}
            
            print(f"✅ Génération lancée: {generation_id}")
            
            # Attendre la fin
            return self._wait_for_generation(generation_id, headers)
            
        except Exception as e:
            print(f"❌ Erreur génération: {e}")
            import traceback
            print(f"🔍 Détails: {traceback.format_exc()}")
            return {"success": False, "error": str(e)}
        finally:
            # Nettoyer le fichier temporaire
            if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
                os.remove(temp_image_path)
                print("🗑️ Fichier temporaire supprimé")
    
    
    def _generate_with_reference_fallback(self, image_url: str, ad_copy: str, tone: str = "professional",
                                    client_context: Dict = None, image_strength: float = 0.5,
                                    style: str = "marketing") -> Dict:
        """Méthode de fallback utilisant l'ancienne approche"""
        
        temp_image_path = self.download_image_from_url(image_url)
        
        if not temp_image_path:
            return {"success": False, "error": "Échec du téléchargement de l'image"}
        
        try:
            # Uploader l'image
            init_image_id = self.upload_init_image(temp_image_path)
            
            if not init_image_id:
                return {"success": False, "error": "Échec de l'upload de l'image"}
            
            # Construire le prompt
            final_prompt = self._build_prompt(ad_copy, tone, client_context, style)
            
            # Génération avec init_image_id
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "accept": "application/json"
            }
            
            payload = {
                "height": 1024,
                "modelId": "1e60896f-3c26-4296-8ecc-53e2afecc132",
                "prompt": final_prompt,
                "width": 1024,
                "init_image_id": init_image_id,
                "init_strength": image_strength,
                "guidance_scale": 7,
                "num_inference_steps": 30,
                "presetStyle": "LEONARDO",
                "promptMagic": True,
                "promptMagicVersion": "v2",
                "alchemy": True
            }
            
            response = requests.post(
                f"{self.base_url}/generations",
                json=payload,
                headers=headers,
                timeout=60
            )
            
            if response.status_code == 200:
                generation_data = response.json()
                generation_id = generation_data.get("sdGenerationJob", {}).get("generationId")
                
                if generation_id:
                    return self._wait_for_generation(generation_id, headers)
            
            return {"success": False, "error": f"Échec génération: {response.status_code}"}
            
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if os.path.exists(temp_image_path):
                os.remove(temp_image_path)
    
    
    def _try_alternative_configuration(self, init_image_id: str, prompt: str, 
                                    image_strength: float, headers: Dict) -> Dict:
        """
        Tentative avec configuration alternative si la première échoue
        """
        try:
            print("🔄 Essai configuration alternative...")
            
            # Configuration alternative sans ControlNet
            payload_alt = {
                "height": 1024,
                "modelId": "ac614f96-1082-45bf-be9d-757f2d31c174",  # Modèle alternatif
                "prompt": prompt,
                "width": 1024,
                "init_image_id": init_image_id,
                "init_strength": image_strength,
                "guidance_scale": 7,
                "num_inference_steps": 30,
                "presetStyle": "LEONARDO",
                "promptMagic": True,
                "promptMagicVersion": "v2",
                "alchemy": True,
                "photoReal": False
                # Pas de ControlNet
            }
            
            response = requests.post(
                f"{self.base_url}/generations",
                json=payload_alt,
                headers=headers,
                timeout=60
            )
            
            if response.status_code == 200:
                generation_data = response.json()
                generation_id = generation_data.get("sdGenerationJob", {}).get("generationId")
                
                if generation_id:
                    print(f"✅ Génération alternative lancée: {generation_id}")
                    return self._wait_for_generation(generation_id, headers)
            
            return {"success": False, "error": f"Échec configuration alternative: {response.status_code}"}
            
        except Exception as e:
            return {"success": False, "error": f"Erreur configuration alternative: {str(e)}"}
    
    def generate_without_reference(self, 
                                 ad_copy: str,
                                 tone: str = "professional",
                                 client_context: Dict = None,
                                 style: str = "marketing") -> Dict:
        """
        Génère une image SANS référence (création from scratch)
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "accept": "application/json"
        }
        
        # Construire le prompt
        final_prompt = self._build_prompt(ad_copy, tone, client_context, style)
        
        payload = {
            "prompt": final_prompt,
            "negative_prompt": "blurry, low quality, distorted, amateur, watermark, text",
            "modelId": "ac614f96-1082-45bf-be9d-757f2d31c174",
            "width": 1024,
            "height": 1024,
            "num_images": 1,
            "guidance_scale": 7,
            "num_inference_steps": 30,
            "presetStyle": "LEONARDO",
            "promptMagic": True,
            "promptMagicVersion": "v2",
            "alchemy": True
        }
        
        try:
            print(f"🎨 Génération SANS référence...")
            print(f"📝 Prompt: {final_prompt}")
            
            response = requests.post(
                f"{self.base_url}/generations",
                json=payload,
                headers=headers,
                timeout=30
            )
            
            print(f"📥 Réponse: {response.status_code}")
            
            if response.status_code == 200:
                generation_data = response.json()
                generation_id = generation_data.get("sdGenerationJob", {}).get("generationId")
                
                if generation_id:
                    print(f"✅ Génération lancée: {generation_id}")
                    return self._wait_for_generation(generation_id, headers)
                else:
                    return {"success": False, "error": "ID non reçu"}
            else:
                return {"success": False, "error": f"Status {response.status_code}: {response.text}"}
                
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _build_prompt(self, ad_copy: str, tone: str, client_context: Dict, style: str) -> str:
        """Construit le prompt final avec adaptation aux styles spécifiques"""
        prompt_parts = [ad_copy]
        
        # Ton avec mappings améliorés
        tone_mappings = {
            "professional": "professional, corporate, business, elegant, sophisticated, clean, modern",
            "casual": "casual, friendly, approachable, modern, trendy, relatable, authentic", 
            "luxury": "luxury, premium, exclusive, high-end, elegant, sophisticated, refined",
            "playful": "playful, fun, energetic, vibrant, colorful, dynamic, engaging",
            "minimalist": "minimalist, clean, simple, modern, elegant, uncluttered, focused",
            "bold": "bold, striking, impactful, eye-catching, dramatic, powerful, confident",
            "elegant": "elegant, refined, graceful, classy, sophisticated, timeless, polished"
        }
        prompt_parts.append(tone_mappings.get(tone.lower(), tone))
        
        # Contexte client enrichi
        if client_context:
            industry = client_context.get('industry', '')
            brand_voice = client_context.get('brand_voice', '')
            brand_values = client_context.get('brand_values', [])
            
            if industry:
                prompt_parts.append(f"{industry} industry, {industry} theme")
            
            if brand_voice:
                prompt_parts.append(f"{brand_voice} brand voice, {brand_voice} style")
            
            if brand_values and isinstance(brand_values, list):
                prompt_parts.extend(brand_values)
        
        # Style visuel avec adaptations spécifiques aux réseaux sociaux
        style_mappings = {
            "marketing": "marketing advertisement, professional photography, commercial, product placement, call to action, conversion optimized",
            "social_media": "social media post, Instagram style, engaging visual, mobile optimized, square format, shareable content, viral potential, social media marketing",
            "banner": "web banner, digital advertisement, clean layout, horizontal format, call to action, website header, online advertising",
            "poster": "poster design, graphic design, typography, print ready, high resolution, eye-catching, informational",
            "product": "product photography, professional lighting, e-commerce, white background, detailed, commercial product shot, online store"
        }
        
        style_prompt = style_mappings.get(style, "professional marketing")
        prompt_parts.append(style_prompt)
        
        # Dimensions et format selon le style
        if style == "social_media":
            prompt_parts.extend(["square format", "mobile optimized", "Instagram ready"])
        elif style == "banner":
            prompt_parts.extend(["horizontal format", "web banner", "header image"])
        
        # Qualité améliorée
        prompt_parts.extend([
            "high quality", 
            "4K resolution", 
            "detailed", 
            "sharp focus", 
            "professional photography",
            "excellent composition",
            "perfect lighting"
        ])
        
        # Éviter les éléments indésirables
        prompt_parts.extend([
            "no blur", 
            "no distortion", 
            "no watermark", 
            "no signature", 
            "no text overlay"
        ])
        
        final_prompt = ", ".join(prompt_parts)
        print(f"🎯 Prompt final construit: {final_prompt}")
        
        return final_prompt
    
    def _wait_for_generation(self, generation_id: str, headers: Dict, 
                        max_wait: int = 180, poll_interval: int = 5) -> Dict:
        """Attend la fin de la génération avec meilleur suivi"""
        start_time = time.time()
        
        print(f"⏳ Attente génération {generation_id}... (max {max_wait}s)")
        
        while time.time() - start_time < max_wait:
            try:
                response = requests.get(
                    f"{self.base_url}/generations/{generation_id}",
                    headers=headers,
                    timeout=15
                )
                
                if response.status_code != 200:
                    print(f"⚠️ Erreur statut: {response.status_code}")
                    time.sleep(poll_interval)
                    continue
                
                data = response.json()
                generation = data.get("generations_by_pk", {})
                status = generation.get("status")
                
                elapsed = int(time.time() - start_time)
                print(f"⏳ Statut: {status} ({elapsed}s)")
                
                if status == "COMPLETE":
                    images = generation.get("generated_images", [])
                    if images:
                        image_urls = [img.get("url") for img in images if img.get("url")]
                        if image_urls:
                            print(f"✅ {len(image_urls)} image(s) générée(s) en {elapsed}s!")
                            return {
                                "success": True,
                                "images": image_urls,
                                "generation_id": generation_id,
                                "generation_time": elapsed
                            }
                    return {"success": False, "error": "Aucune image générée"}
                
                elif status == "FAILED":
                    error_msg = generation.get("failure_reason", "Raison inconnue")
                    print(f"❌ Génération échouée: {error_msg}")
                    return {"success": False, "error": f"Échec: {error_msg}"}
                
                time.sleep(poll_interval)
                
            except Exception as e:
                print(f"⚠️ Erreur vérification statut: {e}")
                time.sleep(poll_interval)
        
        return {"success": False, "error": f"Timeout après {max_wait} secondes"}
    
    def test_connection(self) -> Dict:
        """Teste la connexion à l'API Leonardo"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "accept": "application/json"
        }
        
        try:
            response = requests.get(
                f"{self.base_url}/me",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                user_data = response.json()
                return {
                    "success": True,
                    "user": user_data.get("user", {}).get("username", "Inconnu")
                }
            else:
                return {
                    "success": False,
                    "error": f"Status {response.status_code}: {response.text}"
                }
                
        except Exception as e:
            return {"success": False, "error": str(e)}