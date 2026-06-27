##############################################################
# Nom ......... : rag.py
# Rôle ........ : Cœur du système RAG de l'application. Cherche
#                 dans les collections ChromaDB partagée et privée
#                 de l'utilisateur, fusionne les résultats par
#                 pertinence et génère la réponse via Ollama
#                 (local) ou OpenAI. Deux modes : réponse complète
#                 (repondre) et token par token (repondre_stream).
# Auteur ...... : Maxim Khomenko
# Version ..... : V4.4.0 du 27/06/2026
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

# load_dotenv lit le fichier .env et expose ses valeurs via os.getenv. C'est ce
# qui permet de configurer le modèle, les URL et les clés hors du code (secrets
# locaux gitignorés), sans rien coder en dur dans ce fichier.
from dotenv import load_dotenv
# On réutilise les briques d'ingestion : MÊME modèle d'embedding (sinon les
# vecteurs de la question et ceux du corpus ne seraient pas comparables), MÊME
# convention de nommage des collections, MÊME dossier de persistance. La
# recherche doit s'aligner exactement sur la façon dont les données ont été
# indexées.
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
# Nombre de morceaux ramenés par collection lors de la recherche. On en prend
# quelques-uns (et non un seul) pour donner au LLM un contexte un peu large,
# que le double filtrage resserrera ensuite.
NB_RESULTATS: int = 3
# Seuil de distance maximale pour qu'un morceau soit jugé pertinent.
# 1.4 (au lieu de 1.2) laisse passer les formulations possessives ou
# indirectes (« mes objectifs » vs « Objectifs 2026 ») qui scorent un peu
# plus loin. Le filtre par marge en aval (MARGE_AFFICHAGE) écarte ensuite
# le bruit, donc assouplir ici ne dégrade pas la qualité des sources.
#
# Premier des DEUX filtres du système : ce seuil ABSOLU élimine le bruit
# grossier (morceaux sans rapport avec la question). Voir MARGE_AFFICHAGE pour
# le second, relatif. Une distance PLUS PETITE signifie PLUS proche, donc plus
# pertinent (les embeddings étant normalisés à l'ingestion, cette distance est
# directement interprétable).
SEUIL_PERTINENCE: float = 1.4
MODELE_OLLAMA: str = os.getenv("OLLAMA_MODEL", "mistral")
MODELE_OPENAI: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Marge de tolérance sur le score : on n'affiche une source que si son score
# est proche du meilleur score trouvé. Une source nettement moins pertinente
# (du « bruit » sémantique) est donnée au LLM comme contexte mais pas montrée.
#
# Second filtre, RELATIF cette fois : il se cale sur le meilleur score obtenu
# pour la question, et non sur une valeur fixe. L'idée : ce qui compte n'est
# pas la distance absolue mais l'écart au morceau le plus pertinent. Deux
# morceaux à 0,9 et 0,95 sont tous deux gardés ; un à 1,3 est écarté même s'il
# passe le seuil absolu. C'est ce qui aligne « sources affichées » et « sources
# qui ont réellement nourri la réponse ».
MARGE_AFFICHAGE: float = 0.25

# Réponse de repli unique, utilisée à chaque fois qu'aucun contexte pertinent
# n'est trouvé. La centraliser garantit un message identique partout (route
# complète comme streaming) et reflète la règle n°1 du prompt ci-dessous.
REPONSE_SANS_CONTEXTE: str = (
    "Je ne trouve pas la réponse dans la base documentaire. "
    "Veuillez reformuler votre question ou vérifier que les documents "
    "concernés ont bien été ingérés."
)

# Prompt système : c'est le garde-fou anti-hallucination AU NIVEAU DU MODÈLE.
# Les règles sont volontairement strictes et impératives : répondre uniquement
# d'après le contexte, ne jamais inventer, citer la source, rester en français,
# être concis. Le contexte et la question sont injectés dans les marqueurs
# {context} et {question}. Ce prompt complète le garde-fou ARCHITECTURAL (sans
# contexte pertinent, on n'appelle même pas le LLM) : double protection.
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
# On utilise des dataclasses pour transporter des données structurées entre les
# étapes, plutôt que des dictionnaires libres : les champs sont nommés et typés,
# donc plus sûrs et plus lisibles. field(default_factory=list) crée une liste
# vide propre à chaque instance (même précaution que le default_factory de
# schemas.py : éviter une liste partagée entre instances).
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
    # a_du_contexte est le drapeau clé : il dit si la recherche a trouvé quelque
    # chose d'exploitable. C'est lui que testent repondre() et repondre_stream()
    # pour décider d'appeler le LLM ou de renvoyer la réponse de repli.
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
    # On renvoie None plutôt que de lever une erreur quand la collection n'existe
    # pas ou est vide : une collection privée absente (utilisateur sans document)
    # est un cas NORMAL, pas une panne. Les appelants traitent ce None comme
    # « rien à chercher ici » et passent à la suite.
    if not DOSSIER_VECTORS.exists():
        return None
    try:
        modele = charger_modele_embedding()
        vs = Chroma(
            collection_name=nom_collection,
            embedding_function=modele,
            persist_directory=str(DOSSIER_VECTORS),
        )
        # Une collection existante mais vide ne sert à rien : on la traite comme
        # absente pour éviter une recherche inutile.
        if vs._collection.count() == 0:
            return None
        return vs
    except Exception as exc:
        # En debug seulement : l'inaccessibilité d'une collection est un cas géré
        # (on renvoie None), pas une erreur à remonter bruyamment.
        logger.debug("Collection '%s' inaccessible : %s", nom_collection, exc)
        return None


