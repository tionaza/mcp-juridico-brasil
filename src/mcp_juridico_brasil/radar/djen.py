"""Cliente da API pública do DJEN (Comunica PJe) para busca de comunicações por OAB.

Regras de produção aplicadas (skill `djen` de skills-datajud-djen):
- IP fora do Brasil recebe 403: o Radar precisa rodar de uma máquina no Brasil.
- `itensPorPagina` acima de 50 devolve lista vazia sem erro: usamos 50.
- Página vazia no meio da paginação costuma ser transitória: repetimos a mesma página.
- `numeroOab` é comparado como texto exato e cada tribunal grava um sufixo
  diferente, então consultamos todas as variantes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from mcp_juridico_brasil._core import JuridicoAPIError, get_logger

logger = get_logger(__name__)

DJEN_BASE_URL = "https://comunicaapi.pje.jus.br"
_PATH = "/api/v1/comunicacao"
SUFIXOS_OAB = ("", "-O", "-A", "-N", "-B", "-S", "-E")
ITENS_POR_PAGINA = 50
MAX_PAGINAS = 200
MAX_RETENTATIVAS_PAGINA_VAZIA = 2
MAX_RETENTATIVAS_HTTP = 3
_STATUS_RETENTAVEIS = {429, 500, 502, 503, 504}

Dormir = Callable[[float], Awaitable[None]]


@dataclass
class ResultadoBusca:
    itens: list[dict[str, Any]] = field(default_factory=list)
    incompleta: list[str] = field(default_factory=list)


def variantes_oab(oab: str) -> list[str]:
    return [f"{oab}{sufixo}" for sufixo in SUFIXOS_OAB]


class DjenClient:
    def __init__(
        self,
        base_url: str = DJEN_BASE_URL,
        timeout: float = 30.0,
        pausa_entre_consultas: float = 0.5,
        dormir: Dormir = asyncio.sleep,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout
        self._pausa = pausa_entre_consultas
        self._dormir = dormir
        self._transport = transport

    async def buscar_por_oab(self, oab: str, uf: str, inicio: date, fim: date) -> ResultadoBusca:
        resultado = ResultadoBusca()
        vistos: set[Any] = set()
        async with httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
            transport=self._transport,
        ) as cliente:
            for indice, variante in enumerate(variantes_oab(oab)):
                if indice:
                    await self._dormir(self._pausa)
                params = {
                    "numeroOab": variante,
                    "ufOab": uf,
                    "dataDisponibilizacaoInicio": inicio.isoformat(),
                    "dataDisponibilizacaoFim": fim.isoformat(),
                }
                itens, completo = await self._paginar(cliente, params)
                if not completo:
                    resultado.incompleta.append(variante)
                for item in itens:
                    chave = item.get("id")
                    if chave is None or chave in vistos:
                        continue
                    vistos.add(chave)
                    resultado.itens.append(item)
        return resultado

    async def _paginar(
        self, cliente: httpx.AsyncClient, params: dict[str, str]
    ) -> tuple[list[dict[str, Any]], bool]:
        itens: list[dict[str, Any]] = []
        total: int | None = None
        retentativas = 0
        pagina = 1
        while pagina <= MAX_PAGINAS:
            dados = await self._get(
                cliente,
                {**params, "pagina": str(pagina), "itensPorPagina": str(ITENS_POR_PAGINA)},
            )
            if total is None:
                total = int(dados.get("count") or 0)
            pagina_itens = dados.get("items")
            pagina_itens = pagina_itens if isinstance(pagina_itens, list) else []
            if not pagina_itens:
                if len(itens) >= total:
                    break
                if retentativas >= MAX_RETENTATIVAS_PAGINA_VAZIA:
                    logger.warning(
                        "djen_paginacao_incompleta",
                        numero_oab=params["numeroOab"],
                        coletados=len(itens),
                        total=total,
                    )
                    return itens, False
                retentativas += 1
                await self._dormir(1.0 * retentativas)
                continue
            retentativas = 0
            itens.extend(pagina_itens)
            if len(itens) >= total:
                break
            pagina += 1
        return itens, len(itens) >= (total or 0)

    async def _get(self, cliente: httpx.AsyncClient, params: dict[str, str]) -> dict[str, Any]:
        for tentativa in range(1, MAX_RETENTATIVAS_HTTP + 1):
            try:
                resposta = await cliente.get(_PATH, params=params)
            except httpx.TransportError as exc:
                if tentativa == MAX_RETENTATIVAS_HTTP:
                    raise JuridicoAPIError("DJEN", reason=f"falha de conexão: {exc}") from exc
                await self._dormir(2.0**tentativa)
                continue
            if resposta.status_code == 403:
                raise JuridicoAPIError(
                    "DJEN",
                    status_code=403,
                    reason=(
                        "acesso negado. O DJEN bloqueia conexões de fora do Brasil; "
                        "rode o Radar em um computador ou servidor no Brasil."
                    ),
                )
            if resposta.status_code in _STATUS_RETENTAVEIS and tentativa < MAX_RETENTATIVAS_HTTP:
                await self._dormir(2.0**tentativa)
                continue
            if resposta.status_code >= 400:
                raise JuridicoAPIError("DJEN", status_code=resposta.status_code)
            corpo = resposta.json()
            return corpo if isinstance(corpo, dict) else {}
        raise JuridicoAPIError("DJEN", reason="sem resposta após retentativas")


__all__ = ["DJEN_BASE_URL", "SUFIXOS_OAB", "DjenClient", "ResultadoBusca", "variantes_oab"]
