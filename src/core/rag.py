##############################################################
# Nom ......... : rag.py
# Rôle ........ : Cœur du système RAG de l'application. Cherche
#                 dans les collections ChromaDB partagée et privée
#                 de l'utilisateur, fusionne les résultats par
#                 pertinence et génère la réponse via Ollama
#                 (local) ou OpenAI. Deux modes : réponse complète
#                 (repondre) et token par token (repondre_stream).
# Auteur ...... : Maxim Khomenko
# Version ..... : V4.3.0 du 24/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py et stream.py.
# Dépendances . : langchain, langchain-community, langchain-openai,
#                 ollama, chromadb, python-dotenv
##############################################################

import logging
import os
from dataclasses import dataclass, field

from langchain_chroma import Chroma
from langchain_community.llms import Ollama
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser

from dotenv import load_dotenv
from src.core.ingest import (
    charger_modele_embedding,
    nom_collection_utilisateur,
    COLLECTION_PARTAGEE,
    DOSSIER_VECTORS,
)

load_dotenv()
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
NB_RESULTATS: int = 3
# Seuil de distance maximale pour qu'un morceau soit jugé pertinent.
# 1.4 (au lieu de 1.2) laisse passer les formulations possessives ou
# indirectes (« mes objectifs » vs « Objectifs 2026 ») qui scorent un peu
# plus loin. Le filtre par marge en aval (MARGE_AFFICHAGE) écarte ensuite
# le bruit, donc assouplir ici ne dégrade pas la qualité des sources.
SEUIL_PERTINENCE: float = 1.4
MODELE_OLLAMA: str = os.getenv("OLLAMA_MODEL", "mistral")
MODELE_OPENAI: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Marge de tolérance sur le score : on n'affiche une source que si son score
# est proche du meilleur score trouvé. Une source nettement moins pertinente
# (du « bruit » sémantique) est donnée au LLM comme contexte mais pas montrée.
MARGE_AFFICHAGE: float = 0.25

REPONSE_SANS_CONTEXTE: str = (
    "Je ne trouve pas la réponse dans la base documentaire. "
    "Veuillez reformuler votre question ou vérifier que les documents "
    "concernés ont bien été ingérés."
)

MODELE_PROMPT: str = """Tu es un assistant documentaire interne d'entreprise.
Tu réponds UNIQUEMENT en te basant sur le CONTEXTE fourni ci-dessous.

Règles strictes :
1. Si la réponse n'est pas présente dans le CONTEXTE, réponds exactement :
   "Je ne trouve pas la réponse dans la base documentaire."
2. Ne jamais inventer ou utiliser tes connaissances générales.
3. Cite le document source entre parenthèses, ex: (Source: rapport_rh.pdf).
4. Réponds en français sauf si la question est dans une autre langue.
5. Sois concis et précis.

CONTEXTE :
{context}

QUESTION : {question}

RÉPONSE :"""


# ---------------------------------------------------------------------------
# Structures de données
# ---------------------------------------------------------------------------
@dataclass
class ReponseRAG:
    """Réponse complète du système RAG."""
    reponse: str
    sources: list = field(default_factory=list)
    morceaux: list = field(default_factory=list)
    sources_privees: list = field(default_factory=list)
    sources_partagees: list = field(default_factory=list)


@dataclass
class ContexteRAG:
    """Contexte préparé avant la génération de la réponse."""
    contexte: str
    sources: list
    sources_privees: list
    sources_partagees: list
    morceaux: list
    a_du_contexte: bool


# ---------------------------------------------------------------------------
# 1. Chargement d'une collection
# ---------------------------------------------------------------------------
def charger_collection(nom_collection):
    """Charge une collection ChromaDB. Retourne None si elle est vide ou absente."""
    if not DOSSIER_VECTORS.exists():
        return None
    try:
        modele = charger_modele_embedding()
        vs = Chroma(
            collection_name=nom_collection,
            embedding_function=modele,
            persist_directory=str(DOSSIER_VECTORS),
        )
        if vs._collection.count() == 0:
            return None
        return vs
    except Exception as exc:
        logger.debug("Collection '%s' inaccessible : %s", nom_collection, exc)
        return None


def charger_vectorstore():
    """Charge la collection partagée. Utilisée par la route /health."""
    modele = charger_modele_embedding()
    return Chroma(
        collection_name=COLLECTION_PARTAGEE,
        embedding_function=modele,
        persist_directory=str(DOSSIER_VECTORS),
    )


