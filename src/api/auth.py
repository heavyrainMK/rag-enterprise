##############################################################
# Nom ......... : auth.py
# Rôle ........ : Authentification de l'API RAG Enterprise.
#                 Hachage des mots de passe (bcrypt), création
#                 et vérification des jetons JWT, et dépendances
#                 FastAPI pour protéger les routes.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 27/06/2026
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
# OAuth2PasswordBearer met en place le schéma d'authentification « Bearer
# token » : il indique à FastAPI où se trouve la route de login et comment
# extraire automatiquement le jeton de l'en-tête Authorization des requêtes.
from fastapi.security import OAuth2PasswordBearer
# python-jose fournit l'encodage et le décodage des JWT. JWTError est l'erreur
# qu'il lève quand un jeton est malformé, mal signé ou expiré ; on la capture
# pour la transformer en réponse HTTP propre plutôt qu'en plantage serveur.
from jose import JWTError, jwt

from src.core.database import Utilisateur, get_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration JWT
# ---------------------------------------------------------------------------
# Valeur par défaut du secret - à remplacer en production.
# Ce secret signe les jetons : quiconque le connaît peut forger des jetons
# valides et usurper n'importe quel compte. On le garde donc hors du code en
# production, via la variable d'environnement JWT_SECRET_KEY. La valeur en
# dur ci-dessous n'est qu'un repli pour le développement local, et la
# fonction secret_est_faible (plus bas) sert justement à refuser de démarrer
# en production si ce repli n'a pas été remplacé.
SECRET_PAR_DEFAUT: str = "changer-cette-cle-secrete-en-production"
CLE_SECRETE: str = os.getenv("JWT_SECRET_KEY", SECRET_PAR_DEFAUT)
# HS256 : signature symétrique (le même secret signe et vérifie). Simple et
# suffisant ici, où c'est le même service qui émet et valide les jetons.
ALGORITHME: str = "HS256"
# Durée de vie d'un jeton, en minutes (480 = 8 h par défaut, soit une journée
# de travail). Paramétrable par variable d'environnement. C'est un compromis :
# trop court, l'utilisateur se reconnecte sans cesse ; trop long, un jeton
# volé reste exploitable longtemps.
DUREE_JETON_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))

# tokenUrl indique à la documentation Swagger (et au flux OAuth2) quelle route
# appeler pour obtenir un jeton. Cela alimente aussi le bouton « Authorize »
# de l'interface auto-générée.
schema_oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


def secret_est_faible():
    """Indique si le secret JWT est absent ou laissé à sa valeur par défaut."""
    # Appelée au démarrage (voir la vérification de configuration de main.py) :
    # si le secret est vide ou resté sur sa valeur d'exemple, on est en
    # situation dangereuse en production. Centraliser ce test ici évite de
    # dupliquer la condition ailleurs.
    return (not CLE_SECRETE) or (CLE_SECRETE == SECRET_PAR_DEFAUT)


