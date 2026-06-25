##############################################################
# Nom ......... : ingest.py
# Rôle ........ : Pipeline d'ingestion de l'application RAG
#                 Enterprise. Charge les PDF et TXT depuis
#                 data/shared/ et data/users/<nom>/, les découpe
#                 en morceaux, calcule les embeddings et les
#                 enregistre dans ChromaDB (une collection par
#                 utilisateur).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.2.0 du 23/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : python3 -m src.core.ingest
#                 Ou importé par src/api/routes/documents.py.
# Dépendances . : langchain, langchain-community, chromadb,
#                 sentence-transformers, pypdf
##############################################################

import hashlib
import logging
from pathlib import Path

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
DOSSIER_DATA: Path = Path("data")
DOSSIER_PARTAGE: Path = DOSSIER_DATA / "shared"
DOSSIER_USERS: Path = DOSSIER_DATA / "users"
DOSSIER_VECTORS: Path = Path("vectorstore")

COLLECTION_PARTAGEE: str = "shared"
PREFIXE_COLLECTION_USER: str = "user_"

MODELE_EMBEDDING: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TAILLE_MORCEAU: int = 1000
CHEVAUCHEMENT: int = 150
EXTENSIONS_AUTORISEES: set = {".pdf", ".txt"}


# ---------------------------------------------------------------------------
# Noms de collections et dossiers
# ---------------------------------------------------------------------------
def nom_collection_utilisateur(nom_utilisateur):
    """
    Retourne le nom de la collection ChromaDB d'un utilisateur.

    On ajoute un petit code (hachage) au nom nettoyé pour éviter que
    deux utilisateurs différents (par ex. "Alice.B" et "aliceb")
    aboutissent à la même collection. Le résultat respecte les règles
    de nommage de ChromaDB (lettres, chiffres, tiret bas).
    """
    nom_propre = "".join(
        c for c in nom_utilisateur.lower()
        if (c.isascii() and c.isalnum()) or c == "_"
    )
    nom_propre = nom_propre[:40]
    code = hashlib.sha256(nom_utilisateur.encode("utf-8")).hexdigest()[:8]
    if nom_propre:
        return f"{PREFIXE_COLLECTION_USER}{nom_propre}_{code}"
    return f"{PREFIXE_COLLECTION_USER}{code}"


def dossier_utilisateur(nom_utilisateur):
    """Retourne le dossier des documents privés d'un utilisateur."""
    return DOSSIER_USERS / nom_utilisateur


# ---------------------------------------------------------------------------
# 1. Chargement des fichiers
# ---------------------------------------------------------------------------
def charger_documents(dossier):
    """
    Charge tous les PDF et TXT d'un dossier.

    Retour
    ------
    list
        Documents LangChain avec métadonnée 'source' (nom du fichier).
    """
    if not dossier.exists():
        return []

    chargeurs = {".pdf": PyPDFLoader, ".txt": TextLoader}
    documents = []

    for chemin in sorted(dossier.rglob("*")):
        if chemin.suffix.lower() not in chargeurs:
            continue
        logger.info("Chargement : %s", chemin.name)
        try:
            chargeur = chargeurs[chemin.suffix.lower()](str(chemin))
            docs = chargeur.load()
            for doc in docs:
                doc.metadata["source"] = chemin.name
            documents.extend(docs)
        except Exception as exc:
            logger.warning("Impossible de charger '%s' : %s", chemin.name, exc)

    logger.info("%d document(s) chargé(s) depuis '%s'.", len(documents), dossier)
    return documents


