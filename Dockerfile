# =============================================================
# Dockerfile - RAG Enterprise
#
# Build en deux étapes :
#   1. étape "frontend" : compile le frontend React (Vite) en
#      fichiers statiques (frontend/dist).
#   2. étape finale (Python) : installe le backend FastAPI et
#      copie le build React, que main.py sert sur le port 8000.
#
# Un seul service applicatif : FastAPI sert à la fois l'API et
# le frontend statique. Ollama tourne sur l'hôte.
# =============================================================

# --- Étape 1 : build du frontend React ---
FROM node:20-slim AS frontend

WORKDIR /frontend

# Copier d'abord les manifestes pour profiter du cache Docker
COPY frontend/package*.json ./
RUN npm ci

# Puis le code source et build
COPY frontend/ ./
RUN npm run build


# --- Étape 2 : image finale Python ---
FROM python:3.12-slim

LABEL maintainer="RAG Enterprise"
LABEL description="Système RAG documentaire interne"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# --- Dépendances système ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# --- Dépendances Python ---
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install torch==2.4.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install -r requirements.txt

# --- Code source backend ---
COPY src/ ./src/
COPY .env.example .env

# --- Build React récupéré depuis l'étape 1 ---
# main.py sert ce dossier (frontend/dist) en statique sur le port 8000.
COPY --from=frontend /frontend/dist ./frontend/dist

# Créer les dossiers de données (montés via volumes en prod)
RUN mkdir -p data vectorstore

# Un seul port : FastAPI (API + frontend statique)
EXPOSE 8000