##############################################################
# Nom ......... : documents.py
# Rôle ........ : Gestion des documents de l'API RAG Enterprise.
#                 Téléversement (partagé/privé), liste et
#                 suppression des fichiers PDF/TXT. Relance
#                 l'ingestion après chaque téléversement et
#                 nettoie l'index ChromaDB à la suppression.
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.2.0 du 26/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py via include_router.
# Dépendances . : fastapi, pydantic, src.core.ingest, src.api.auth
##############################################################

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File, BackgroundTasks, Depends
from pydantic import BaseModel

from src.api.auth import utilisateur_courant
from src.core.ingest import (
    lancer_ingestion,
    DOSSIER_PARTAGE,
    COLLECTION_PARTAGEE,
    DOSSIER_VECTORS,
    dossier_utilisateur,
    nom_collection_utilisateur,
    charger_modele_embedding,
    EXTENSIONS_AUTORISEES,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["Documents"])

# Limite de taille d'un fichier téléversé (garde-fou contre la saturation du
# disque et les abus). Un dépassement renvoie une erreur 413.
TAILLE_MAX_MO: int = 50
TAILLE_LECTURE: int = 1024 * 1024  # 1 Mo : lecture par blocs
# Les deux espaces de stockage possibles : partagé (visible de tous) ou privé
# (visible du seul propriétaire). On utilise un set pour une vérification
# d'appartenance rapide et lisible.
SCOPES_VALIDES: set = {"shared", "private"}


# ---------------------------------------------------------------------------
# Modèles Pydantic
# ---------------------------------------------------------------------------
class ReponseTeleversement(BaseModel):
    nom_fichier: str
    scope: str
    message: str
    ingestion_lancee: bool


class InfoDocument(BaseModel):
    nom_fichier: str
    taille_ko: float
    extension: str
    scope: str


class ReponseListeDocuments(BaseModel):
    documents: list[InfoDocument]
    total: int
    nb_partages: int
    nb_prives: int


class ReponseSuppression(BaseModel):
    nom_fichier: str
    message: str


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------
def valider_scope(scope):
    """Vérifie que le scope est 'shared' ou 'private' (sinon 400)."""
    if scope not in SCOPES_VALIDES:
        raise HTTPException(status_code=400, detail="scope doit être 'shared' ou 'private'.")


def exiger_admin_si_partage(scope, utilisateur):
    """Exige le rôle admin pour toute opération sur l'espace partagé."""
    # Contrôle de droits : seul un admin peut écrire ou supprimer dans l'espace
    # partagé (commun à tous). Un utilisateur standard reste cantonné à son
    # espace privé. Refus explicite par un code 403 (« interdit »).
    if scope == "shared" and str(utilisateur.role) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Seul un admin peut agir sur l'espace partagé.",
        )


def nom_sur(nom_fichier):
    """
    Garde uniquement le nom du fichier (sans chemin) et refuse les
    tentatives de remontée de dossier (path traversal).
    """
    # SÉCURITÉ (1re barrière) : Path(...).name ne conserve que le dernier
    # segment du chemin. Ainsi "../../etc/passwd" devient "passwd" : on neutralise
    # toute tentative d'écrire ailleurs que dans le dossier prévu (attaque dite
    # « path traversal »). On refuse aussi les noms vides ou réduits à "." / "..".
    nom = Path(str(nom_fichier or "")).name
    if not nom or nom in {".", ".."}:
        raise HTTPException(status_code=400, detail="Nom de fichier invalide.")
    return nom


def chemin_dans_dossier(dossier, nom_fichier):
    """
    Construit le chemin du fichier et vérifie qu'il reste bien
    à l'intérieur du dossier autorisé.
    """
    # SÉCURITÉ (2e barrière, défense en profondeur) : même après avoir nettoyé
    # le nom, on vérifie que le chemin final, une fois RÉSOLU (.resolve() suit
    # les liens et simplifie les ".."), pointe bien à l'intérieur du dossier
    # autorisé. On exige que « base » soit le dossier cible lui-même ou l'un de
    # ses parents. Toute échappée hors du dossier est rejetée (400).
    nom = nom_sur(nom_fichier)
    base = dossier.resolve()
    cible = (base / nom).resolve()
    if base != cible and base not in cible.parents:
        raise HTTPException(status_code=400, detail="Chemin de fichier non autorisé.")
    return cible


