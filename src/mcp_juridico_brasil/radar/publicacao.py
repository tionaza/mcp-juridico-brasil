"""Normalização de comunicações do DJEN e estimativa de prazo."""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from mcp_juridico_brasil.prazo.calendario import UF_PARA_ISO, calcular_prazo
from mcp_juridico_brasil.radar.cadastro import Advogado, normalizar_oab
from mcp_juridico_brasil.radar.djen import DJEN_BASE_URL

PRAZO_PADRAO_DIAS = 15
_LIMITE_TRECHO = 700
_RE_PRAZO = re.compile(
    r"prazo[^.;]{0,60}?\b(\d{1,3})\s*(?:\([^)]{1,30}\)\s*)?dias?",
    re.IGNORECASE,
)
_RE_PRAZO_HORAS = re.compile(
    r"prazo[^.;]{0,60}?\b(\d{1,3})\s*(?:\([^)]{1,30}\)\s*)?horas", re.IGNORECASE
)
_RE_PENAL = re.compile(
    r"penal|criminal|crimes?\b|habeas|flagrante|antit[oó]xicos|cal[uú]nia|j[uú]ri\b"
    r"|inqu[eé]rito|execu[cç][aã]o da pena|medidas protetivas",
    re.IGNORECASE,
)
_RE_TJ = re.compile(r"^TJ([A-Z]{2})$")


