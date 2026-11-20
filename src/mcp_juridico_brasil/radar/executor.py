"""Execução diária do Radar: busca no DJEN, estimativa de prazos e relatório."""

from __future__ import annotations

import datetime
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mcp_juridico_brasil._core import JuridicoAPIError, get_logger
from mcp_juridico_brasil.radar.cadastro import Advogado, diretorio_radar, listar_advogados
from mcp_juridico_brasil.radar.djen import DjenClient
from mcp_juridico_brasil.radar.publicacao import Publicacao, normalizar

logger = get_logger(__name__)

FUSO_BRASILIA = datetime.timezone(datetime.timedelta(hours=-3))
_ARQUIVO_VISTAS = "vistas.json"
AVISO = (
    "Estimativa de apoio. Não substitui a consulta oficial ao DJEN nem a conferência do "
    "prazo pelo advogado responsável. Não considera feriados municipais, pontos "
    "facultativos, suspensões de expediente nem prazos em dobro (arts. 180, 183, 186 e "
    "229 do CPC)."
)


def hoje_brasilia() -> datetime.date:
    return datetime.datetime.now(FUSO_BRASILIA).date()


@dataclass
class FalhaConsulta:
    advogado: str
    motivo: str


@dataclass
class RelatorioRadar:
    data_referencia: datetime.date
    inicio: datetime.date
    fim: datetime.date
    advogados: list[str]
    publicacoes: list[Publicacao] = field(default_factory=list)
    falhas: list[FalhaConsulta] = field(default_factory=list)
    arquivos: list[str] = field(default_factory=list)

    @property
    def novas(self) -> list[Publicacao]:
        return [p for p in self.publicacoes if p.nova]

    def como_dict(self) -> dict[str, Any]:
        dados = asdict(self)
        dados["total_publicacoes"] = len(self.publicacoes)
        dados["total_novas"] = len(self.novas)
        dados["aviso"] = AVISO
        return dados


def _carregar_vistas(diretorio: Path) -> dict[str, dict[str, Any]]:
    caminho = diretorio / _ARQUIVO_VISTAS
    if not caminho.exists():
        return {}
    dados: dict[str, dict[str, Any]] = json.loads(caminho.read_text(encoding="utf-8"))
    return dados


