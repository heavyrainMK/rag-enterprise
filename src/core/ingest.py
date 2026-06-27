##############################################################
# Nom ......... : ingest.py
# Rôle ........ : Pipeline d'ingestion de l'application RAG
#                 Enterprise. Charge les PDF et TXT depuis
#                 data/shared/ et data/users/<nom>/, les découpe
#                 en morceaux, calcule les embeddings et les
#                 enregistre dans ChromaDB (une collection par
#                 utilisateur).
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.3.0 du 27/06/2026
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

# LangChain fournit les briques du pipeline RAG : un découpeur de texte, des
# chargeurs de fichiers (PDF, TXT), l'interface vers le modèle d'embedding et
# le connecteur ChromaDB. On assemble ces briques plutôt que de réécrire le
# découpage ou la vectorisation à la main.
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

# Ce module est aussi exécutable en ligne de commande (voir le bloc final) :
# on configure donc la journalisation ici, pour qu'un lancement direct
# (python3 -m src.core.ingest) produise des logs lisibles.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
# Toute la configuration de l'ingestion est rassemblée ici, en constantes
# nommées, plutôt que dispersée en « nombres magiques » dans le code. On peut
# ainsi ajuster un paramètre (taille de morceau, modèle...) en un seul endroit.
DOSSIER_DATA: Path = Path("data")
DOSSIER_PARTAGE: Path = DOSSIER_DATA / "shared"
DOSSIER_USERS: Path = DOSSIER_DATA / "users"
DOSSIER_VECTORS: Path = Path("vectorstore")

# Une collection ChromaDB partagée, plus une collection par utilisateur. Cette
# séparation physique est le fondement de l'isolation : une recherche ne vise
# qu'une collection à la fois, donc ne peut pas atteindre les données d'autrui.
COLLECTION_PARTAGEE: str = "shared"
PREFIXE_COLLECTION_USER: str = "user_"

# Modèle d'embedding MULTILINGUE : il sait représenter du texte français (et
# d'autres langues) dans le même espace vectoriel, ce qui est indispensable
# pour un corpus francophone. C'est lui qui transforme le sens en vecteurs.
MODELE_EMBEDDING: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
# Découpage : morceaux de ~1000 caractères avec 150 de chevauchement. Le
# chevauchement évite de couper une idée en deux à la frontière de deux
# morceaux : une phrase à cheval reste présente en entier dans l'un d'eux,
# ce qui améliore la pertinence de la recherche.
TAILLE_MORCEAU: int = 1000
CHEVAUCHEMENT: int = 150
# Liste blanche des extensions acceptées : on ne traite QUE ce qu'on sait
# charger en toute sécurité. Tout autre type de fichier est ignoré.
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
    # ChromaDB impose des noms de collection restreints (lettres ASCII,
    # chiffres, tiret bas). On nettoie donc le nom d'utilisateur en ne gardant
    # que les caractères autorisés, en minuscules.
    nom_propre = "".join(
        c for c in nom_utilisateur.lower()
        if (c.isascii() and c.isalnum()) or c == "_"
    )
    # On borne la longueur pour rester dans les limites de ChromaDB.
    nom_propre = nom_propre[:40]
    # Le piège : ce nettoyage est « destructeur », donc deux noms distincts
    # (« Alice.B » et « aliceb ») pourraient se réduire au même texte et donc
    # partager une collection, ce qui casserait l'isolation. On suffixe donc un
    # hachage du nom ORIGINAL : il diffère dès que le nom d'origine diffère,
    # garantissant une collection distincte par utilisateur réel.
    code = hashlib.sha256(nom_utilisateur.encode("utf-8")).hexdigest()[:8]
    if nom_propre:
        return f"{PREFIXE_COLLECTION_USER}{nom_propre}_{code}"
    # Cas limite : si le nom ne contient aucun caractère exploitable (que des
    # symboles), on retombe sur le seul hachage, toujours valide et unique.
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
    # Dossier absent = rien à charger : on renvoie une liste vide plutôt que de
    # lever une erreur, car un espace utilisateur peut légitimement être vide.
    if not dossier.exists():
        return []

    # On associe chaque extension à son chargeur LangChain. Le dictionnaire sert
    # à la fois de liste blanche (on ignore ce qui n'y figure pas) et de
    # routage vers le bon chargeur.
    chargeurs = {".pdf": PyPDFLoader, ".txt": TextLoader}
    documents = []

    # rglob parcourt récursivement les sous-dossiers ; sorted rend l'ordre de
    # traitement déterministe (utile pour des logs et des IDs reproductibles).
    for chemin in sorted(dossier.rglob("*")):
        if chemin.suffix.lower() not in chargeurs:
            continue
        logger.info("Chargement : %s", chemin.name)
        try:
            chargeur = chargeurs[chemin.suffix.lower()](str(chemin))
            docs = chargeur.load()
            # On force la métadonnée « source » au nom de fichier : c'est elle
            # qui, à la fin, permettra de citer la source dans la réponse. On
            # remplace le chemin complet par le simple nom, plus lisible et qui
            # ne divulgue pas l'arborescence du serveur.
            for doc in docs:
                doc.metadata["source"] = chemin.name
            documents.extend(docs)
        except Exception as exc:
            # Un fichier illisible (PDF corrompu, par ex.) ne doit pas faire
            # échouer toute l'ingestion : on le signale et on passe au suivant.
            logger.warning("Impossible de charger '%s' : %s", chemin.name, exc)

    logger.info("%d document(s) chargé(s) depuis '%s'.", len(documents), dossier)
    return documents


