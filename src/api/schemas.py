##############################################################
# Nom ......... : schemas.py
# Rôle ........ : Modèles Pydantic de l'API RAG Enterprise.
#                 Ils valident automatiquement les données
#                 entrantes et sortantes de chaque route.
# Auteur ...... : Maxim Khomenko
# Version ..... : V3.1.0 du 23/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py et les routes.
# Dépendances . : pydantic
##############################################################

from pydantic import BaseModel, Field


class RequeteChat(BaseModel):
    """Corps de la requête POST /chat."""
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
        return str(self.session_id) if self.session_id else str(nom_utilisateur)


class ReponseChat(BaseModel):
    """Corps de la réponse de POST /chat."""
    reponse: str
    sources: list[str] = Field(default_factory=list)


class ReponseSante(BaseModel):
    """Réponse de la route GET /health."""
    statut: str = Field(default="ok")
    nb_documents: int