def _gravar_vistas(diretorio: Path, vistas: dict[str, dict[str, Any]]) -> None:
    caminho = diretorio / _ARQUIVO_VISTAS
    temporario = caminho.with_suffix(".tmp")
    temporario.write_text(json.dumps(vistas, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(caminho)


def _ordenar(publicacao: Publicacao) -> tuple[int, datetime.date, str]:
    if publicacao.prazo is None:
        return (1, publicacao.disponibilizacao, publicacao.processo)
    return (0, publicacao.prazo.vencimento, publicacao.processo)


async def executar_radar(
    data_referencia: datetime.date | None = None,
    dias_janela: int = 2,
    cliente: DjenClient | None = None,
    advogados: list[Advogado] | None = None,
    gravar: bool = True,
) -> RelatorioRadar:
    referencia = data_referencia or hoje_brasilia()
    inicio = referencia - datetime.timedelta(days=max(dias_janela, 1) - 1)
    monitorados = advogados if advogados is not None else listar_advogados()
    cliente = cliente or DjenClient()
    relatorio = RelatorioRadar(
        data_referencia=referencia,
        inicio=inicio,
        fim=referencia,
        advogados=[a.rotulo for a in monitorados],
    )
    por_id: dict[str, Publicacao] = {}
    for advogado in monitorados:
        try:
            busca = await cliente.buscar_por_oab(advogado.oab, advogado.uf, inicio, referencia)
        except JuridicoAPIError as exc:
            relatorio.falhas.append(FalhaConsulta(advogado.rotulo, exc.message))
            continue
        if busca.incompleta:
            relatorio.falhas.append(
                FalhaConsulta(
                    advogado.rotulo,
                    "o DJEN devolveu menos itens que o total anunciado "
                    f"(variantes {', '.join(busca.incompleta)}); rode de novo mais tarde.",
                )
            )
        for item in busca.itens:
            publicacao = normalizar(item, monitorados, advogado.uf)
            if publicacao is None:
                continue
            chave = str(publicacao.id)
            if chave in por_id:
                existentes = por_id[chave].advogados
                existentes.extend(a for a in publicacao.advogados if a not in existentes)
                continue
            if not publicacao.advogados:
                publicacao.advogados = [advogado.rotulo]
            por_id[chave] = publicacao

    diretorio = diretorio_radar()
    vistas = _carregar_vistas(diretorio)
    for chave, publicacao in por_id.items():
        anterior = vistas.get(chave)
        publicacao.nova = anterior is None or (
            publicacao.cancelada and not anterior.get("cancelada", False)
        )
        vistas[chave] = {
            "primeira_vez": (anterior or {}).get("primeira_vez", referencia.isoformat()),
            "cancelada": publicacao.cancelada,
            "processo": publicacao.processo,
        }
    relatorio.publicacoes = sorted(por_id.values(), key=_ordenar)

    if gravar:
        _gravar_vistas(diretorio, vistas)
        pasta = diretorio / "relatorios"
        pasta.mkdir(exist_ok=True)
        md = pasta / f"{referencia.isoformat()}.md"
        js = pasta / f"{referencia.isoformat()}.json"
        md.write_text(gerar_markdown(relatorio), encoding="utf-8")
        js.write_text(
            json.dumps(relatorio.como_dict(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        relatorio.arquivos = [str(md), str(js)]
    return relatorio


def _data_br(data: datetime.date) -> str:
    return data.strftime("%d/%m/%Y")


def _celula(texto: str) -> str:
    return texto.replace("|", "/").replace("\n", " ")


def gerar_markdown(relatorio: RelatorioRadar) -> str:
    linhas = [
        f"# Radar de publicações e prazos: {_data_br(relatorio.data_referencia)}",
        "",
        f"Janela consultada no DJEN: {_data_br(relatorio.inicio)} a {_data_br(relatorio.fim)}. "
        f"Advogados monitorados: {len(relatorio.advogados)}. "
        f"Publicações encontradas: {len(relatorio.publicacoes)} "
        f"({len(relatorio.novas)} novas desde a última execução).",
        "",
        f"> {AVISO}",
        "",
    ]
    if relatorio.falhas:
        linhas += ["## Atenção: consultas com falha", ""]
        linhas += [f"- {_celula(f.advogado)}: {_celula(f.motivo)}" for f in relatorio.falhas]
        linhas.append("")
    if not relatorio.publicacoes:
        linhas += ["Nenhuma publicação no período.", ""]
        return "\n".join(linhas)

    linhas += [
        "## Prazos",
        "",
        "| Vence em | Processo | Tribunal | Comunicação | Advogado(s) | Prazo | Situação |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in relatorio.publicacoes:
        situacao = "CANCELADA" if p.cancelada else ("NOVA" if p.nova else "já vista")
        if p.cancelada and not p.nova:
            situacao = "cancelada (já vista)"
        vence = _data_br(p.prazo.vencimento) if p.prazo else "-"
        prazo = f"{p.prazo.dias} {p.prazo.contagem.split(' (')[0]}" if p.prazo else "-"
        comunicacao = " / ".join(x for x in (p.tipo_comunicacao, p.tipo_documento) if x)
        linhas.append(
            f"| {vence} | {_celula(p.processo)} | {_celula(p.tribunal)} | "
            f"{_celula(comunicacao)} | {_celula('; '.join(p.advogados))} | {prazo} | {situacao} |"
        )
    linhas += ["", "## Detalhes", ""]
    for p in relatorio.publicacoes:
        linhas += [f"### {p.processo} ({p.tribunal})", ""]
        if p.orgao or p.classe:
            linhas.append(f"- Órgão e classe: {' · '.join(x for x in (p.orgao, p.classe) if x)}")
        linhas.append(f"- Disponibilizada em {_data_br(p.disponibilizacao)}")
        if p.prazo:
            linhas.append(
                f"- Publicação considerada em {_data_br(p.prazo.data_publicacao)}; "
                f"prazo de {p.prazo.dias} {p.prazo.contagem}, {p.prazo.origem}; "
                f"vence em {_data_br(p.prazo.vencimento)} (feriados da UF {p.prazo.uf_feriados})"
            )
        if p.cancelada:
            linhas.append(f"- Publicação cancelada pelo tribunal: {p.motivo_cancelamento}")
        if p.certidao:
            linhas.append(f"- [Certidão oficial no DJEN]({p.certidao})")
        if p.link:
            linhas.append(f"- [Documento no tribunal]({p.link})")
        if p.trecho:
            linhas += ["", "> " + p.trecho.replace("\n", "\n> ")]
        linhas.append("")
    return "\n".join(linhas)


__all__ = ["AVISO", "RelatorioRadar", "executar_radar", "gerar_markdown", "hoje_brasilia"]
