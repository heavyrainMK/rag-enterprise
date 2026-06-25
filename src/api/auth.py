##############################################################
# Nom ......... : auth.py
# Rôle ........ : Authentification de l'API RAG Enterprise.
#                 Hachage des mots de passe (bcrypt), création
#                 et vérification des jetons JWT, et dépendances
#                 FastAPI pour protéger les routes.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.0.0 du 19/06/2026
# Licence ..... : Réalisé dans le cadre d'un projet de fin de
#                 licence en Informatique (L3)
# Usage ....... : Importé par src/api/main.py et les routes.
# Dépendances . : bcrypt, python-jose, fastapi, sqlalchemy
##############################################################

import logging
import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from src.core.database import Utilisateur, get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration JWT
# ---------------------------------------------------------------------------
# Valeur par défaut du secret - à remplacer en production.
SECRET_PAR_DEFAUT: str = "changez-cette-cle-secrete-en-production"
CLE_SECRETE: str = os.getenv("JWT_SECRET_KEY", SECRET_PAR_DEFAUT)
ALGORITHME: str = "HS256"
DUREE_JETON_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

schema_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def secret_est_faible():
    """Indique si le secret JWT est absent ou laissé à sa valeur par défaut."""
    return (not CLE_SECRETE) or (CLE_SECRETE == SECRET_PAR_DEFAUT)


# ---------------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------------
def hacher_mot_de_passe(mot_de_passe):
    """Hache un mot de passe avec bcrypt."""
    return bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifier_mot_de_passe(mot_de_passe, hache):
    """Vérifie qu'un mot de passe correspond au hachage stocké."""
    return bcrypt.checkpw(mot_de_passe.encode("utf-8"), hache.encode("utf-8"))


# ---------------------------------------------------------------------------
# Jetons JWT
# ---------------------------------------------------------------------------
def creer_jeton(donnees):
    """
    Crée un jeton JWT signé avec une date d'expiration.

    Paramètres
    ----------
    donnees : dict
        Contenu à encoder (par ex. {"sub": nom, "role": role}).

    Retour
    ------
    str
        Jeton JWT encodé.
    """
    a_encoder = donnees.copy()
    expiration = datetime.now(timezone.utc) + timedelta(minutes=DUREE_JETON_MINUTES)
    a_encoder["exp"] = expiration
    return jwt.encode(a_encoder, CLE_SECRETE, algorithm=ALGORITHME)


def decoder_jeton(jeton):
    """
    Décode et vérifie un jeton JWT.

    Lève une HTTPException 401 si le jeton est invalide ou expiré.
    """
    try:
        contenu = jwt.decode(jeton, CLE_SECRETE, algorithms=[ALGORITHME])
        nom = contenu.get("sub")
        if not nom:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Jeton invalide : champ 'sub' manquant.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return contenu
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton invalide ou expiré.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# Gestion des utilisateurs
# ---------------------------------------------------------------------------
def trouver_utilisateur(db, nom_utilisateur):
    """Récupère un utilisateur par son nom."""
    return db.query(Utilisateur).filter(Utilisateur.nom_utilisateur == nom_utilisateur).first()


def creer_utilisateur(db, nom_utilisateur, mot_de_passe, role="user"):
    """
    Crée un nouvel utilisateur avec son mot de passe haché.

    Lève une HTTPException 409 si le nom est déjà pris.
    """
    if trouver_utilisateur(db, nom_utilisateur):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Le nom d'utilisateur '{nom_utilisateur}' est déjà pris.",
        )

    utilisateur = Utilisateur(
        nom_utilisateur=nom_utilisateur,
        mot_de_passe_hache=hacher_mot_de_passe(mot_de_passe),
        role=role,
    )
    db.add(utilisateur)
    db.commit()
    db.refresh(utilisateur)
    logger.info("Utilisateur créé : %s (rôle=%s)", nom_utilisateur, role)
    return utilisateur


def authentifier_utilisateur(db, nom_utilisateur, mot_de_passe):
    """
    Vérifie les identifiants d'un utilisateur.

    Lève une HTTPException 401 si les identifiants sont incorrects,
    ou 403 si le compte est désactivé.
    """
    utilisateur = trouver_utilisateur(db, nom_utilisateur)

    if utilisateur is None or not verifier_mot_de_passe(mot_de_passe, str(utilisateur.mot_de_passe_hache)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not bool(utilisateur.actif):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez un administrateur.",
        )

    return utilisateur


# ---------------------------------------------------------------------------
# Dépendances FastAPI
# ---------------------------------------------------------------------------
def utilisateur_courant(jeton=Depends(schema_oauth2), db=Depends(get_db)):
    """Extrait et vérifie l'utilisateur à partir du jeton JWT."""
    contenu = decoder_jeton(jeton)
    nom = contenu.get("sub")
    if not nom:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    utilisateur = trouver_utilisateur(db, nom)

    if utilisateur is None or not bool(utilisateur.actif):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou inactif.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return utilisateur


def exiger_admin(utilisateur=Depends(utilisateur_courant)):
    """Vérifie que l'utilisateur connecté est administrateur (sinon 403)."""
    if str(utilisateur.role) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs.",
        )
    return utilisateur