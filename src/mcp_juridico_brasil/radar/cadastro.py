"""Cadastro dos advogados monitorados pelo Radar (titulares e associados).

Persistido em JSON no diretório do Radar (variável RADAR_DIR, padrão
~/.mcp-juridico-radar). Cada advogado é identificado por OAB (só dígitos) + UF.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp_juridico_brasil._core import JuridicoValidationError
from mcp_juridico_brasil.prazo.calendario import UF_PARA_ISO

RADAR_DIR_ENV = "RADAR_DIR"
_ARQUIVO_ADVOGADOS = "advogados.json"
_PAPEIS = ("titular", "associado")
_lock = threading.Lock()


def diretorio_radar() -> Path:
    base = os.environ.get(RADAR_DIR_ENV) or str(Path.home() / ".mcp-juridico-radar")
    caminho = Path(base).expanduser()
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


def normalizar_oab(numero: str) -> str:
    return re.sub(r"\D", "", numero or "").lstrip("0")


@dataclass
class Advogado:
    nome: str
    oab: str
    uf: str
    papel: str = "associado"
    ativo: bool = True
    email: str = ""
    cadastrado_em: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @property
    def chave(self) -> str:
        return f"{self.oab}/{self.uf}"

    @property
    def rotulo(self) -> str:
        return f"{self.nome} (OAB/{self.uf} {self.oab})"


def _validar(nome: str, oab: str, uf: str, papel: str) -> tuple[str, str, str, str]:
    nome_limpo = " ".join(nome.split())
    if not nome_limpo:
        raise JuridicoValidationError("nome", nome, "Informe o nome do advogado.")
    oab_limpa = normalizar_oab(oab)
    if not oab_limpa or len(oab_limpa) > 7:
        raise JuridicoValidationError("oab", oab, "Informe o número da OAB (até 7 dígitos).")
    uf_limpa = uf.strip().upper()
    if uf_limpa not in UF_PARA_ISO:
        raise JuridicoValidationError("uf", uf, "Informe a sigla da UF da inscrição (ex.: PA).")
    papel_limpo = papel.strip().lower()
    if papel_limpo not in _PAPEIS:
        raise JuridicoValidationError("papel", papel, "Use 'titular' ou 'associado'.")
    return nome_limpo, oab_limpa, uf_limpa, papel_limpo


def _arquivo() -> Path:
    return diretorio_radar() / _ARQUIVO_ADVOGADOS


def _ler() -> list[Advogado]:
    caminho = _arquivo()
    if not caminho.exists():
        return []
    dados: list[dict[str, Any]] = json.loads(caminho.read_text(encoding="utf-8"))
    return [Advogado(**item) for item in dados]


def _gravar(advogados: list[Advogado]) -> None:
    caminho = _arquivo()
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(
        json.dumps([asdict(a) for a in advogados], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporario.replace(caminho)


def listar_advogados(incluir_inativos: bool = False) -> list[Advogado]:
    with _lock:
        advogados = _ler()
    return [a for a in advogados if incluir_inativos or a.ativo]


def adicionar_advogado(
    nome: str, oab: str, uf: str, papel: str = "associado", email: str = ""
) -> Advogado:
    nome_l, oab_l, uf_l, papel_l = _validar(nome, oab, uf, papel)
    with _lock:
        advogados = _ler()
        for existente in advogados:
            if existente.oab == oab_l and existente.uf == uf_l:
                existente.nome = nome_l
                existente.papel = papel_l
                existente.email = email.strip() or existente.email
                existente.ativo = True
                _gravar(advogados)
                return existente
        novo = Advogado(nome=nome_l, oab=oab_l, uf=uf_l, papel=papel_l, email=email.strip())
        advogados.append(novo)
        _gravar(advogados)
        return novo


def definir_ativo(oab: str, uf: str, ativo: bool) -> Advogado:
    oab_l = normalizar_oab(oab)
    uf_l = uf.strip().upper()
    with _lock:
        advogados = _ler()
        for existente in advogados:
            if existente.oab == oab_l and existente.uf == uf_l:
                existente.ativo = ativo
                _gravar(advogados)
                return existente
    raise JuridicoValidationError("oab", f"{oab}/{uf}", "Advogado não cadastrado no Radar.")


__all__ = [
    "RADAR_DIR_ENV",
    "Advogado",
    "adicionar_advogado",
    "definir_ativo",
    "diretorio_radar",
    "listar_advogados",
    "normalizar_oab",
]
