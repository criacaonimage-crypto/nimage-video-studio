"""Banco SQLite local: padrões, projetos e configurações."""
from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from . import config
from .patterns import completar, hash_padrao

SCHEMA = """
CREATE TABLE IF NOT EXISTS padroes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE,                 -- preenchido só nos padrões que vêm com o programa
    embutido INTEGER NOT NULL DEFAULT 0,
    hash_embutido TEXT,               -- hash da versão do programa, para saber se foi editado
    excluido INTEGER NOT NULL DEFAULT 0,
    dados TEXT NOT NULL,
    criado TEXT NOT NULL,
    atualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projetos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL,
    dados TEXT NOT NULL,              -- vídeos, configuração e CÓPIA do padrão usado
    criado TEXT NOT NULL,
    atualizado TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuracoes (
    chave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def _agora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or config.db_path())
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self.sincronizar_padroes_embutidos()

    # ---------- padrões ----------
    def _carregar_embutidos(self) -> list[dict]:
        arq = config.resources_dir() / "padroes_embutidos.json"
        return json.loads(arq.read_text(encoding="utf-8"))["padroes"]

    def sincronizar_padroes_embutidos(self) -> None:
        """Insere padrões novos do programa e atualiza os que o usuário não editou.

        Padrões embutidos que o usuário editou ficam como estão; os que ele excluiu não voltam.
        """
        for item in self._carregar_embutidos():
            dados = completar(item["padrao"])
            h = hash_padrao(dados)
            row = self.conn.execute("SELECT * FROM padroes WHERE slug=?", (item["slug"],)).fetchone()
            if row is None:
                # Padrão personalizado idêntico (ex.: exportado deste PC para o programa) passa a ser o embutido.
                for r2 in self.conn.execute("SELECT id, dados FROM padroes WHERE slug IS NULL").fetchall():
                    if hash_padrao(completar(json.loads(r2["dados"]))) == h:
                        self.conn.execute("UPDATE padroes SET slug=?, embutido=1, hash_embutido=? WHERE id=?",
                                          (item["slug"], h, r2["id"]))
                        break
                else:
                    self.conn.execute(
                        "INSERT INTO padroes (slug, embutido, hash_embutido, dados, criado, atualizado) VALUES (?,?,?,?,?,?)",
                        (item["slug"], 1, h, json.dumps(dados, ensure_ascii=False), _agora(), _agora()))
                continue
            if not row["excluido"] and row["hash_embutido"] != h:
                local_h = hash_padrao(completar(json.loads(row["dados"])))
                if local_h == h:  # já igual à versão nova (ex.: foi este PC que exportou)
                    self.conn.execute("UPDATE padroes SET hash_embutido=? WHERE id=?", (h, row["id"]))
                elif local_h == row["hash_embutido"]:  # não editado → recebe a versão nova
                    self.conn.execute("UPDATE padroes SET dados=?, hash_embutido=?, atualizado=? WHERE id=?",
                                      (json.dumps(dados, ensure_ascii=False), h, _agora(), row["id"]))
        self.conn.commit()

    def restaurar_padroes_embutidos(self) -> None:
        """Volta todos os padrões do programa ao original (inclusive os excluídos)."""
        for item in self._carregar_embutidos():
            dados = completar(item["padrao"])
            self.conn.execute("DELETE FROM padroes WHERE slug=?", (item["slug"],))
            self.conn.execute(
                "INSERT INTO padroes (slug, embutido, hash_embutido, dados, criado, atualizado) VALUES (?,?,?,?,?,?)",
                (item["slug"], 1, hash_padrao(dados), json.dumps(dados, ensure_ascii=False), _agora(), _agora()))
        self.conn.commit()

    def listar_padroes(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, slug, embutido, dados FROM padroes WHERE excluido=0 ORDER BY embutido DESC, id").fetchall()
        return [{"id": r["id"], "slug": r["slug"], "embutido": bool(r["embutido"]),
                 "dados": completar(json.loads(r["dados"]))} for r in rows]

    def obter_padrao(self, pid: int) -> dict | None:
        r = self.conn.execute("SELECT dados FROM padroes WHERE id=? AND excluido=0", (pid,)).fetchone()
        return completar(json.loads(r["dados"])) if r else None

    def criar_padrao(self, dados: dict) -> int:
        cur = self.conn.execute("INSERT INTO padroes (dados, criado, atualizado) VALUES (?,?,?)",
                                (json.dumps(completar(dados), ensure_ascii=False), _agora(), _agora()))
        self.conn.commit()
        return cur.lastrowid

    def salvar_padrao(self, pid: int, dados: dict) -> None:
        self.conn.execute("UPDATE padroes SET dados=?, atualizado=? WHERE id=?",
                          (json.dumps(completar(dados), ensure_ascii=False), _agora(), pid))
        self.conn.commit()

    def duplicar_padrao(self, pid: int) -> int:
        dados = copy.deepcopy(self.obter_padrao(pid))
        nomes = {p["dados"]["nome"] for p in self.listar_padroes()}
        base = f"{dados['nome']} (cópia)"
        nome, n = base, 2
        while nome in nomes:
            nome, n = f"{base} {n}", n + 1
        dados["nome"] = nome
        return self.criar_padrao(dados)

    def excluir_padrao(self, pid: int) -> None:
        r = self.conn.execute("SELECT embutido FROM padroes WHERE id=?", (pid,)).fetchone()
        if r and r["embutido"]:
            self.conn.execute("UPDATE padroes SET excluido=1, atualizado=? WHERE id=?", (_agora(), pid))
        else:
            self.conn.execute("DELETE FROM padroes WHERE id=?", (pid,))
        self.conn.commit()

    def exportar_embutidos_json(self) -> dict:
        """Gera o conteúdo de padroes_embutidos.json com os padrões atuais, para levar ao GitHub."""
        import re
        import unicodedata
        itens, usados = [], set()
        for p in self.listar_padroes():
            slug = p["slug"]
            if not slug:
                s = unicodedata.normalize("NFKD", p["dados"]["nome"]).encode("ascii", "ignore").decode()
                slug = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "padrao"
            while slug in usados:
                slug += "-2"
            usados.add(slug)
            itens.append({"slug": slug, "padrao": p["dados"]})
        return {"versao": 1, "observacao": "Exportado pelo NIMAGE VIDEO STUDIO", "padroes": itens}

    # ---------- projetos ----------
    def salvar_projeto(self, nome: str, dados: dict, pid: int | None = None) -> int:
        txt = json.dumps(dados, ensure_ascii=False)
        if pid:
            self.conn.execute("UPDATE projetos SET nome=?, dados=?, atualizado=? WHERE id=?", (nome, txt, _agora(), pid))
        else:
            pid = self.conn.execute("INSERT INTO projetos (nome, dados, criado, atualizado) VALUES (?,?,?,?)",
                                    (nome, txt, _agora(), _agora())).lastrowid
        self.conn.commit()
        return pid

    def listar_projetos(self, limite: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT id, nome, dados, atualizado FROM projetos ORDER BY atualizado DESC, id DESC LIMIT ?",
                                 (limite,)).fetchall()
        return [{"id": r["id"], "nome": r["nome"], "atualizado": r["atualizado"], "dados": json.loads(r["dados"])} for r in rows]

    def obter_projeto(self, pid: int) -> dict | None:
        r = self.conn.execute("SELECT id, nome, dados FROM projetos WHERE id=?", (pid,)).fetchone()
        return {"id": r["id"], "nome": r["nome"], "dados": json.loads(r["dados"])} if r else None

    def excluir_projeto(self, pid: int) -> None:
        self.conn.execute("DELETE FROM projetos WHERE id=?", (pid,))
        self.conn.commit()

    # ---------- configurações ----------
    def get(self, chave: str, padrao: str | None = None) -> str | None:
        r = self.conn.execute("SELECT valor FROM configuracoes WHERE chave=?", (chave,)).fetchone()
        return r["valor"] if r else padrao

    def set(self, chave: str, valor: str) -> None:
        self.conn.execute("INSERT INTO configuracoes (chave, valor) VALUES (?,?) "
                          "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))
        self.conn.commit()
