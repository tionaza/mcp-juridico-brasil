"""Tools MCP do Radar de publicações e prazos."""

from __future__ import annotations

import datetime
from dataclasses import asdict
from typing import Any

from mcp_juridico_brasil._core import JuridicoValidationError
from mcp_juridico_brasil.radar.cadastro import adicionar_advogado, definir_ativo, listar_advogados
from mcp_juridico_brasil.radar.executor import AVISO, executar_radar, gerar_markdown


async def radar_listar_advogados(incluir_inativos: bool = False) -> dict[str, Any]:
    """Lista os advogados (titulares e associados) monitorados pelo Radar no DJEN.

    Args:
        incluir_inativos: Se True, inclui advogados desativados.
    """
    advogados = listar_advogados(incluir_inativos=incluir_inativos)
    return {"total": len(advogados), "advogados": [asdict(a) for a in advogados]}


async def radar_cadastrar_advogado(
    nome: str, oab: str, uf: str, papel: str = "associado", email: str = ""
) -> dict[str, Any]:
    """Cadastra (ou reativa) um advogado no Radar para monitorar suas publicações no DJEN.

    Args:
        nome: Nome completo do advogado.
        oab: Número de inscrição na OAB (só o número; sufixos são tratados pelo Radar).
        uf: UF da inscrição (ex.: 'PA').
        papel: 'titular' ou 'associado'.
        email: E-mail para receber o resumo (opcional).
    """
    try:
        advogado = adicionar_advogado(nome, oab, uf, papel, email)
    except JuridicoValidationError as exc:
        return {"erro": exc.message}
    return {"cadastrado": True, "advogado": asdict(advogado)}


async def radar_desativar_advogado(oab: str, uf: str) -> dict[str, Any]:
    """Para de monitorar um advogado no Radar sem apagar o histórico.

    Args:
        oab: Número de inscrição na OAB.
        uf: UF da inscrição.
    """
    try:
        advogado = definir_ativo(oab, uf, ativo=False)
    except JuridicoValidationError as exc:
        return {"erro": exc.message}
    return {"desativado": True, "advogado": asdict(advogado)}


async def radar_executar(
    data_referencia_iso: str | None = None, dias_janela: int = 2
) -> dict[str, Any]:
    """Busca no DJEN as publicações dos advogados cadastrados e estima os prazos.

    Precisa rodar de um IP no Brasil (o DJEN bloqueia acessos do exterior).

    Args:
        data_referencia_iso: Último dia da janela (AAAA-MM-DD). Padrão: hoje em Brasília.
        dias_janela: Quantos dias consultar até a data de referência (padrão 2: ontem e hoje).
    """
    referencia = None
    if data_referencia_iso:
        try:
            referencia = datetime.date.fromisoformat(data_referencia_iso)
        except ValueError:
            return {"erro": f"Data inválida: {data_referencia_iso!r}. Use AAAA-MM-DD."}
    if not listar_advogados():
        return {"erro": "Nenhum advogado cadastrado. Use radar_cadastrar_advogado primeiro."}
    relatorio = await executar_radar(referencia, dias_janela)
    return {
        "total_publicacoes": len(relatorio.publicacoes),
        "total_novas": len(relatorio.novas),
        "falhas": [asdict(f) for f in relatorio.falhas],
        "arquivos": relatorio.arquivos,
        "relatorio_markdown": gerar_markdown(relatorio),
        "aviso": AVISO,
    }


__all__ = [
    "radar_cadastrar_advogado",
    "radar_desativar_advogado",
    "radar_executar",
    "radar_listar_advogados",
]