# ---------------------------------------------------------------------------
# 2. Découpage en morceaux
# ---------------------------------------------------------------------------
def decouper_documents(documents):
    """Découpe les documents en morceaux avec chevauchement."""
    decoupeur = RecursiveCharacterTextSplitter(
        chunk_size=TAILLE_MORCEAU,
        chunk_overlap=CHEVAUCHEMENT,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    morceaux = decoupeur.split_documents(documents)
    logger.info(
        "%d document(s) → %d morceau(x) (taille=%d, chevauchement=%d).",
        len(documents), len(morceaux), TAILLE_MORCEAU, CHEVAUCHEMENT,
    )
    return morceaux


# ---------------------------------------------------------------------------
# 3. Modèle d'embedding (chargé une seule fois)
# ---------------------------------------------------------------------------
_modele_embedding = None


def charger_modele_embedding():
    """Retourne le modèle d'embedding, chargé une seule fois en mémoire."""
    global _modele_embedding
    if _modele_embedding is None:
        logger.info("Chargement du modèle d'embedding : %s", MODELE_EMBEDDING)
        _modele_embedding = HuggingFaceEmbeddings(
            model_name=MODELE_EMBEDDING,
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _modele_embedding


# ---------------------------------------------------------------------------
# 4. Enregistrement dans ChromaDB
# ---------------------------------------------------------------------------
def enregistrer_morceaux(morceaux, nom_collection, modele):
    """Calcule les embeddings et enregistre les morceaux dans ChromaDB."""
    DOSSIER_VECTORS.mkdir(parents=True, exist_ok=True)
    identifiants = [id_morceau(m, i, nom_collection) for i, m in enumerate(morceaux)]

    vectorstore = Chroma(
        collection_name=nom_collection,
        embedding_function=modele,
        persist_directory=str(DOSSIER_VECTORS),
    )
    vectorstore.add_documents(documents=morceaux, ids=identifiants)
    logger.info(
        "Collection '%s' mise à jour : %d morceaux.",
        nom_collection,
        vectorstore._collection.count(),
    )
    return vectorstore


# ---------------------------------------------------------------------------
# Fonctions utilitaires
# ---------------------------------------------------------------------------
def id_morceau(doc, position, collection=""):
    """
    Génère un identifiant stable pour un morceau.

    Format : <collection>:<source>:<page>:<position>, haché en MD5
    pour garantir l'unicité et éviter les doublons à la réingestion.
    """
    source = doc.metadata.get("source", "inconnu")
    page = doc.metadata.get("page", 0)
    brut = f"{collection}:{source}:{page}:{position}"
    return hashlib.md5(brut.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 5. Pipelines d'ingestion
# ---------------------------------------------------------------------------
def ingerer(dossier, collection, etiquette):
    """
    Pipeline d'ingestion d'un dossier vers une collection ChromaDB :
    chargement → découpage → embeddings → enregistrement.

    Paramètres
    ----------
    dossier : Path
        Dossier source à ingérer.
    collection : str
        Nom de la collection ChromaDB cible.
    etiquette : str
        Libellé lisible pour les logs (ex. "des documents partagés").

    Retour
    ------
    int
        Nombre de morceaux indexés (0 si aucun document).
    """
    dossier.mkdir(parents=True, exist_ok=True)
    logger.info("=== Ingestion %s → collection '%s' ===", etiquette, collection)
    documents = charger_documents(dossier)
    if not documents:
        logger.info("Aucun document pour %s.", etiquette)
        return 0
    morceaux = decouper_documents(documents)
    modele = charger_modele_embedding()
    enregistrer_morceaux(morceaux, collection, modele)
    return len(morceaux)


def ingerer_partages():
    """
    Ingère les documents de shared/ dans la collection 'shared'.

    Retour
    ------
    int
        Nombre de morceaux indexés.
    """
    return ingerer(DOSSIER_PARTAGE, COLLECTION_PARTAGEE, "des documents partagés")


def ingerer_utilisateur(nom_utilisateur):
    """
    Ingère les documents privés d'un utilisateur.

    Retour
    ------
    int
        Nombre de morceaux indexés.
    """
    return ingerer(
        dossier_utilisateur(nom_utilisateur),
        nom_collection_utilisateur(nom_utilisateur),
        f"utilisateur '{nom_utilisateur}'",
    )


def lancer_ingestion(nom_utilisateur=None):
    """
    Lance l'ingestion complète.

    Si nom_utilisateur est fourni : ingère shared + ce utilisateur.
    Sinon : ingère shared + tous les utilisateurs présents dans users/.
    """
    logger.info("=== Démarrage de l'ingestion ===")

    nb_partage = ingerer_partages()

    if nom_utilisateur:
        nb_user = ingerer_utilisateur(nom_utilisateur)
        logger.info(
            "=== Terminé : %d morceaux partagés + %d morceaux pour '%s' ===",
            nb_partage, nb_user, nom_utilisateur,
        )
    else:
        total_user = 0
        if DOSSIER_USERS.exists():
            for dossier in DOSSIER_USERS.iterdir():
                if dossier.is_dir():
                    total_user += ingerer_utilisateur(dossier.name)
        logger.info(
            "=== Terminé : %d morceaux partagés + %d morceaux utilisateurs ===",
            nb_partage, total_user,
        )


if __name__ == "__main__":
    lancer_ingestion()