# ---------------------------------------------------------------------------
# 2. Modèle de langage
# ---------------------------------------------------------------------------
def charger_llm():
    """
    Crée le modèle de langage selon la configuration.

    Par défaut Ollama (local). Si USE_OPENAI=true et qu'une clé est
    présente, utilise OpenAI à la place.
    """
    utiliser_openai = os.getenv("USE_OPENAI", "false").lower() == "true"
    if utiliser_openai:
        cle = os.getenv("OPENAI_API_KEY")
        if not cle:
            raise EnvironmentError("USE_OPENAI=true mais OPENAI_API_KEY manquant.")
        logger.info("LLM : OpenAI (%s)", MODELE_OPENAI)
        return ChatOpenAI(model=MODELE_OPENAI, temperature=0)
    url_ollama = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    logger.info("LLM : Ollama (%s) @ %s", MODELE_OLLAMA, url_ollama)
    return Ollama(model=MODELE_OLLAMA, base_url=url_ollama, temperature=0)


def construire_chaine():
    """
    Construit la chaîne LangChain de génération : prompt → modèle → parser.

    Factorisée ici car repondre() et repondre_stream() montent exactement
    la même chaîne ; seule l'invocation diffère (invoke vs stream).
    """
    prompt = PromptTemplate(
        template=MODELE_PROMPT,
        input_variables=["context", "question"],
    )
    return prompt | charger_llm() | StrOutputParser()


# ---------------------------------------------------------------------------
# 3. Recherche dans les collections
# ---------------------------------------------------------------------------
def _chercher_dans(nom_collection, origine, question, resultats):
    """
    Cherche dans une collection et ajoute ses morceaux pertinents à
    'resultats' (liste de tuples (document, score, origine)).

    Sans effet si la collection est absente ou vide. Seuls les morceaux
    sous le SEUIL_PERTINENCE sont retenus.
    """
    vs = charger_collection(nom_collection)
    if not vs:
        return
    for doc, score in vs.similarity_search_with_score(question, k=NB_RESULTATS):
        if score <= SEUIL_PERTINENCE:
            resultats.append((doc, score, origine))


def rechercher_multi(question, nom_utilisateur):
    """
    Cherche dans la collection partagée ET la collection privée.

    Les résultats sont filtrés par seuil de pertinence, fusionnés et
    triés par score croissant (plus petit = plus pertinent).

    Retour
    ------
    tuple
        (documents, sources_privees, sources_partagees)
    """
    resultats = []  # (document, score, origine)

    _chercher_dans(COLLECTION_PARTAGEE, "partage", question, resultats)
    _chercher_dans(
        nom_collection_utilisateur(nom_utilisateur), "prive", question, resultats
    )

    if not resultats:
        logger.info("Aucun morceau pertinent trouvé (seuil=%.2f).", SEUIL_PERTINENCE)
        return [], [], []

    resultats.sort(key=lambda x: x[1])

    # On attache le score à chaque document pour pouvoir filtrer l'affichage
    # des sources plus tard (sans réinterroger ChromaDB).
    for doc, score, _origine in resultats:
        doc.metadata["_score"] = float(score)

    documents = [r[0] for r in resultats]
    sources_privees = sorted({
        r[0].metadata.get("source", "inconnu")
        for r in resultats if r[2] == "prive"
    })
    sources_partagees = sorted({
        r[0].metadata.get("source", "inconnu")
        for r in resultats if r[2] == "partage"
    })

    logger.info(
        "Fusion : %d morceaux (partagés=%s, privés=%s)",
        len(documents), sources_partagees, sources_privees,
    )
    return documents, sources_privees, sources_partagees


# ---------------------------------------------------------------------------
# 4. Construction du contexte
# ---------------------------------------------------------------------------
def construire_contexte(documents):
    """Assemble les morceaux en un bloc de texte pour le prompt."""
    if not documents:
        return ""
    parties = []
    for i, doc in enumerate(documents, start=1):
        source = doc.metadata.get("source", "inconnu")
        parties.append(f"[Extrait {i} - {source}]\n{doc.page_content.strip()}")
    return "\n\n---\n\n".join(parties)


