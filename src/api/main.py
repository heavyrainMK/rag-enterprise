##############################################################
# Nom ......... : main.py
# Rôle ........ : Point d'entrée de l'API FastAPI RAG Enterprise.
#                 Définit l'application, les middlewares, la
#                 gestion des erreurs et les routes principales
#                 (/health, /chat). Branche tous les routeurs.
# Auteur ...... : Maxim Khomenko
# Version ..... : V5.2.0 du 27/06/2026
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
# On importe chaque routeur sous un alias parlant. Regrouper les routes par
# fichier (documents, historique, auth, streaming, admin) garde main.py lisible :
# ce point d'entrée se contente d'assembler des briques définies ailleurs.
from src.api.routes.documents import router as routeur_documents
from src.api.routes.history import router as routeur_historique
from src.api.routes.auth_routes import router as routeur_auth
from src.api.routes.stream import router as routeur_stream
from src.api.routes.admin import router as routeur_admin
from src.core.database import creer_base, get_db, sauvegarder_conversation
from src.core.rag import repondre, charger_vectorstore, MODELE_OLLAMA

# On configure la journalisation une seule fois, ici au point d'entrée : tous
# les modules qui font getLogger(__name__) hériteront de ce format et de ce
# niveau. L'horodatage et le nom du logger facilitent le diagnostic en prod.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# L'environnement pilote plusieurs comportements de sécurité. On le lit une
# fois et on en déduit un booléen EN_PRODUCTION, plus lisible que de comparer
# la chaîne partout. lower() rend la comparaison insensible à la casse.
ENV: str = os.getenv("ENV", "development").lower()
EN_PRODUCTION: bool = ENV == "production"

# Les origines CORS autorisées sont configurables par variable d'environnement.
# On part d'une liste de valeurs locales par défaut (utiles en développement),
# puis on découpe sur les virgules en nettoyant chaque entrée et en écartant
# les vides : ainsi « a, ,b » ne produit pas d'origine vide parasite.
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
    # Principe « fail-fast » : mieux vaut refuser de démarrer qu'exposer un
    # service mal configuré. On accumule TOUS les problèmes avant de décider,
    # pour les signaler d'un coup plutôt qu'un par un à chaque relance.
    cors_ouvert = "*" in ORIGINES_CORS

    problemes = []
    if secret_est_faible():
        problemes.append("JWT_SECRET_KEY non défini ou laissé à sa valeur par défaut")
    if cors_ouvert:
        # Un CORS ouvert (« * ») combiné aux cookies/identifiants est une
        # faille classique : n'importe quel site pourrait appeler l'API au nom
        # de l'utilisateur. Interdit en production.
        problemes.append("CORS_ORIGINS autorise toutes les origines ('*')")

    if not problemes:
        return

    message = "Configuration non sûre : " + " ; ".join(problemes)
    # La même anomalie est BLOQUANTE en production mais seulement avertie en
    # développement : on veut un poste de dev souple, sans jamais tolérer
    # l'insécurité en production. C'est tout l'intérêt du booléen EN_PRODUCTION.
    if EN_PRODUCTION:
        raise RuntimeError(
            message + ". Définissez ces variables d'environnement avant de "
            "démarrer en production."
        )
    logger.warning("%s. Toléré en développement (ENV=%s).", message, ENV)


def migrer_anciens_documents():
    """Copie les documents de data/ vers data/shared/ si besoin (compatibilité)."""
    # Migration de compatibilité : d'anciennes versions rangeaient les
    # documents directement dans data/ ; le modèle actuel les attend dans
    # data/shared/. On rapatrie donc l'existant au démarrage, une fois, pour
    # ne pas perdre les fichiers déjà déposés sous l'ancienne organisation.
    # L'import est local à la fonction pour éviter un import circulaire au
    # chargement du module et ne charger ingest que si la migration tourne.
    from src.core.ingest import DOSSIER_PARTAGE, EXTENSIONS_AUTORISEES, DOSSIER_DATA

    DOSSIER_PARTAGE.mkdir(parents=True, exist_ok=True)
    if not DOSSIER_DATA.exists():
        return

    migres = 0
    for f in DOSSIER_DATA.iterdir():
        # On ne migre que les fichiers d'extension reconnue, et seulement s'ils
        # n'existent pas déjà côté shared : la migration est idempotente, on
        # peut redémarrer le serveur sans dupliquer ni écraser quoi que ce soit.
        if f.is_file() and f.suffix.lower() in EXTENSIONS_AUTORISEES:
            dest = DOSSIER_PARTAGE / f.name
            if not dest.exists():
                # copy2 préserve les métadonnées (dates) ; on copie sans
                # supprimer l'original, plus prudent qu'un déplacement.
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
    # Chaque préchargement est isolé dans son propre try : si l'un échoue
    # (modèle indisponible, index pas encore créé), l'autre peut quand même
    # réussir, et surtout l'API démarre dans tous les cas. On dégrade les
    # performances du premier appel, jamais la disponibilité du service.
    try:
        from src.core.ingest import charger_modele_embedding
        charger_modele_embedding()
        logger.info("Modèle d'embedding préchargé.")
    except Exception as exc:
        logger.warning("Préchargement du modèle ignoré : %s", exc)

    try:
        vectorstore = charger_vectorstore()
        # count() interroge directement la collection ChromaDB pour connaître
        # le nombre de fragments indexés ; utile comme trace de démarrage.
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
    # lifespan est le mécanisme moderne de FastAPI pour exécuter du code au
    # démarrage et à l'arrêt. Tout ce qui précède le « yield » s'exécute une
    # fois, au lancement ; ce qui suit, à l'extinction. L'ORDRE compte : on
    # vérifie la sécurité d'abord (pour échouer tôt si la config est mauvaise),
    # puis on crée la base, on migre, et enfin on préchauffe les modèles.
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
    version="5.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    # On rattache le cycle de vie défini ci-dessus : c'est ce paramètre qui
    # déclenche verifier_securite, creer_base, etc. au lancement du serveur.
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Middlewares
# ---------------------------------------------------------------------------
# Un middleware s'intercale sur CHAQUE requête. CORS doit être déclaré ici, au
# niveau de l'app, car le navigateur l'exige pour autoriser le frontend (servi
# sur une autre origine en développement) à appeler l'API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ORIGINES_CORS,
    # allow_credentials=True autorise l'envoi de cookies/jetons. C'est
    # précisément ce qui rend un « allow_origins=* » dangereux, d'où le
    # contrôle fait dans verifier_securite.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def journaliser_requetes(request, call_next):
    """Journalise chaque requête avec sa durée."""
    # On mesure le temps de traitement de bout en bout. perf_counter est une
    # horloge monotone, insensible aux ajustements d'heure système : c'est le
    # bon choix pour mesurer une durée (contrairement à datetime.now).
    debut = time.perf_counter()
    # call_next passe la main à la suite de la chaîne (autres middlewares puis
    # la route). On entoure cet appel de la mesure pour chronométrer le tout.
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
    # Filet de sécurité global : toute exception non rattrapée ailleurs finit
    # ici. On la journalise AVEC sa pile complète (exc_info=True) pour le
    # diagnostic, mais on ne renvoie au client qu'un message générique : on ne
    # divulgue jamais de détail interne (chemins, requêtes SQL, traces) qui
    # aiderait un attaquant ou exposerait le fonctionnement du serveur.
    logger.error("Erreur non gérée : %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Erreur interne du serveur."},
    )


