##############################################################
# Nom ......... : main.py
# Rôle ........ : Point d'entrée de l'API FastAPI RAG Enterprise.
#                 Définit l'application, les middlewares, la
#                 gestion des erreurs et les routes principales
#                 (/health, /chat). Branche tous les routeurs.
# Auteur ...... : Maxim Khomenko
# Version ..... : V5.1.0 du 22/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : uvicorn src.api.main:app --reload --port 8000
# Dépendances . : fastapi, uvicorn, sqlalchemy, src.core.*, src.api.*
##############################################################

import logging
import os
import shutil
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.schemas import RequeteChat, ReponseChat, ReponseSante
from src.api.auth import utilisateur_courant, secret_est_faible
from src.api.routes.documents import router as routeur_documents
from src.api.routes.history import router as routeur_historique
from src.api.routes.auth_routes import router as routeur_auth
from src.api.routes.stream import router as routeur_stream
from src.api.routes.admin import router as routeur_admin
from src.core.database import creer_base, get_db, sauvegarder_conversation
from src.core.rag import repondre, charger_vectorstore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENV: str = os.getenv("ENV", "development").lower()
EN_PRODUCTION: bool = ENV == "production"

_ORIGINES_DEFAUT = "http://localhost:8000,http://localhost:8001,http://127.0.0.1:8001"
ORIGINES_CORS: list = [
    origine.strip()
    for origine in os.getenv("CORS_ORIGINS", _ORIGINES_DEFAUT).split(",")
    if origine.strip()
]


def verifier_securite():
    """
    Vérifie la configuration sensible au démarrage.

    En production : refuse de démarrer si le secret JWT est faible ou si
    CORS est ouvert à toutes les origines. En développement : simple
    avertissement.
    """
    cors_ouvert = "*" in ORIGINES_CORS

    problemes = []
    if secret_est_faible():
        problemes.append("JWT_SECRET_KEY non défini ou laissé à sa valeur par défaut")
    if cors_ouvert:
        problemes.append("CORS_ORIGINS autorise toutes les origines ('*')")

    if not problemes:
        return

    message = "Configuration non sûre : " + " ; ".join(problemes)
    if EN_PRODUCTION:
        raise RuntimeError(
            message + ". Définissez ces variables d'environnement avant de "
            "démarrer en production."
        )
    logger.warning("%s. Toléré en développement (ENV=%s).", message, ENV)


def migrer_anciens_documents():
    """Copie les documents de data/ vers data/shared/ si besoin (compatibilité)."""
    from src.core.ingest import DOSSIER_PARTAGE, EXTENSIONS_AUTORISEES, DOSSIER_DATA

    DOSSIER_PARTAGE.mkdir(parents=True, exist_ok=True)
    if not DOSSIER_DATA.exists():
        return

    migres = 0
    for f in DOSSIER_DATA.iterdir():
        if f.is_file() and f.suffix.lower() in EXTENSIONS_AUTORISEES:
            dest = DOSSIER_PARTAGE / f.name
            if not dest.exists():
                shutil.copy2(f, dest)
                migres += 1
                logger.info("Migration : %s → shared/", f.name)
    if migres:
        logger.info("Migration terminée : %d fichier(s) déplacé(s) vers shared/.", migres)


def prechauffer_modeles():
    """
    Précharge le modèle d'embedding et la collection partagée au démarrage.

    Sans cela, le modèle (plusieurs secondes à charger) serait instancié
    lors de la première requête d'un utilisateur, qui subirait toute
    l'attente. En le chargeant ici, ce coût est payé au démarrage du
    serveur, où personne ne l'attend.

    Les erreurs sont seulement journalisées : un index encore absent
    (avant la première ingestion) ne doit pas empêcher l'API de démarrer.
    """
    try:
        from src.core.ingest import charger_modele_embedding
        charger_modele_embedding()
        logger.info("Modèle d'embedding préchargé.")
    except Exception as exc:
        logger.warning("Préchargement du modèle ignoré : %s", exc)

    try:
        vectorstore = charger_vectorstore()
        nombre = vectorstore._collection.count()
        logger.info("Collection partagée préchargée (%d morceaux).", nombre)
    except Exception as exc:
        logger.warning("Préchargement de la collection ignoré : %s", exc)


@asynccontextmanager
async def lifespan(app):
    """
    Cycle de vie de l'application.
    Au démarrage : vérifie la sécurité, crée la base, migre les anciens
    documents, puis préchauffe les modèles pour accélérer le premier appel.
    """
    verifier_securite()
    creer_base()
    migrer_anciens_documents()
    prechauffer_modeles()
    logger.info("Application démarrée (ENV=%s).", ENV)
    yield
    logger.info("Application arrêtée.")