def charger_vectorstore():
    """Charge la collection partagée. Utilisée par la route /health."""
    # Variante simple sans contrôle de vacuité : /health veut juste un objet
    # vectorstore pour compter les morceaux indexés. La gestion du cas vide est
    # faite côté route (cf. main.py).
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
    # Le choix du LLM est piloté par l'environnement, pas codé en dur : par
    # défaut Ollama en local (cohérent avec l'objectif « aucune donnée ne quitte
    # l'infrastructure »), avec une bascule optionnelle vers OpenAI si on le
    # demande explicitement ET qu'une clé est fournie.
    utiliser_openai = os.getenv("USE_OPENAI", "false").lower() == "true"
    if utiliser_openai:
        cle = os.getenv("OPENAI_API_KEY")
        # On échoue tôt et clairement si la config est incohérente (OpenAI
        # demandé mais clé absente), plutôt que de laisser une erreur obscure
        # surgir au premier appel réseau.
        if not cle:
            raise EnvironmentError("USE_OPENAI=true mais OPENAI_API_KEY manquant.")
        logger.info("LLM : OpenAI (%s)", MODELE_OPENAI)
        # temperature=0 : on veut des réponses déterministes et factuelles,
        # collées au contexte, pas de créativité. Essentiel pour un assistant
        # documentaire dont la fidélité aux sources prime.
        return ChatOpenAI(model=MODELE_OPENAI, temperature=0)
    url_ollama = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    logger.info("LLM : Ollama (%s) @ %s", MODELE_OLLAMA, url_ollama)
    # Même temperature=0 côté Ollama, pour la même raison.
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
    # L'opérateur « | » chaîne les étapes (syntaxe LCEL de LangChain) : le prompt
    # rempli alimente le LLM, dont la sortie passe par un parser qui en extrait
    # le texte brut. La même chaîne sait répondre d'un bloc (invoke) ou en flux
    # (stream), d'où la factorisation.
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
    # Collection absente/vide : on ne fait rien (l'isolation reste intacte, on
    # n'a tout simplement rien à ajouter depuis cette source).
    if not vs:
        return
    # similarity_search_with_score renvoie chaque morceau AVEC sa distance. On
    # applique ici le PREMIER filtre (seuil absolu) : seuls les morceaux
    # suffisamment proches sont conservés. On note aussi l'origine (privé ou
    # partagé) pour pouvoir ventiler les sources ensuite.
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

    # On interroge DEUX collections et UNIQUEMENT deux : la partagée (commune à
    # tous) et la collection privée DE CET utilisateur. À aucun moment on ne
    # touche la collection d'un autre : c'est ici que l'isolation par
    # utilisateur se concrétise au moment de la recherche.
    _chercher_dans(COLLECTION_PARTAGEE, "partage", question, resultats)
    _chercher_dans(
        nom_collection_utilisateur(nom_utilisateur), "prive", question, resultats
    )

    # Aucun morceau sous le seuil : c'est le garde-fou ARCHITECTURAL. On le
    # signale et on renvoie du vide, ce qui fera court-circuiter l'appel au LLM
    # en amont. Le modèle n'est jamais sollicité pour une question hors corpus,
    # ce qui rend l'hallucination structurellement impossible dans ce cas.
    if not resultats:
        logger.info("Aucun morceau pertinent trouvé (seuil=%.2f).", SEUIL_PERTINENCE)
        return [], [], []

    # Tri par score croissant : les morceaux les plus pertinents en tête. Ce tri
    # conditionne le « meilleur score » dont dépend le second filtre.
    resultats.sort(key=lambda x: x[1])

    # On attache le score à chaque document pour pouvoir filtrer l'affichage
    # des sources plus tard (sans réinterroger ChromaDB).
    for doc, score, _origine in resultats:
        doc.metadata["_score"] = float(score)

    documents = [r[0] for r in resultats]
    # set(...) déduplique : un même fichier source peut fournir plusieurs
    # morceaux, on ne veut le citer qu'une fois. sorted rend l'affichage stable.
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
    # On numérote chaque extrait et on rappelle sa source dans le texte injecté
    # au LLM : cela aide le modèle à attribuer correctement ses citations
    # (règle n°3 du prompt). Le séparateur visible entre extraits évite que le
    # modèle ne les confonde ou ne les fusionne.
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
    # Implémentation du SECOND filtre (relatif). On prend le meilleur score
    # (la plus petite distance, déjà en tête après le tri) comme référence, et
    # on ne garde que ce qui se trouve dans une marge au-dessus. C'est ce qui
    # distingue « assez bon dans l'absolu » de « vraiment dans le sujet ».
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

    # Aucun document pertinent : on renvoie un ContexteRAG avec a_du_contexte
    # à False. C'est le signal qui, en amont, évite tout appel au LLM.
    if not documents:
        return ContexteRAG(
            contexte="", sources=[], sources_privees=[],
            sources_partagees=[], morceaux=[], a_du_contexte=False,
        )

    # Filtre de pertinence appliqué AVANT tout : le contexte LLM et les
    # sources sont alignés sur ce même sous-ensemble pertinent.
    documents = filtrer_par_pertinence(documents)

    # Recalcule les listes de sources à partir des seuls documents retenus.
    # Point essentiel : on reconstruit les sources APRÈS le second filtre, sur
    # le sous-ensemble réellement conservé. Sans ce recalcul, on afficherait des
    # sources écartées du contexte, et la promesse « sources affichées = sources
    # utilisées » serait fausse. On classe « privé » d'après l'appartenance à
    # l'ensemble des sources privées, le reste étant considéré comme partagé.
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
    # LE garde-fou architectural en action : sans contexte pertinent, on renvoie
    # directement la réponse de repli SANS jamais appeler le LLM. C'est ce qui a
    # été validé empiriquement (une question hors corpus est refusée quasi
    # instantanément, sans coût de génération).
    if not ctx.a_du_contexte:
        return ReponseRAG(reponse=REPONSE_SANS_CONTEXTE)

    chaine = construire_chaine()
    # invoke : génération en un seul bloc (par opposition à stream ci-dessous).
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
    # Version génératrice de repondre() : même logique, mais elle PRODUIT des
    # événements au fil de l'eau au lieu de renvoyer un résultat unique. C'est
    # cette fonction que consomme stream.py pour réémettre les tokens en SSE.
    logger.info("=== repondre_stream() utilisateur='%s' : %s ===", nom_utilisateur, question)

    try:
        ctx = preparer_contexte(question, nom_utilisateur)
    except Exception as exc:
        # Une panne pendant la RECHERCHE (avant toute génération) est signalée
        # par un événement d'erreur dédié, puis on s'arrête là.
        logger.error("Erreur de recherche : %s", exc)
        yield {"type": "error", "content": str(exc)}
        return

    # Même garde-fou architectural qu'en mode complet : pas de contexte, pas
    # d'appel au LLM. On émet un événement no_context distinct pour que
    # l'interface affiche un message honnête plutôt qu'une réponse inventée.
    if not ctx.a_du_contexte:
        yield {"type": "no_context", "content": REPONSE_SANS_CONTEXTE}
        return

    chaine = construire_chaine()

    try:
        # stream() produit la réponse token par token : on relaie chacun
        # immédiatement, ce qui donne l'effet « machine à écrire » côté client.
        for token in chaine.stream({"context": ctx.contexte, "question": question}):
            yield {"type": "token", "content": token}
    except Exception as exc:
        # Erreur survenue PENDANT la génération (typiquement Ollama qui tombe) :
        # on bascule sur un événement d'erreur et on arrête le flux.
        logger.error("Erreur du LLM en streaming : %s", exc)
        yield {"type": "error", "content": f"Erreur LLM : {exc}"}
        return

    # Événement final : il transporte les sources (et leur ventilation
    # privé/partagé) APRÈS la fin de la génération. stream.py s'en sert pour
    # afficher les références et archiver l'échange.
    logger.info("repondre_stream() terminé. Sources : %s", ctx.sources)
    yield {
        "type": "done",
        "sources": ctx.sources,
        "private_sources": ctx.sources_privees,
        "shared_sources": ctx.sources_partagees,
    }