class _ExtratorTexto(HTMLParser):
    _BLOCOS = frozenset({"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._ignorar += 1
        elif tag in self._BLOCOS:
            self._partes.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self._partes.append(data)

    def texto(self) -> str:
        bruto = "".join(self._partes)
        linhas = (" ".join(linha.split()) for linha in bruto.splitlines())
        return "\n".join(linha for linha in linhas if linha)


def html_para_texto(html: str) -> str:
    extrator = _ExtratorTexto()
    extrator.feed(html or "")
    extrator.close()
    return extrator.texto()


@dataclass
class EstimativaPrazo:
    data_publicacao: datetime.date
    vencimento: datetime.date
    dias: int
    contagem: str
    origem: str
    uf_feriados: str


@dataclass
class Publicacao:
    id: int | str
    processo: str
    tribunal: str
    tipo_comunicacao: str
    tipo_documento: str
    orgao: str
    classe: str
    disponibilizacao: datetime.date
    texto: str
    link: str
    certidao: str
    cancelada: bool
    motivo_cancelamento: str
    advogados: list[str] = field(default_factory=list)
    prazo: EstimativaPrazo | None = None
    nova: bool = True

    @property
    def trecho(self) -> str:
        if len(self.texto) <= _LIMITE_TRECHO:
            return self.texto
        return self.texto[:_LIMITE_TRECHO].rsplit(" ", 1)[0] + " [...]"


def _str(valor: Any) -> str:
    return "" if valor is None else str(valor).strip()


def _advogados_da_comunicacao(item: dict[str, Any], monitorados: list[Advogado]) -> list[str]:
    chaves = set()
    for entrada in item.get("destinatarioadvogados") or []:
        adv = entrada.get("advogado") if isinstance(entrada, dict) else None
        if isinstance(adv, dict):
            chaves.add(
                (normalizar_oab(_str(adv.get("numero_oab"))), _str(adv.get("uf_oab")).upper())
            )
    return [a.rotulo for a in monitorados if (a.oab, a.uf) in chaves]


def uf_para_feriados(tribunal: str, uf_padrao: str) -> str:
    casamento = _RE_TJ.match(tribunal.upper())
    if casamento and casamento.group(1) in UF_PARA_ISO:
        return casamento.group(1)
    return uf_padrao


def dias_do_prazo(texto: str) -> tuple[int, str]:
    if "processo sigiloso" in texto.lower():
        return PRAZO_PADRAO_DIAS, (
            f"padrão de {PRAZO_PADRAO_DIAS} dias: processo sigiloso, confira o prazo nos autos"
        )
    em_dias = _RE_PRAZO.search(texto)
    em_horas = _RE_PRAZO_HORAS.search(texto)
    if em_horas and (em_dias is None or em_horas.start() < em_dias.start()):
        horas = int(em_horas.group(1))
        if 0 < horas <= 240:
            return -(-horas // 24), f"{horas} horas lidas no texto da publicação"
    if em_dias:
        dias = int(em_dias.group(1))
        if 0 < dias <= 365:
            return dias, "lido no texto da publicação"
    return PRAZO_PADRAO_DIAS, f"padrão de {PRAZO_PADRAO_DIAS} dias (não encontrado no texto)"


def _proximo_dia_util(data: datetime.date, uf: str) -> datetime.date:
    return calcular_prazo(data, 1, uf).data_final


def eh_materia_penal(classe: str, orgao: str = "") -> bool:
    return bool(_RE_PENAL.search(classe) or _RE_PENAL.search(orgao))


def estimar_prazo(
    disponibilizacao: datetime.date, texto: str, classe: str, uf: str, orgao: str = ""
) -> EstimativaPrazo:
    publicacao = _proximo_dia_util(disponibilizacao, uf)
    dias, origem = dias_do_prazo(texto)
    if eh_materia_penal(classe, orgao):
        alvo = publicacao + datetime.timedelta(days=dias)
        vencimento = _proximo_dia_util(alvo - datetime.timedelta(days=1), uf)
        contagem = "dias corridos (art. 798 do CPP)"
    else:
        vencimento = calcular_prazo(publicacao, dias, uf).data_final
        contagem = "dias úteis (art. 219 do CPC)"
    return EstimativaPrazo(
        data_publicacao=publicacao,
        vencimento=vencimento,
        dias=dias,
        contagem=contagem,
        origem=origem,
        uf_feriados=uf,
    )


def normalizar(
    item: dict[str, Any], monitorados: list[Advogado], uf_padrao: str
) -> Publicacao | None:
    identificador = item.get("id")
    numero = _str(item.get("numeroprocessocommascara")) or _str(item.get("numero_processo"))
    try:
        disponibilizacao = datetime.date.fromisoformat(_str(item.get("data_disponibilizacao"))[:10])
    except ValueError:
        return None
    if identificador is None or not numero:
        return None
    tribunal = _str(item.get("siglaTribunal")).upper()
    classe = _str(item.get("nomeClasse"))
    texto = html_para_texto(_str(item.get("texto")))
    motivo = _str(item.get("motivo_cancelamento"))
    hash_ = _str(item.get("hash"))
    publicacao = Publicacao(
        id=identificador,
        processo=numero,
        tribunal=tribunal,
        tipo_comunicacao=_str(item.get("tipoComunicacao")),
        tipo_documento=_str(item.get("tipoDocumento")),
        orgao=_str(item.get("nomeOrgao")),
        classe=classe,
        disponibilizacao=disponibilizacao,
        texto=texto,
        link=_str(item.get("link")),
        certidao=f"{DJEN_BASE_URL}/api/v1/comunicacao/{hash_}/certidao" if hash_ else "",
        cancelada=bool(motivo),
        motivo_cancelamento=motivo,
        advogados=_advogados_da_comunicacao(item, monitorados),
    )
    if not publicacao.cancelada:
        publicacao.prazo = estimar_prazo(
            disponibilizacao,
            texto,
            classe,
            uf_para_feriados(tribunal, uf_padrao),
            orgao=publicacao.orgao,
        )
    return publicacao


__all__ = [
    "PRAZO_PADRAO_DIAS",
    "EstimativaPrazo",
    "Publicacao",
    "dias_do_prazo",
    "eh_materia_penal",
    "estimar_prazo",
    "html_para_texto",
    "normalizar",
    "uf_para_feriados",
]