# ---------------------------------------------------------------------------
# Mots de passe
# ---------------------------------------------------------------------------
def hacher_mot_de_passe(mot_de_passe):
    """Hache un mot de passe avec bcrypt."""
    # On ne stocke JAMAIS un mot de passe en clair. bcrypt produit une
    # empreinte irréversible : on ne peut pas retrouver le mot de passe à
    # partir du hachage. gensalt génère un « sel » aléatoire intégré au
    # résultat, si bien que deux utilisateurs ayant le même mot de passe
    # obtiennent deux hachages différents, ce qui contre les attaques par
    # tables précalculées. bcrypt est aussi volontairement lent, pour rendre
    # les attaques par force brute coûteuses.
    return bcrypt.hashpw(mot_de_passe.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verifier_mot_de_passe(mot_de_passe, hache):
    """Vérifie qu'un mot de passe correspond au hachage stocké."""
    # checkpw rehache le mot de passe fourni avec le sel contenu dans le
    # hachage stocké, puis compare. La comparaison se fait en temps constant,
    # ce qui évite de fuiter de l'information par le temps de réponse.
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
    # On copie le dictionnaire reçu avant d'y ajouter l'expiration, pour ne pas
    # modifier l'objet de l'appelant par effet de bord (bonne hygiène : une
    # fonction ne devrait pas muter ses arguments à l'insu de qui l'appelle).
    a_encoder = donnees.copy()
    # On calcule l'instant d'expiration en UTC (datetime « conscient » du
    # fuseau) pour éviter toute ambiguïté de fuseau horaire entre serveurs.
    expiration = datetime.now(timezone.utc) + timedelta(minutes=DUREE_JETON_MINUTES)
    # « exp » est un champ standard du JWT : les bibliothèques le reconnaissent
    # et rejettent automatiquement un jeton dont la date est dépassée.
    a_encoder["exp"] = expiration
    return jwt.encode(a_encoder, CLE_SECRETE, algorithm=ALGORITHME)


def decoder_jeton(jeton):
    """
    Décode et vérifie un jeton JWT.

    Lève une HTTPException 401 si le jeton est invalide ou expiré.
    """
    try:
        # jwt.decode vérifie la signature ET l'expiration en une seule étape :
        # si le secret ne correspond pas ou si le jeton est périmé, il lève
        # JWTError. Un décodage qui réussit garantit donc un jeton authentique
        # et encore valide.
        contenu = jwt.decode(jeton, CLE_SECRETE, algorithms=[ALGORITHME])
        nom = contenu.get("sub")
        # « sub » (subject) identifie le titulaire du jeton. Un jeton
        # techniquement valide mais sans sujet est inexploitable : on le
        # rejette explicitement plutôt que de laisser passer un nom vide.
        if not nom:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Jeton invalide : champ 'sub' manquant.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return contenu
    except JWTError as exc:
        # On traduit toute erreur de la bibliothèque en 401 avec un message
        # volontairement vague (« invalide ou expiré ») : on ne révèle pas
        # POURQUOI le jeton est rejeté, pour ne pas aider un attaquant. L'en-
        # tête WWW-Authenticate: Bearer signale au client le schéma attendu.
        # Le « from exc » conserve la cause d'origine dans la trace, utile au
        # diagnostic côté serveur sans l'exposer au client.
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
    # first() renvoie l'utilisateur ou None s'il n'existe pas : les appelants
    # testent ce None pour décider de la suite (créer, refuser, authentifier).
    return db.query(Utilisateur).filter(Utilisateur.nom_utilisateur == nom_utilisateur).first()


def creer_utilisateur(db, nom_utilisateur, mot_de_passe, role="user"):
    """
    Crée un nouvel utilisateur avec son mot de passe haché.

    Lève une HTTPException 409 si le nom est déjà pris.
    """
    # On vérifie l'unicité AVANT d'insérer : 409 Conflict est le code adapté
    # pour « la ressource existe déjà ». Ici, révéler que le nom est pris est
    # acceptable (c'est une inscription, pas une tentative de connexion).
    if trouver_utilisateur(db, nom_utilisateur):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Le nom d'utilisateur '{nom_utilisateur}' est déjà pris.",
        )

    # On ne stocke que le hachage du mot de passe, jamais le mot de passe lui-
    # même : c'est la règle d'or, respectée dès la création du compte.
    utilisateur = Utilisateur(
        nom_utilisateur=nom_utilisateur,
        mot_de_passe_hache=hacher_mot_de_passe(mot_de_passe),
        role=role,
    )
    db.add(utilisateur)
    db.commit()
    # refresh recharge l'objet depuis la base après le commit, afin de récupérer
    # les valeurs générées par la base (id auto-incrémenté, date de création...)
    # qui n'existaient pas encore au moment de l'ajout.
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

    # Choix de sécurité important : on traite « nom inconnu » et « mauvais mot
    # de passe » de façon identique, avec le MÊME message 401. Distinguer les
    # deux permettrait à un attaquant d'énumérer les comptes existants. L'ordre
    # du « or » court-circuite proprement : si l'utilisateur est None, on
    # n'évalue pas la vérification du mot de passe (qui planterait), mais le
    # message unique renvoyé masque de toute façon lequel des deux cas s'applique.
    if utilisateur is None or not verifier_mot_de_passe(mot_de_passe, str(utilisateur.mot_de_passe_hache)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiants incorrects.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Un compte peut exister avec de bons identifiants mais avoir été désactivé
    # par un admin. On le distingue ici par un 403 (interdit) explicite : à ce
    # stade l'identité est prouvée, donc lui dire « compte désactivé » ne fuite
    # rien et l'oriente vers la bonne action (contacter un admin).
    if not bool(utilisateur.actif):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé. Contactez un administrateur.",
        )

    return utilisateur


# ---------------------------------------------------------------------------
# Dépendances FastAPI
# ---------------------------------------------------------------------------
# Ces fonctions sont conçues pour être injectées dans les routes via Depends.
# FastAPI les exécute AVANT le corps de la route : si elles lèvent une erreur,
# la route n'est jamais atteinte. C'est ainsi qu'on protège un endpoint sans
# répéter le contrôle d'accès dans chaque fonction.
def utilisateur_courant(jeton=Depends(schema_oauth2), db=Depends(get_db)):
    """Extrait et vérifie l'utilisateur à partir du jeton JWT."""
    # Le jeton est extrait automatiquement de l'en-tête par schema_oauth2. On
    # le décode (signature + expiration), puis on relit l'utilisateur en base.
    contenu = decoder_jeton(jeton)
    nom = contenu.get("sub")
    if not nom:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton invalide.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    utilisateur = trouver_utilisateur(db, nom)

    # On revérifie en base à CHAQUE requête plutôt que de se fier au seul
    # contenu du jeton : un compte peut avoir été supprimé ou désactivé après
    # l'émission du jeton. Sans cette relecture, un jeton encore valide
    # donnerait accès à un compte qui ne devrait plus l'avoir.
    if utilisateur is None or not bool(utilisateur.actif):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou inactif.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return utilisateur


def exiger_admin(utilisateur=Depends(utilisateur_courant)):
    """Vérifie que l'utilisateur connecté est administrateur (sinon 403)."""
    # Cette dépendance s'appuie sur la précédente : utilisateur_courant a déjà
    # authentifié l'appelant, on n'ajoute ici que le contrôle de rôle. 403
    # (et non 401) car l'identité est connue et valide ; ce qui manque, c'est
    # le droit. On chaîne ainsi des dépendances de plus en plus exigeantes.
    if str(utilisateur.role) != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès réservé aux administrateurs.",
        )
    return utilisateur