# ---------------------------------------------------------------------------
# 2. Découpage en morceaux
# ---------------------------------------------------------------------------
def decouper_documents(documents):
    """Découpe les documents en morceaux avec chevauchement."""
    # On découpe parce qu'on ne peut pas (ni ne veut) vectoriser un document
    # entier d'un bloc : des morceaux de taille homogène donnent une recherche
    # plus fine et un contexte ciblé à fournir au LLM.
    decoupeur = RecursiveCharacterTextSplitter(
        chunk_size=TAILLE_MORCEAU,
        chunk_overlap=CHEVAUCHEMENT,
        # La liste de séparateurs est essayée DANS L'ORDRE : on coupe d'abord
        # aux sauts de paragraphe, puis aux lignes, puis aux phrases, puis aux
        # mots, et seulement en dernier recours n'importe où. On respecte ainsi
        # au mieux la structure naturelle du texte, sans casser les phrases.
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
# Variable de module servant de cache : le modèle d'embedding est lourd à
# charger (plusieurs secondes), on ne veut le faire qu'UNE fois pour toute la
# durée de vie du processus, pas à chaque ingestion.
_modele_embedding = None


def charger_modele_embedding():
    """Retourne le modèle d'embedding, chargé une seule fois en mémoire."""
    # Patron « singleton paresseux » : on instancie au premier appel, puis on
    # réutilise. global est nécessaire pour réaffecter la variable de module.
    global _modele_embedding
    if _modele_embedding is None:
        logger.info("Chargement du modèle d'embedding : %s", MODELE_EMBEDDING)
        _modele_embedding = HuggingFaceEmbeddings(
            model_name=MODELE_EMBEDDING,
            # device cpu : pas de dépendance GPU, cohérent avec un déploiement
            # local standard et avec l'image Docker allégée (torch CPU-only).
            model_kwargs={"device": "cpu"},
            # normalize_embeddings=True : on normalise les vecteurs. Sur des
            # vecteurs de norme 1, comparer par distance revient à comparer par
            # similarité cosinus, ce qui rend les scores de recherche
            # homogènes et les seuils de pertinence interprétables.
            encode_kwargs={"normalize_embeddings": True},
        )
    return _modele_embedding


# ---------------------------------------------------------------------------
# 4. Enregistrement dans ChromaDB
# ---------------------------------------------------------------------------
def enregistrer_morceaux(morceaux, nom_collection, modele):
    """Calcule les embeddings et enregistre les morceaux dans ChromaDB."""
    DOSSIER_VECTORS.mkdir(parents=True, exist_ok=True)
    # On calcule un identifiant stable par morceau (voir id_morceau). Le passer
    # explicitement à Chroma est ce qui rend la réingestion idempotente : un
    # même morceau réécrit le même ID au lieu de créer un doublon.
    identifiants = [id_morceau(m, i, nom_collection) for i, m in enumerate(morceaux)]

    vectorstore = Chroma(
        collection_name=nom_collection,
        embedding_function=modele,
        # persist_directory : la collection est écrite sur disque, donc elle
        # survit au redémarrage du serveur (pas besoin de tout réindexer).
        persist_directory=str(DOSSIER_VECTORS),
    )
    # add_documents calcule les embeddings des morceaux et les stocke avec leurs
    # IDs. Grâce aux IDs stables, réingérer un document déjà présent met à jour
    # ses morceaux au lieu de les dupliquer.
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
    # L'ID est DÉTERMINISTE : il dépend de la collection, du fichier source, de
    # la page et de la position du morceau. Le même morceau produit donc
    # toujours le même ID, ce qui empêche les doublons quand on réingère. MD5
    # suffit ici : on cherche un identifiant compact et stable, pas une
    # propriété de sécurité cryptographique.
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
    # Fonction générique réutilisée pour le partagé ET pour chaque utilisateur :
    # seuls le dossier, la collection cible et l'étiquette de log changent. On
    # évite ainsi de dupliquer les quatre étapes du pipeline.
    dossier.mkdir(parents=True, exist_ok=True)
    logger.info("=== Ingestion %s → collection '%s' ===", etiquette, collection)
    documents = charger_documents(dossier)
    # Sortie anticipée si rien à ingérer : inutile de charger le modèle ou de
    # toucher à ChromaDB pour un dossier vide.
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
    # On combine ici les deux conventions définies plus haut : le dossier privé
    # de l'utilisateur comme source, et sa collection dédiée (nom nettoyé +
    # hachage) comme cible. C'est ce couplage qui matérialise l'isolation.
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

    # Le partagé est toujours ingéré : c'est le socle commun à tous.
    nb_partage = ingerer_partages()

    if nom_utilisateur:
        # Mode ciblé : on ne réindexe que cet utilisateur (typiquement après
        # qu'il a déposé un document), ce qui évite de tout reparcourir.
        nb_user = ingerer_utilisateur(nom_utilisateur)
        logger.info(
            "=== Terminé : %d morceaux partagés + %d morceaux pour '%s' ===",
            nb_partage, nb_user, nom_utilisateur,
        )
    else:
        # Mode complet : on réindexe le partagé et CHAQUE utilisateur présent.
        # Utile pour une reconstruction totale de l'index (ex. après une
        # désynchronisation de ChromaDB).
        total_user = 0
        if DOSSIER_USERS.exists():
            for dossier in DOSSIER_USERS.iterdir():
                if dossier.is_dir():
                    total_user += ingerer_utilisateur(dossier.name)
        logger.info(
            "=== Terminé : %d morceaux partagés + %d morceaux utilisateurs ===",
            nb_partage, total_user,
        )


# Permet de lancer une ingestion complète en ligne de commande
# (python3 -m src.core.ingest), indépendamment de l'API. Pratique pour
# (ré)indexer le corpus sans démarrer le serveur web.
if __name__ == "__main__":
    lancer_ingestion()