def verifier_extension(fichier):
    """Vérifie que l'extension du fichier est autorisée (sinon 400)."""
    nom = str(fichier.filename) if fichier.filename else ""
    extension = Path(nom).suffix.lower()
    if extension not in EXTENSIONS_AUTORISEES:
        raise HTTPException(
            status_code=400,
            detail=f"Extension '{extension}' non supportée. Formats : {', '.join(EXTENSIONS_AUTORISEES)}",
        )


def dossier_du_scope(scope, utilisateur):
    """Retourne le dossier disque correspondant au scope demandé."""
    if scope == "shared":
        return DOSSIER_PARTAGE
    return dossier_utilisateur(str(utilisateur.nom_utilisateur))


def collection_du_scope(scope, utilisateur):
    """Retourne le nom de la collection ChromaDB correspondant au scope."""
    if scope == "shared":
        return COLLECTION_PARTAGEE
    return nom_collection_utilisateur(str(utilisateur.nom_utilisateur))


def lister_dossier(dossier, scope):
    """Retourne les InfoDocument d'un dossier pour un scope donné."""
    dossier.mkdir(parents=True, exist_ok=True)
    infos = []
    for chemin in sorted(dossier.iterdir()):
        if chemin.suffix.lower() in EXTENSIONS_AUTORISEES:
            infos.append(InfoDocument(
                nom_fichier=chemin.name,
                taille_ko=round(chemin.stat().st_size / 1024, 2),
                extension=chemin.suffix.lower(),
                scope=scope,
            ))
    return infos


async def ecrire_sur_disque(fichier, chemin):
    """
    Écrit le fichier téléversé par blocs, en refusant tout contenu
    dépassant TAILLE_MAX_MO. Supprime le fichier partiel en cas de
    dépassement.

    Retour
    ------
    float
        Taille finale du fichier en Mo.
    """
    # On lit et écrit le fichier par BLOCS de 1 Mo plutôt qu'en une seule fois.
    # Avantage : on ne charge jamais tout le fichier en mémoire (un gros fichier
    # ne fait pas exploser la RAM), et on peut interrompre dès que la limite de
    # taille est franchie, sans avoir tout reçu.
    max_octets = TAILLE_MAX_MO * 1024 * 1024
    ecrit = 0
    try:
        with open(chemin, "wb") as sortie:
            while True:
                bloc = await fichier.read(TAILLE_LECTURE)
                if not bloc:  # plus rien à lire : fichier entièrement reçu
                    break
                ecrit += len(bloc)
                # Dès qu'on dépasse la taille maximale, on s'arrête net (413).
                if ecrit > max_octets:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Fichier trop volumineux. Maximum : {TAILLE_MAX_MO} Mo.",
                    )
                sortie.write(bloc)
    except HTTPException:
        # Nettoyage : en cas de rejet (fichier trop gros), on supprime le
        # fichier partiel déjà écrit pour ne pas laisser de résidu sur le disque,
        # puis on relaie l'erreur à l'appelant.
        chemin.unlink(missing_ok=True)
        raise
    return ecrit / (1024 * 1024)


def ingestion_arriere_plan(nom_utilisateur):
    """Lance l'ingestion en arrière-plan."""
    try:
        logger.info("Réingestion déclenchée pour utilisateur='%s'", nom_utilisateur)
        lancer_ingestion(nom_utilisateur=nom_utilisateur)
    except Exception as exc:
        logger.error("Erreur de réingestion : %s", exc, exc_info=True)