# ---------------------------------------------------------------------------
# Routeurs
# ---------------------------------------------------------------------------
# On branche tous les sous-routeurs sur l'application. Chacun apporte ses
# propres routes (avec leurs préfixes définis dans leur fichier). Les
# regrouper ici donne une vue d'ensemble des grands domaines de l'API.
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
    # Route volontairement publique (pas de dépendance d'authentification) :
    # elle sert à vérifier que le service répond, y compris avant connexion et
    # pour la supervision. Elle renvoie aussi MODELE_OLLAMA, ce qui permet au
    # frontend d'afficher dynamiquement le modèle actif au lieu d'un nom figé.
    try:
        vectorstore = charger_vectorstore()
        nombre = vectorstore._collection.count()
        return ReponseSante(statut="ok", nb_documents=nombre, modele=MODELE_OLLAMA)
    except FileNotFoundError as exc:
        # Pas encore d'index (aucune ingestion) : on répond 503 (service
        # indisponible) plutôt que 500. C'est un état transitoire normal, pas
        # un bug ; 503 le signale correctement à un outil de supervision.
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
    # Variante NON streamée de /chat/stream : on calcule la réponse complète
    # d'un bloc. Même question tronquée à 60 caractères dans les logs que pour
    # le streaming, pour le diagnostic sans déverser le texte intégral.
    logger.info(
        "POST /chat | utilisateur='%s' | question='%s'",
        utilisateur.nom_utilisateur,
        requete.question[:60],
    )

    try:
        reponse_rag = repondre(requete.question, nom_utilisateur=str(utilisateur.nom_utilisateur))
    except FileNotFoundError as exc:
        # Index absent : service indisponible (503), même logique que /health.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        # Toute autre panne du moteur (typiquement Ollama non lancé) : on
        # renvoie un 503 avec un message actionnable pour l'utilisateur, et on
        # trace le détail côté serveur. On distingue ainsi « ma config est
        # incomplète » d'une vraie erreur 500 interne.
        logger.error("Erreur RAG : %s", exc, exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Le service LLM est indisponible. Vérifiez qu'Ollama est lancé.",
        ) from exc

    # On archive l'échange seulement après une génération réussie : pas de
    # réponse, pas d'enregistrement. session_effective centralise le choix de
    # l'identifiant de session (fourni ou dérivé du nom d'utilisateur).
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
# Un seul service sert à la fois l'API et le frontend compilé : c'est pourquoi
# le frontend est sur la même origine que l'API en production (cf. api.ts).
# Ces imports sont placés ici, au plus près de leur usage, pour garder le haut
# du fichier centré sur l'API.
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

DOSSIER_FRONTEND = Path("frontend/dist")

# On ne monte le frontend que s'il a été compilé (dossier présent). En
# développement pur backend, son absence ne doit pas empêcher l'API de tourner.
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
    #    C'est le motif classique d'une « single-page application » : le
    #    routage côté client (React Router) a besoin que toute URL inconnue du
    #    serveur retombe sur index.html, sinon un rafraîchissement sur une
    #    route React donnerait un 404.
    @app.get("/{chemin_complet:path}", include_in_schema=False)
    async def servir_frontend(chemin_complet: str):
        # Si le client demande un fichier réel présent dans dist/ (favicon,
        # icons, manifest…), on le sert tel quel ; sinon on renvoie index.html.
        # On teste is_file pour distinguer un vrai fichier d'une route React :
        # un fichier existe sur disque, une route React non, et c'est elle qui
        # doit recevoir index.html.
        fichier = DOSSIER_FRONTEND / chemin_complet
        if chemin_complet and fichier.is_file():
            return FileResponse(str(fichier))
        return FileResponse(str(DOSSIER_FRONTEND / "index.html"))