##############################################################
# Nom ......... : test_ingest.py
# Rôle ........ : Tests du pipeline d'ingestion src/core/ingest.py.
#                 Chargement des fichiers, découpage en morceaux,
#                 identifiants stables et noms de collections.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.2.0 du 27/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_ingest.py -v
# Dépendances . : pytest, langchain-core, src.core.ingest
##############################################################

from pathlib import Path

from langchain_core.documents import Document

from src.core.ingest import (
    charger_documents,
    decouper_documents,
    nom_collection_utilisateur,
    id_morceau,
    TAILLE_MORCEAU,
    CHEVAUCHEMENT,
)


class TestChargement:

    def test_charger_txt(self, tmp_path):
        # On écrit un vrai fichier dans le dossier temporaire de pytest, puis on
        # vérifie trois choses : le document est chargé, son contenu est lu, et
        # sa métadonnée « source » vaut le nom de fichier. Cette source est
        # essentielle : c'est elle qui permettra plus tard de citer le document.
        txt = tmp_path / "politique.txt"
        txt.write_text("Article 1 : Les employés ont 25 jours de congés.")
        docs = charger_documents(tmp_path)
        assert len(docs) == 1
        assert "25 jours" in docs[0].page_content
        assert docs[0].metadata["source"] == "politique.txt"

    def test_dossier_vide(self, tmp_path):
        # Un dossier sans fichier exploitable donne une liste vide, pas d'erreur :
        # un espace utilisateur vide est un cas normal qui ne doit rien casser.
        assert charger_documents(tmp_path) == []

    def test_ignorer_extension_non_supportee(self, tmp_path):
        # Un .png et un .txt dans le même dossier : seul le .txt est chargé. On
        # valide la liste blanche d'extensions, qui protège le pipeline en ne
        # traitant que les types qu'on sait lire en sécurité.
        (tmp_path / "image.png").write_bytes(b"fake png")
        (tmp_path / "note.txt").write_text("contenu valide")
        docs = charger_documents(tmp_path)
        assert len(docs) == 1
        assert docs[0].metadata["source"] == "note.txt"

    def test_dossier_inexistant(self):
        # Chemin qui n'existe pas : liste vide plutôt qu'exception. Le pipeline
        # reste robuste si on lui passe un dossier absent.
        docs = charger_documents(Path("/tmp/dossier_inexistant_xyz_123"))
        assert docs == []

    def test_source_est_nom_seul(self, tmp_path):
        # La source doit être le NOM du fichier seul, sans aucun morceau de
        # chemin (« / »). On évite ainsi de divulguer l'arborescence du serveur
        # dans les citations affichées à l'utilisateur.
        (tmp_path / "rapport_q3.txt").write_text("données Q3")
        docs = charger_documents(tmp_path)
        assert docs[0].metadata["source"] == "rapport_q3.txt"
        assert "/" not in docs[0].metadata["source"]


class TestDecoupage:

    def test_doc_court_un_morceau(self):
        # Un texte plus court que la taille de morceau ne doit pas être découpé :
        # il reste un seul morceau. Cas de base du découpeur.
        doc = Document(page_content="Texte court.", metadata={"source": "test.txt"})
        assert len(decouper_documents([doc])) == 1

    def test_doc_long_plusieurs_morceaux(self):
        # À l'inverse, un texte volumineux doit produire PLUSIEURS morceaux :
        # c'est tout l'intérêt du découpage, qui permet une recherche fine.
        texte = "Mot " * 1000
        doc = Document(page_content=texte, metadata={"source": "long.txt"})
        assert len(decouper_documents([doc])) > 1

    def test_morceaux_heritent_metadonnees(self):
        # Chaque morceau issu d'un document doit conserver sa source. Sans cet
        # héritage, on ne pourrait plus citer d'où vient un morceau retrouvé : la
        # traçabilité de la source serait perdue dès le découpage.
        doc = Document(page_content="Article 1. " * 200, metadata={"source": "rh.txt"})
        for morceau in decouper_documents([doc]):
            assert morceau.metadata["source"] == "rh.txt"

    def test_taille_morceau_respectee(self):
        # Aucun morceau ne doit dépasser la taille cible AUGMENTÉE du
        # chevauchement : la borne haute réaliste est TAILLE_MORCEAU +
        # CHEVAUCHEMENT, car le chevauchement rajoute volontairement du texte
        # commun entre morceaux voisins. On vérifie que le découpeur respecte
        # bien cette limite configurée.
        doc = Document(page_content="A" * 5000, metadata={"source": "test.txt"})
        for morceau in decouper_documents([doc]):
            assert len(morceau.page_content) <= TAILLE_MORCEAU + CHEVAUCHEMENT


class TestFonctionsUtilitaires:

    def testid_morceau_stable(self):
        # L'identifiant d'un morceau doit être DÉTERMINISTE : mêmes entrées, même
        # id. C'est cette stabilité qui rend la réingestion idempotente (réécrire
        # le même morceau ne crée pas de doublon).
        doc = Document(page_content="contenu", metadata={"source": "doc.txt", "page": 0})
        assert id_morceau(doc, 0) == id_morceau(doc, 0)

    def testid_morceau_positions_differentes(self):
        # Le pendant du test précédent : deux positions différentes doivent
        # donner deux identifiants différents, sinon des morceaux distincts se
        # écraseraient mutuellement dans l'index. La position fait donc bien
        # partie de la clé.
        doc = Document(page_content="contenu", metadata={"source": "doc.txt", "page": 0})
        assert id_morceau(doc, 0) != id_morceau(doc, 1)


class TestNomCollection:

    def test_pas_de_collision(self):
        # LE test clé de l'isolation : cinq variantes d'un même nom (casse,
        # ponctuation, espaces) doivent produire CINQ noms de collection
        # distincts. Si deux d'entre elles collisionnaient, deux utilisateurs
        # différents partageraient une collection et donc verraient les documents
        # l'un de l'autre. C'est ce que le hachage du nom original empêche.
        users = ["Alice.B", "alice b", "alice-b", "aliceb", "ALICEB"]
        noms = {nom_collection_utilisateur(u) for u in users}
        assert len(noms) == len(users)

    def test_stable(self):
        # Un même nom d'utilisateur doit toujours donner la même collection,
        # sinon on ne retrouverait pas ses documents d'une session à l'autre.
        assert nom_collection_utilisateur("Bob.X") == nom_collection_utilisateur("Bob.X")

    def test_contraintes_chromadb(self):
        # ChromaDB impose des règles strictes sur les noms de collection :
        # longueur entre 3 et 63, caractères limités, et pas de « .. ». On les
        # vérifie sur des cas volontairement hostiles (accents, caractères non
        # latins, que des points, nom d'un seul caractère, nom très long). Quelle
        # que soit l'entrée, le nom produit doit rester VALIDE pour ChromaDB,
        # sans quoi la création de collection échouerait à l'exécution.
        import re
        motif = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]")
        for u in ["alice", "josé", "陳", "用户名", "...", "a", "x" * 100]:
            nom = nom_collection_utilisateur(u)
            assert 3 <= len(nom) <= 63
            assert motif.fullmatch(nom)
            assert ".." not in nom

    def test_non_ascii_distincts(self):
        # Deux noms entièrement non-ASCII et différents ne doivent pas se réduire
        # au même résultat. Comme le nettoyage retire ces caractères, c'est le
        # hachage du nom original qui garantit ici encore des collections
        # distinctes, donc l'isolation, même pour des noms non latins.
        assert nom_collection_utilisateur("用户名") != nom_collection_utilisateur("陳")