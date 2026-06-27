##############################################################
# Nom ......... : schemas.py
# Rôle ........ : Modèles Pydantic de l'API RAG Enterprise.
#                 Ils valident automatiquement les données
#                 entrantes et sortantes de chaque route.
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.2.0 du 27/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py et les routes.
# Dépendances . : pydantic
##############################################################

from pydantic import BaseModel, Field


# Ces modèles décrivent la forme des données échangées avec l'API. Leur intérêt
# est double : Pydantic valide automatiquement chaque entrée (une requête
# malformée est rejetée avant d'atteindre la logique métier) et FastAPI s'en
# sert pour générer la documentation Swagger. On définit ainsi un contrat clair
# entre le frontend et le backend, vérifié à l'exécution.
class RequeteChat(BaseModel):
    """Corps de la requête POST /chat."""
    # Le « ... » marque le champ comme OBLIGATOIRE (pas de valeur par défaut).
    # Les bornes min/max sont des garde-fous : on refuse une question vide
    # (inexploitable) comme une question démesurée (qui surchargerait le LLM).
    # Cette validation est faite par Pydantic avant même d'entrer dans la route.
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(
        default=None,
        description="Identifiant de session pour regrouper l'historique.",
    )

    def session_effective(self, nom_utilisateur):
        """
        Retourne la session à utiliser pour l'historique : la session
        explicite si elle est fournie, sinon le nom d'utilisateur (chaque
        utilisateur a ainsi sa propre session par défaut).
        """
        # On centralise ici la règle « quelle session retenir ? », plutôt que
        # de la dupliquer dans /chat et /chat/stream. Conséquence : si aucun
        # session_id n'est fourni, l'historique d'un utilisateur est regroupé
        # sous son propre nom, ce qui lui donne une session par défaut isolée
        # de celle des autres. Le str(...) garantit une chaîne en sortie quel
        # que soit le type reçu.
        return str(self.session_id) if self.session_id else str(nom_utilisateur)


class ReponseChat(BaseModel):
    """Corps de la réponse de POST /chat."""
    reponse: str
    # default_factory=list crée une NOUVELLE liste vide à chaque instance. On
    # n'écrit pas « = [] » comme valeur par défaut, car ce piège classique de
    # Python partagerait la même liste entre toutes les instances. Ici, une
    # réponse sans source renvoie proprement une liste vide, jamais « null ».
    sources: list[str] = Field(default_factory=list)


class ReponseSante(BaseModel):
    """Réponse de la route GET /health."""
    statut: str = Field(default="ok")
    nb_documents: int
    # Champ ajouté pour exposer le modèle de langage réellement actif (lu
    # depuis la configuration RAG), afin que le frontend l'affiche dynamiquement
    # au lieu d'un nom codé en dur. Voir l'usage côté api.ts (interface EtatSante).
    modele: str