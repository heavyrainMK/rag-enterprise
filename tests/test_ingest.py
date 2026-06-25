##############################################################
# Nom ......... : test_ingest.py
# Rôle ........ : Tests du pipeline d'ingestion src/core/ingest.py.
#                 Chargement des fichiers, découpage en morceaux,
#                 identifiants stables et noms de collections.
# Auteur ...... : Maxim Khomenko
# Version ..... : V2.1.0 du 23/06/2026
# Licence ..... : Projet de fin de licence en Informatique (L3)
# Usage ....... : pytest tests/test_ingest.py -v
# Dépendances . : pytest, langchain-core, src.core.ingest
##############################################################

from pathlib import Path

import pytest
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
        txt = tmp_path / "politique.txt"
        txt.write_text("Article 1 : Les employés ont 25 jours de congés.")
        docs = charger_documents(tmp_path)
        assert len(docs) == 1
        assert "25 jours" in docs[0].page_content
        assert docs[0].metadata["source"] == "politique.txt"

    def test_dossier_vide(self, tmp_path):
        assert charger_documents(tmp_path) == []

    def test_ignorer_extension_non_supportee(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"fake png")
        (tmp_path / "note.txt").write_text("contenu valide")
        docs = charger_documents(tmp_path)
        assert len(docs) == 1
        assert docs[0].metadata["source"] == "note.txt"

    def test_dossier_inexistant(self):
        docs = charger_documents(Path("/tmp/dossier_inexistant_xyz_123"))
        assert docs == []

    def test_source_est_nom_seul(self, tmp_path):
        (tmp_path / "rapport_q3.txt").write_text("données Q3")
        docs = charger_documents(tmp_path)
        assert docs[0].metadata["source"] == "rapport_q3.txt"
        assert "/" not in docs[0].metadata["source"]


class TestDecoupage:

    def test_doc_court_un_morceau(self):
        doc = Document(page_content="Texte court.", metadata={"source": "test.txt"})
        assert len(decouper_documents([doc])) == 1

    def test_doc_long_plusieurs_morceaux(self):
        texte = "Mot " * 1000
        doc = Document(page_content=texte, metadata={"source": "long.txt"})
        assert len(decouper_documents([doc])) > 1

    def test_morceaux_heritent_metadonnees(self):
        doc = Document(page_content="Article 1. " * 200, metadata={"source": "rh.txt"})
        for morceau in decouper_documents([doc]):
            assert morceau.metadata["source"] == "rh.txt"

    def test_taille_morceau_respectee(self):
        doc = Document(page_content="A" * 5000, metadata={"source": "test.txt"})
        for morceau in decouper_documents([doc]):
            assert len(morceau.page_content) <= TAILLE_MORCEAU + CHEVAUCHEMENT


class TestFonctionsUtilitaires:

    def testid_morceau_stable(self):
        doc = Document(page_content="contenu", metadata={"source": "doc.txt", "page": 0})
        assert id_morceau(doc, 0) == id_morceau(doc, 0)

    def testid_morceau_positions_differentes(self):
        doc = Document(page_content="contenu", metadata={"source": "doc.txt", "page": 0})
        assert id_morceau(doc, 0) != id_morceau(doc, 1)


class TestNomCollection:

    def test_pas_de_collision(self):
        users = ["Alice.B", "alice b", "alice-b", "aliceb", "ALICEB"]
        noms = {nom_collection_utilisateur(u) for u in users}
        assert len(noms) == len(users)

    def test_stable(self):
        assert nom_collection_utilisateur("Bob.X") == nom_collection_utilisateur("Bob.X")

    def test_contraintes_chromadb(self):
        import re
        motif = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*[a-zA-Z0-9]")
        for u in ["alice", "josé", "陳", "用户名", "...", "a", "x" * 100]:
            nom = nom_collection_utilisateur(u)
            assert 3 <= len(nom) <= 63
            assert motif.fullmatch(nom)
            assert ".." not in nom

    def test_non_ascii_distincts(self):
        assert nom_collection_utilisateur("用户名") != nom_collection_utilisateur("陳")