def supprimer_de_lindex(nom_collection, nom_fichier):
    """
    Supprime de ChromaDB tous les morceaux issus d'un fichier source.
    Sans erreur si la collection n'existe pas encore.
    """
    # Cette fonction est ESSENTIELLE à la cohérence du système : quand on
    # supprime un document du disque, il faut aussi retirer ses vecteurs de
    # l'index, sinon ils deviendraient des « vecteurs orphelins » qui
    # pollueraient encore les recherches. La suppression cible précisément les
    # morceaux dont la métadonnée « source » correspond au fichier supprimé.
    if not DOSSIER_VECTORS.exists():
        return
    try:
        from langchain_chroma import Chroma

        modele = charger_modele_embedding()
        vs = Chroma(
            collection_name=nom_collection,
            embedding_function=modele,
            persist_directory=str(DOSSIER_VECTORS),
        )
        # where={"source": nom_fichier} : on ne supprime que les morceaux issus
        # de CE fichier, en laissant intacts ceux des autres documents.
        vs._collection.delete(where={"source": nom_fichier})
        logger.info("Morceaux supprimés de '%s' pour source='%s'.", nom_collection, nom_fichier)
    except Exception as exc:
        # Tolérant aux erreurs : un échec de nettoyage de l'index ne doit pas
        # faire échouer la suppression du fichier elle-même (déjà effectuée).
        logger.warning("Nettoyage ChromaDB impossible (%s, %s) : %s", nom_collection, nom_fichier, exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.post("/upload", response_model=ReponseTeleversement)
async def televerser_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scope: str = "private",
    utilisateur=Depends(utilisateur_courant),
):
    """
    Téléverse un document dans l'espace partagé ou privé.
    - scope=shared  : visible par tous - réservé aux admins.
    - scope=private : visible uniquement par l'utilisateur connecté.
    """
    # Séquence de vérifications AVANT toute écriture (on échoue tôt si besoin) :
    #   1. le scope est valide ("shared" ou "private") ;
    #   2. l'extension du fichier est autorisée (PDF/TXT) ;
    #   3. l'utilisateur a le droit d'écrire dans cet espace (admin si partagé).
    valider_scope(scope)
    verifier_extension(file)
    exiger_admin_si_partage(scope, utilisateur)

    # Choix du dossier de destination selon le scope, créé si absent.
    dossier = dossier_du_scope(scope, utilisateur)
    dossier.mkdir(parents=True, exist_ok=True)

    # Construction du chemin SÉCURISÉ (anti path traversal) puis écriture.
    chemin = chemin_dans_dossier(dossier, file.filename)
    nom_fichier = chemin.name

    taille_mo = await ecrire_sur_disque(file, chemin)
    logger.info("Document sauvegardé : %s → %s (%.2f Mo)", nom_fichier, scope, taille_mo)

    # Ré-indexation lancée EN ARRIÈRE-PLAN (background task) : la réponse HTTP
    # part immédiatement, sans faire attendre l'utilisateur pendant le calcul
    # des vecteurs (qui peut durer plusieurs secondes).
    #   - espace partagé  → on réindexe la collection partagée (nom = None) ;
    #   - espace privé    → on réindexe la collection de cet utilisateur.
    if scope == "shared":
        background_tasks.add_task(ingestion_arriere_plan, None)
    else:
        background_tasks.add_task(ingestion_arriere_plan, str(utilisateur.nom_utilisateur))

    return ReponseTeleversement(
        nom_fichier=nom_fichier,
        scope=scope,
        message=f"'{nom_fichier}' téléversé dans l'espace {scope}. Indexation en cours...",
        ingestion_lancee=True,
    )


@router.get("/", response_model=ReponseListeDocuments)
async def lister_documents(utilisateur=Depends(utilisateur_courant)):
    """Liste les documents accessibles : partagés + privés de l'utilisateur."""
    documents = lister_dossier(DOSSIER_PARTAGE, "shared")
    documents += lister_dossier(dossier_utilisateur(str(utilisateur.nom_utilisateur)), "private")

    nb_partages = sum(1 for d in documents if d.scope == "shared")
    nb_prives = sum(1 for d in documents if d.scope == "private")

    return ReponseListeDocuments(
        documents=documents,
        total=len(documents),
        nb_partages=nb_partages,
        nb_prives=nb_prives,
    )


@router.delete("/{scope}/{nom_fichier}", response_model=ReponseSuppression)
async def supprimer_document(
    scope: str,
    nom_fichier: str,
    background_tasks: BackgroundTasks,
    utilisateur=Depends(utilisateur_courant),
):
    """
    Supprime un document du disque ET ses morceaux dans ChromaDB.
    - scope=shared  : réservé aux admins.
    - scope=private : l'utilisateur supprime ses propres documents.
    """
    valider_scope(scope)
    exiger_admin_si_partage(scope, utilisateur)

    dossier = dossier_du_scope(scope, utilisateur)
    collection = collection_du_scope(scope, utilisateur)

    # Chemin sécurisé (anti path traversal) du fichier à supprimer.
    chemin = chemin_dans_dossier(dossier, nom_fichier)
    nom = chemin.name

    # Fichier inexistant : on renvoie 404 (« non trouvé »).
    if not chemin.exists():
        raise HTTPException(status_code=404, detail=f"'{nom}' introuvable.")

    # Suppression en deux temps, dans cet ordre :
    #   1. le fichier sur le disque ;
    #   2. ses vecteurs dans l'index ChromaDB (cohérence disque/index).
    chemin.unlink()
    logger.info("Document supprimé : %s/%s", scope, nom)

    supprimer_de_lindex(collection, nom)

    return ReponseSuppression(
        nom_fichier=nom,
        message=f"'{nom}' supprimé de l'espace {scope}.",
    )