app = FastAPI(
    title="RAG Enterprise API",
    description=(
        "API RAG documentaire sécurisée.\n\n"
        "**Authentification :** toutes les routes (sauf `/health` et `/auth/login`) "
        "nécessitent un jeton JWT.\n\n"
        "**Première utilisation :** créer un admin via `POST /auth/register/first-admin`, "
        "puis se connecter via `POST /auth/login`."
    ),
    version="5.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_CORS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def journaliser_requetes(request, call_next):
    """Journalise chaque requête avec sa durée."""
    debut = time.perf_counter()
    reponse = await call_next(request)
    duree = time.perf_counter() - debut
    logger.info(
        "%s %s → %d (%.2fs)",
        request.method, request.url.path,
        reponse.status_code, duree,
    )
    return reponse


@app.exception_handler(Exception)
async def gestionnaire_erreurs(request, exc):
    """Capture toute exception non gérée et retourne une 500 propre."""
    logger.error("Erreur non gérée : %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur."},
    )


# ---------------------------------------------------------------------------
# Routeurs
# ---------------------------------------------------------------------------
app.include_router(routeur_auth)
app.include_router(routeur_documents)
app.include_router(routeur_historique)
app.include_router(routeur_stream)
app.include_router(routeur_admin)


# ---------------------------------------------------------------------------
# Routes principales
# ---------------------------------------------------------------------------
@app.get("/health", response_model=ReponseSante, tags=["Monitoring"])
async def sante():
    """Route publique - état du service + nombre de morceaux indexés."""
    try:
        vectorstore = charger_vectorstore()
        nombre = vectorstore._collection.count()
        return ReponseSante(statut="ok", nb_documents=nombre)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/chat", response_model=ReponseChat, tags=["RAG"])
async def chat(
    requete: RequeteChat,
    db=Depends(get_db),
    utilisateur=Depends(utilisateur_courant),
):
    """
    Système RAG sécurisé. Nécessite un jeton JWT valide.
    La réponse est automatiquement sauvegardée dans l'historique.
    """
    logger.info(
        "POST /chat | utilisateur='%s' | question='%s'",
        utilisateur.nom_utilisateur,
        requete.question[:60],
    )

    try:
        reponse_rag = repondre(requete.question, nom_utilisateur=str(utilisateur.nom_utilisateur))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Erreur RAG : %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Le service LLM est indisponible. Vérifiez qu'Ollama est lancé.",
        ) from exc

    sauvegarder_conversation(
        db=db,
        question=requete.question,
        reponse=reponse_rag.reponse,
        sources=reponse_rag.sources,
        session_id=requete.session_effective(utilisateur.nom_utilisateur),
        utilisateur=str(utilisateur.nom_utilisateur),
    )

    return ReponseChat(reponse=reponse_rag.reponse, sources=reponse_rag.sources)


# ---------------------------------------------------------------------------
# Service du frontend React (build statique)
# ---------------------------------------------------------------------------
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DOSSIER_FRONTEND = Path("frontend/dist")

if DOSSIER_FRONTEND.exists():
    # 1) Les assets compilés (JS/CSS/images) sont servis sur /assets.
    #    Vite génère ce sous-dossier ; le monter ici n'interfère avec
    #    aucune route de l'API.
    dossier_assets = DOSSIER_FRONTEND / "assets"
    if dossier_assets.exists():
        app.mount(
            "/assets",
            StaticFiles(directory=str(dossier_assets)),
            name="assets",
        )

    # 2) Catch-all : toute requête GET qui n'a pas été captée par une route
    #    API ci-dessus renvoie index.html (l'app React prend alors le relais).
    #    Comme cette route est déclarée en DERNIER, FastAPI teste d'abord
    #    /health, /chat, /docs, /auth/*, etc. — elle ne les masque pas.
    @app.get("/{chemin_complet:path}", include_in_schema=False)
    async def servir_frontend(chemin_complet: str):
        # Si le client demande un fichier réel présent dans dist/ (favicon,
        # icons, manifest…), on le sert tel quel ; sinon on renvoie index.html.
        fichier = DOSSIER_FRONTEND / chemin_complet
        if chemin_complet and fichier.is_file():
            return FileResponse(str(fichier))
        return FileResponse(str(DOSSIER_FRONTEND / "index.html"))