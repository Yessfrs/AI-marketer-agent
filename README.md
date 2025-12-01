# AI-marketer-agent

Création des postes sur les réseaux sociaux de façon automatisé, agent d’intelligence artificielle marketing conçu pour aider les entreprises à automatiser leurs campagnes, générer du contenu intelligent, analyser leurs audiences et gérer leurs communications via plusieurs canaux (WhatsApp, Web, n8n, API…).

Ce projet exploite les technologies modernes de LLM, RAG, scraping web, automatisations n8n, et intégration API pour créer un véritable assistant marketing autonome.

Fonctionnalités Principales : 

1. Agent IA Marketing Intelligent

Génération de contenu marketing (offres, promotions, descriptions produit…)

Création automatique de calendriers éditoriaux

Analyse du public cible et segmentation

Suggestions d’optimisation marketing basées sur vos données

2. Intégration Multicanale

WhatsApp (API Business ou providers tiers)

Frontend Web / chatbot intégré

n8n pour automatiser les workflows

API REST pour integrations externes

3. Automatisations Marketing

Envoi automatique de messages promotionnels

Scraping intelligent pour collecter des données (concurrents, tendances…)

Programmation de campagnes

Mise à jour automatique du calendrier éditorial

4. Moteur RAG (Retrieval-Augmented Generation)

Base de connaissances dynamique

Support pour PDF, TXT, ou liens web

Réponses basées sur les documents fournis par l’utilisateur

Ai-Marketer/
│
├── backend/              # Backend Python / Flask 
│   ├── app.py            # API principale
│   ├── rag/              # Moteur RAG (embeddings + vector store)
│   ├── scraping/         # Scripts de scraping
│   ├── workflows/        # Automatisations n8n connectées
│   └── models/           # Intégration LLM (Gemini)
│
├── frontend/             # Interface utilisateur (HTML)
│   ├── components/       # UI du chatbot
│   └── pages/
│
└── README.md             # Documentation du projet

Technologies Utilisées : 

Backend : Python (Flask)

Gemini

SentenceTransformers (embeddings)

FAISS (vector store)

MongoDB 

API REST

Automatisation + Webhooks (N8N)

API WhatsApp Cloud (Meta)

📲 Fonctionnement Général : 

L’utilisateur pose une question ou soumet une demande (WhatsApp, Web…)

Le backend appelle :

Le moteur RAG si une base de connaissance est disponible

Le modèle LLM pour produire une réponse contextuelle

L’agent choisit une action :

Générer un contenu marketing

Scraper un site

Mettre à jour le calendrier éditorial

Envoyer un message automatisé

n8n exécute les tâches automatiques

La réponse est envoyée à l’utilisateur via whatsApp Business.