# ---------------------------------------------------------------------------
# 5. Préparation du contexte
# ---------------------------------------------------------------------------
def filtrer_par_pertinence(documents):
    """
    Ne garde que les documents dont le score est proche du meilleur.

    Les documents trop éloignés (bruit sémantique) sont écartés AVANT la
    construction du contexte : ainsi le LLM ne reçoit que les sources
    réellement pertinentes, et la réponse ne peut pas être polluée par un
    document privé qui contient un terme commun mais une info hors-sujet
    (ex : la ligne « tickets restaurant -88 € » d'un bulletin de paie).

    Le résultat : les sources affichées à l'utilisateur = exactement les
    sources qui ont nourri la réponse.
    """
    if not documents:
        return []
    meilleur = min(
        (d.metadata.get("_score", 0.0) for d in documents),
        default=0.0,
    )
    seuil = meilleur + MARGE_AFFICHAGE
    return [d for d in documents if d.metadata.get("_score", 0.0) <= seuil]


def preparer_contexte(question, nom_utilisateur):
    """Lance la recherche et prépare le contexte pour le prompt."""
    documents, sources_privees, sources_partagees = rechercher_multi(question, nom_utilisateur)

    if not documents:
        return ContexteRAG(
            contexte="", sources=[], sources_privees=[],
            sources_partagees=[], morceaux=[], a_du_contexte=False,
        )

    # Filtre de pertinence appliqué AVANT tout : le contexte LLM et les
    # sources sont alignés sur ce même sous-ensemble pertinent.
    documents = filtrer_par_pertinence(documents)

    # Recalcule les listes de sources à partir des seuls documents retenus.
    privees_retenues = sorted({
        d.metadata.get("source", "inconnu") for d in documents
        if d.metadata.get("source", "inconnu") in set(sources_privees)
    })
    partagees_retenues = sorted({
        d.metadata.get("source", "inconnu") for d in documents
        if d.metadata.get("source", "inconnu") not in set(sources_privees)
    })

    contexte = construire_contexte(documents)
    toutes_sources = sorted(set(privees_retenues + partagees_retenues))
    morceaux = [doc.page_content for doc in documents]

    return ContexteRAG(
        contexte=contexte,
        sources=toutes_sources,
        sources_privees=privees_retenues,
        sources_partagees=partagees_retenues,
        morceaux=morceaux,
        a_du_contexte=True,
    )


# ---------------------------------------------------------------------------
# 6a. repondre() - réponse complète
# ---------------------------------------------------------------------------
def repondre(question, nom_utilisateur="anonyme"):
    """
    Système RAG complet - retourne la réponse entière d'un coup.
    Utilisé par la route POST /chat.
    """
    logger.info("=== repondre() utilisateur='%s' : %s ===", nom_utilisateur, question)

    ctx = preparer_contexte(question, nom_utilisateur)
    if not ctx.a_du_contexte:
        return ReponseRAG(reponse=REPONSE_SANS_CONTEXTE)

    chaine = construire_chaine()
    reponse = chaine.invoke({"context": ctx.contexte, "question": question})

    logger.info("repondre() terminé. Sources : %s", ctx.sources)
    return ReponseRAG(
        reponse=reponse,
        sources=ctx.sources,
        morceaux=ctx.morceaux,
        sources_privees=ctx.sources_privees,
        sources_partagees=ctx.sources_partagees,
    )


# ---------------------------------------------------------------------------
# 6b. repondre_stream() - réponse token par token
# ---------------------------------------------------------------------------
def repondre_stream(question, nom_utilisateur="anonyme"):
    """
    Système RAG en streaming - génère les tokens un par un.
    Utilisé par la route POST /chat/stream.

    Produit des dictionnaires :
        {"type": "token"|"done"|"no_context"|"error", ...}
    """
    logger.info("=== repondre_stream() utilisateur='%s' : %s ===", nom_utilisateur, question)

    try:
        ctx = preparer_contexte(question, nom_utilisateur)
    except Exception as exc:
        logger.error("Erreur de recherche : %s", exc)
        yield {"type": "error", "content": str(exc)}
        return

    if not ctx.a_du_contexte:
        yield {"type": "no_context", "content": REPONSE_SANS_CONTEXTE}
        return

    chaine = construire_chaine()

    try:
        for token in chaine.stream({"context": ctx.contexte, "question": question}):
            yield {"type": "token", "content": token}
    except Exception as exc:
        logger.error("Erreur du LLM en streaming : %s", exc)
        yield {"type": "error", "content": f"Erreur LLM : {exc}"}
        return

    logger.info("repondre_stream() terminé. Sources : %s", ctx.sources)
    yield {
        "type": "done",
        "sources": ctx.sources,
        "private_sources": ctx.sources_privees,
        "shared_sources": ctx.sources_partagees,
    }