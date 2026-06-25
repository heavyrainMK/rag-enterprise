# RAG Enterprise

Système **RAG** (Retrieval-Augmented Generation) d'entreprise, **100 % local**,
permettant d'interroger des documents internes (PDF / TXT) via une IA. Les
réponses sont générées uniquement à partir des documents fournis, avec citation
des sources dans le texte.

> Projet de fin de licence (L3 Informatique) - **Maxim Khomenko**.

---

## Sommaire

- [Aperçu](#aperçu)
- [Architecture](#architecture)
- [Stack technique](#stack-technique)
- [Structure du projet](#structure-du-projet)
- [Prérequis](#prérequis)
- [Installation et lancement (développement)](#installation-et-lancement-développement)
- [Lancement avec Docker](#lancement-avec-docker)
- [Configuration](#configuration)
- [Tests](#tests)
- [Sécurité](#sécurité)
- [Déploiement en production](#déploiement-en-production)

---

## Aperçu

L'application répond aux questions des utilisateurs en s'appuyant **exclusivement**
sur une base documentaire interne. Chaque utilisateur dispose de ses propres
documents privés ; un espace partagé, géré par les administrateurs, est visible
de tous.

Fonctionnalités principales :

- **Chat documentaire en streaming** : la réponse s'affiche token par token.
- **Isolation par utilisateur** : documents et historique privés ; un utilisateur
  ne voit jamais les données d'un autre. Un administrateur dispose d'une vue
  globale sur les statistiques.
- **Gestion des documents** : téléversement (privé ou partagé), liste et
  suppression, avec réindexation automatique.
- **Tableau de bord administrateur** : statistiques système, répartition des
  utilisateurs, activité récente, sources les plus citées.
- **Citation des sources** : chaque réponse indique les documents utilisés pour
  la générer.

---

## Architecture

Le backend FastAPI expose l'API REST **et** sert le frontend React compilé
(fichiers statiques). Un seul service applicatif est donc nécessaire ; Ollama
tourne séparément sur la machine hôte.

```
                 ┌────────────────────────────────────────┐
                 │           Backend FastAPI :8000          │
                 │                                          │
  Navigateur ───▶│  API REST (/auth, /chat, /documents…)    │
                 │  + frontend React statique (build Vite)  │
                 │                                          │
                 │   ┌──────────┐   ┌──────────┐            │
                 │   │ ChromaDB │   │  SQLite  │            │
                 │   │(vecteurs)│   │ (comptes │            │
                 │   └──────────┘   │ + histo.)│            │
                 │                  └──────────┘            │
                 └──────────────┬───────────────────────────┘
                                │
                                ▼
                       ┌─────────────────┐
                       │  Ollama (hôte)  │
                       │  Llama 3.2 /    │
                       │  Mistral        │
                       └─────────────────┘
```

En développement, le frontend tourne sur son propre serveur Vite et les appels
API sont redirigés vers le backend via un proxy (voir `frontend/vite.config.ts`).

---

## Stack technique

**Backend**

- Python 3.12, FastAPI, Uvicorn
- LangChain (écosystème 0.2.x) + ChromaDB (base vectorielle)
- Embeddings : `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- LLM : Ollama en local (modèle configurable : Llama 3.2 pour la rapidité,
  Mistral pour la qualité ; OpenAI possible en option)
- SQLite + SQLAlchemy (historique des conversations, comptes)
- Authentification JWT (python-jose + bcrypt)

**Frontend**

- React 19 + TypeScript
- Vite (build et serveur de développement)
- Tailwind CSS
- recharts (graphiques du tableau de bord)

---

## Structure du projet

```
rag_project/
├── src/
│   ├── core/
│   │   ├── ingest.py          # Ingestion : chargement, découpage, embeddings
│   │   ├── rag.py             # Cœur RAG : recherche multi-collections + génération
│   │   └── database.py        # Persistance SQLite (conversations, comptes)
│   └── api/
│       ├── main.py            # Point d'entrée FastAPI
│       ├── auth.py            # JWT, hachage bcrypt, dépendances de sécurité
│       ├── schemas.py         # Modèles Pydantic partagés
│       └── routes/
│           ├── auth_routes.py # Inscription, connexion, profil
│           ├── documents.py   # Téléversement / liste / suppression
│           ├── history.py     # Historique des conversations
│           ├── stream.py      # Streaming SSE (réponse token par token)
│           └── admin.py       # Statistiques (réservé aux admins)
├── frontend/                  # Application React (Vite)
│   ├── src/
│   │   ├── App.tsx            # Composant racine + vues (chat, documents, historique)
│   │   ├── VueAdmin.tsx       # Tableau de bord administrateur
│   │   ├── api.ts             # Couche d'accès à l'API
│   │   ├── index.css          # Thème (Aurora / glassmorphism)
│   │   └── main.tsx           # Point d'entrée React
│   ├── public/                # favicon
│   ├── index.html
│   ├── vite.config.ts         # Config Vite + proxy de développement
│   ├── tailwind.config.js
│   └── package.json
├── tests/                     # Suite de tests pytest
├── data/
│   ├── shared/                # Documents partagés (visibles par tous)
│   └── users/<nom>/           # Documents privés par utilisateur
├── vectorstore/               # Index ChromaDB (généré à l'ingestion)
├── requirements.txt
├── docker-compose.yml
├── Dockerfile                 # Build multi-stage (frontend + backend, torch CPU-only)
└── .env.example               # Modèle de configuration
```

---

## Prérequis

- **Python 3.12**
- **Node.js 20** (pour le frontend)
- **Ollama** installé sur la machine, avec un modèle :

  ```bash
  ollama pull llama3.2     # rapide (recommandé sur CPU)
  ollama pull mistral      # plus lent mais plus précis
  ```

---

## Installation et lancement (développement)

Deux terminaux : un pour le backend, un pour le frontend.

### 1. Backend

```bash
# Environnement virtuel
python3 -m venv .venv
source .venv/bin/activate

# Dépendances
pip install -r requirements.txt

# Configuration
cp .env.example .env
# Éditer .env pour définir JWT_SECRET_KEY (voir section Configuration)

# Ingestion initiale des documents présents dans data/
python3 -m src.core.ingest

# Démarrage de l'API
uvicorn src.api.main:app --port 8000
```

### 2. Frontend

```bash
cd frontend
npm install
npm run dev
```

Le serveur de développement Vite affiche l'URL locale au démarrage (par défaut
`http://localhost:5173`) et redirige les appels API vers le backend sur le
port 8000.

### 3. Premier administrateur

Tant qu'aucun compte n'existe, créer le premier administrateur via la route
dédiée (remplacer les identifiants par les vôtres) :

```bash
curl -X POST http://localhost:8000/auth/register/first-admin \
  -H "Content-Type: application/json" \
  -d '{"nom_utilisateur":"VOTRE_NOM","mot_de_passe":"VOTRE_MOT_DE_PASSE","role":"admin"}'
```

Se connecter ensuite via l'interface web. La documentation interactive de l'API
est disponible sur `http://localhost:8000/docs`.

---

## Lancement avec Docker

Le `Dockerfile` compile le frontend (étape Node) puis l'embarque dans l'image
Python ; FastAPI sert alors l'API et le frontend sur un port unique. L'image
installe la version CPU de PyTorch (l'inférence ne nécessite pas de GPU), ce qui
allège fortement sa taille.

```bash
# Construction
docker compose build

# Démarrage
docker compose up
```

L'application est accessible sur `http://localhost:8000`.

### Accès à Ollama depuis le conteneur (Linux)

Ollama tourne sur l'**hôte**, pas dans Docker. Le conteneur y accède via
`host.docker.internal` (configuré dans `docker-compose.yml`). Sur Linux, deux
réglages sont nécessaires pour que cet accès fonctionne :

**1. Ollama doit écouter sur toutes les interfaces** (pas seulement localhost) :

```bash
sudo systemctl edit ollama
```
Ajouter :
```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```
Puis :
```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

**2. Le pare-feu doit autoriser le port 11434 depuis les réseaux Docker** :

```bash
sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp
sudo ufw reload
```

### Premier administrateur (Docker)

La base de données du conteneur démarre vide (sauf si `rag_history.db` est monté
en volume). Créer le premier administrateur :

```bash
curl -X POST http://localhost:8000/auth/register/first-admin \
  -H "Content-Type: application/json" \
  -d '{"nom_utilisateur":"VOTRE_NOM","mot_de_passe":"VOTRE_MOT_DE_PASSE","role":"admin"}'
```

Pour conserver les comptes entre deux lancements, ajouter le montage de la base
aux volumes du service dans `docker-compose.yml` :
```yaml
      - ./rag_history.db:/app/rag_history.db
```

---

## Configuration

Les variables sont définies dans un fichier `.env` (copié depuis `.env.example`).

| Variable | Rôle | Défaut |
|---|---|---|
| `ENV` | `development` ou `production` | `development` |
| `JWT_SECRET_KEY` | Clé de signature des jetons JWT (**obligatoire en prod**) | - |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Durée de validité d'un jeton (minutes) | `480` |
| `CORS_ORIGINS` | Origines autorisées, séparées par des virgules | localhost |
| `OLLAMA_MODEL` | Modèle Ollama à utiliser (`llama3.2`, `mistral`…) | `mistral` |
| `OLLAMA_BASE_URL` | URL du serveur Ollama | `http://localhost:11434` |
| `USE_OPENAI` | `true` pour basculer sur OpenAI | `false` |
| `OPENAI_API_KEY` | Clé API OpenAI (si `USE_OPENAI=true`) | - |
| `OPENAI_MODEL` | Modèle OpenAI | `gpt-4o-mini` |

Générer une clé JWT forte :

```bash
openssl rand -hex 32
```

---

## Tests

La suite de tests couvre l'authentification, la base de données, l'ingestion,
le cœur RAG, les routes documents et le streaming.

```bash
pytest          # exécute la suite complète
pytest -v       # détail test par test
```

> Le dossier `data/` contient un jeu de documents de démonstration permettant de
> tester le système immédiatement après l'ingestion.

---

## Sécurité

- **Authentification JWT** sur toutes les routes (sauf `/health` et `/auth/login`).
- **Hachage des mots de passe** avec bcrypt.
- **Isolation par utilisateur** : chaque utilisateur n'accède qu'à ses propres
  documents et conversations ; les accès non autorisés renvoient 404 (sans
  révéler l'existence de la ressource).
- **Protection contre le path traversal** sur les noms de fichiers téléversés.
- **Lecture en streaming** des téléversements (limite de taille, pas de
  saturation mémoire).
- **CORS restreint** aux origines configurées.
- **Vérification au démarrage** : en production, l'application refuse de démarrer
  si la clé JWT est faible ou si CORS autorise toutes les origines.

---

## Déploiement en production

Compiler le frontend, puis lancer le backend avec les variables sensibles
définies :

```bash
# Build du frontend (servi ensuite en statique par FastAPI)
cd frontend && npm run build && cd ..

# Lancement
ENV=production \
JWT_SECRET_KEY=$(openssl rand -hex 32) \
CORS_ORIGINS=https://mondomaine.fr \
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Penser à monter `data/` et `vectorstore/` sur un stockage persistant, et à ne
jamais committer le fichier `.env` réel (voir `.gitignore`).