"""Testes do Radar de publicações e prazos (DJEN). Sem chamadas reais à API."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mcp_juridico_brasil._core import JuridicoAPIError, JuridicoValidationError
from mcp_juridico_brasil.radar import cadastro
from mcp_juridico_brasil.radar.djen import DJEN_BASE_URL, DjenClient, ResultadoBusca, variantes_oab
from mcp_juridico_brasil.radar.executor import executar_radar, gerar_markdown
from mcp_juridico_brasil.radar.publicacao import (
    dias_do_prazo,
    eh_materia_penal,
    estimar_prazo,
    html_para_texto,
    uf_para_feriados,
)
from mcp_juridico_brasil.radar.tools import radar_cadastrar_advogado, radar_listar_advogados

URL = f"{DJEN_BASE_URL}/api/v1/comunicacao"


@pytest.fixture(autouse=True)
def radar_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(cadastro.RADAR_DIR_ENV, str(tmp_path))
    return tmp_path


async def _sem_pausa(_: float) -> None:
    return None


def _item(id_: int, oab: str = "40783", uf: str = "PA", **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": id_,
        "hash": f"h{id_}",
        "numero_processo": "08000012320268140301",
        "numeroprocessocommascara": "0800001-23.2026.8.14.0301",
        "siglaTribunal": "TJPA",
        "tipoComunicacao": "Intimação",
        "tipoDocumento": "Despacho",
        "nomeOrgao": "1ª Vara Cível de Belém",
        "nomeClasse": "Procedimento Comum Cível",
        "data_disponibilizacao": "2026-03-05",
        "texto": "<p>Intime-se a parte autora para, no prazo de 5 (cinco) dias, emendar.</p>",
        "link": "https://pje.tjpa.jus.br/doc",
        "motivo_cancelamento": None,
        "destinatarioadvogados": [
            {"advogado": {"nome": "X", "numero_oab": f"{oab}-O", "uf_oab": uf}}
        ],
    }
    base.update(extra)
    return base


class TestCadastro:
    def test_adiciona_titular_e_associado(self, radar_dir: Path) -> None:
        cadastro.adicionar_advogado(
            "Renan Marcelo dos Santos Nazaré", "40.783", "pa", papel="titular"
        )
        cadastro.adicionar_advogado("Associada Exemplo", "12345", "PA")
        advogados = cadastro.listar_advogados()
        assert [(a.oab, a.uf, a.papel) for a in advogados] == [
            ("40783", "PA", "titular"),
            ("12345", "PA", "associado"),
        ]
        assert json.loads((radar_dir / "advogados.json").read_text(encoding="utf-8"))

    def test_mesma_oab_atualiza_em_vez_de_duplicar(self) -> None:
        cadastro.adicionar_advogado("Nome Antigo", "40783", "PA")
        cadastro.adicionar_advogado("Nome Novo", "040783", "PA", papel="titular")
        advogados = cadastro.listar_advogados()
        assert len(advogados) == 1
        assert advogados[0].nome == "Nome Novo"
        assert advogados[0].papel == "titular"

    def test_desativar_e_reativar(self) -> None:
        cadastro.adicionar_advogado("Associado", "111", "PA")
        cadastro.definir_ativo("111", "PA", ativo=False)
        assert cadastro.listar_advogados() == []
        assert len(cadastro.listar_advogados(incluir_inativos=True)) == 1
        cadastro.adicionar_advogado("Associado", "111", "PA")
        assert len(cadastro.listar_advogados()) == 1

    @pytest.mark.parametrize(
        ("nome", "oab", "uf", "papel"),
        [
            ("", "1", "PA", "associado"),
            ("A", "", "PA", "associado"),
            ("A", "1", "XX", "associado"),
            ("A", "1", "PA", "estagiario"),
        ],
    )
    def test_validacao(self, nome: str, oab: str, uf: str, papel: str) -> None:
        with pytest.raises(JuridicoValidationError):
            cadastro.adicionar_advogado(nome, oab, uf, papel)

    def test_desativar_inexistente(self) -> None:
        with pytest.raises(JuridicoValidationError):
            cadastro.definir_ativo("999", "PA", ativo=False)

    async def test_tools(self) -> None:
        resposta = await radar_cadastrar_advogado("Associado", "222", "PA")
        assert resposta["cadastrado"] is True
        assert (await radar_listar_advogados())["total"] == 1
        assert "erro" in await radar_cadastrar_advogado("Associado", "222", "ZZ")


class TestDjenClient:
    def test_variantes(self) -> None:
        assert variantes_oab("40783") == [
            "40783",
            "40783-O",
            "40783-A",
            "40783-N",
            "40783-B",
            "40783-S",
            "40783-E",
        ]

    @respx.mock
    async def test_consulta_todas_variantes_com_50_itens_e_deduplica(self) -> None:
        rota = respx.get(URL).mock(
            return_value=httpx.Response(200, json={"count": 1, "items": [_item(1)]})
        )
        cliente = DjenClient(dormir=_sem_pausa)
        resultado = await cliente.buscar_por_oab(
            "40783", "PA", datetime.date(2026, 3, 4), datetime.date(2026, 3, 5)
        )
        assert rota.call_count == 7
        assert [i["id"] for i in resultado.itens] == [1]
        params = rota.calls[1].request.url.params
        assert params["numeroOab"] == "40783-O"
        assert params["itensPorPagina"] == "50"
        assert params["ufOab"] == "PA"
        assert params["dataDisponibilizacaoInicio"] == "2026-03-04"

    @respx.mock
    async def test_repete_pagina_vazia_transitoria(self) -> None:
        respostas = iter(
            [
                httpx.Response(200, json={"count": 2, "items": [_item(1)]}),
                httpx.Response(200, json={"count": 2, "items": []}),
                httpx.Response(200, json={"count": 2, "items": [_item(2)]}),
            ]
        )
        respx.get(URL, params={"numeroOab": "40783"}).mock(side_effect=lambda _: next(respostas))
        respx.get(URL).mock(return_value=httpx.Response(200, json={"count": 0, "items": []}))
        resultado = await DjenClient(dormir=_sem_pausa).buscar_por_oab(
            "40783", "PA", datetime.date(2026, 3, 5), datetime.date(2026, 3, 5)
        )
        assert sorted(i["id"] for i in resultado.itens) == [1, 2]
        assert resultado.incompleta == []

    @respx.mock
    async def test_marca_incompleta_quando_paginas_vazias_persistem(self) -> None:
        respx.get(URL).mock(return_value=httpx.Response(200, json={"count": 3, "items": []}))
        resultado = await DjenClient(dormir=_sem_pausa).buscar_por_oab(
            "1", "PA", datetime.date(2026, 3, 5), datetime.date(2026, 3, 5)
        )
        assert len(resultado.incompleta) == 7

    @respx.mock
    async def test_403_explica_geobloqueio(self) -> None:
        respx.get(URL).mock(return_value=httpx.Response(403))
        with pytest.raises(JuridicoAPIError, match="fora do Brasil"):
            await DjenClient(dormir=_sem_pausa).buscar_por_oab(
                "1", "PA", datetime.date(2026, 3, 5), datetime.date(2026, 3, 5)
            )

    @respx.mock
    async def test_retenta_erro_500(self) -> None:
        respostas = iter([httpx.Response(500), httpx.Response(200, json={"count": 0, "items": []})])
        respx.get(URL, params={"numeroOab": "1"}).mock(side_effect=lambda _: next(respostas))
        respx.get(URL).mock(return_value=httpx.Response(200, json={"count": 0, "items": []}))
        resultado = await DjenClient(dormir=_sem_pausa).buscar_por_oab(
            "1", "PA", datetime.date(2026, 3, 5), datetime.date(2026, 3, 5)
        )
        assert resultado.itens == []


class TestPrazo:
    @pytest.mark.parametrize(
        ("texto", "dias"),
        [
            ("Intime-se para, no prazo de 5 (cinco) dias, emendar a inicial.", 5),
            ("Manifeste-se a parte no prazo legal de 15 dias.", 15),
            ("Prazo: 10 dias.", 10),
            ("Ciência às partes da sentença.", 15),
            ("Vista às partes para, no prazo comum de 48 (quarenta e oito) horas, falar.", 2),
            ("No prazo de 5 dias. Após, prazo de 24 horas.", 5),
            ("Processo sigiloso\nPara visualização do documento consulte os autos digitais", 15),
        ],
    )
    def test_dias_do_prazo(self, texto: str, dias: int) -> None:
        assert dias_do_prazo(texto)[0] == dias

    def test_civel_conta_dias_uteis_apos_publicacao(self) -> None:
        estimativa = estimar_prazo(
            datetime.date(2026, 3, 5), "no prazo de 5 (cinco) dias", "Procedimento Comum", "PA"
        )
        assert estimativa.data_publicacao == datetime.date(2026, 3, 6)
        assert estimativa.vencimento == datetime.date(2026, 3, 13)
        assert "úteis" in estimativa.contagem

    def test_disponibilizacao_na_sexta_publica_na_segunda(self) -> None:
        estimativa = estimar_prazo(datetime.date(2026, 3, 6), "prazo de 5 dias", "", "PA")
        assert estimativa.data_publicacao == datetime.date(2026, 3, 9)
        assert estimativa.vencimento == datetime.date(2026, 3, 16)

    def test_penal_conta_dias_corridos(self) -> None:
        estimativa = estimar_prazo(
            datetime.date(2026, 3, 5),
            "no prazo de 5 dias",
            "Ação Penal - Procedimento Ordinário",
            "PA",
        )
        assert estimativa.vencimento == datetime.date(2026, 3, 11)
        assert "corridos" in estimativa.contagem

    def test_penal_que_vence_no_sabado_prorroga(self) -> None:
        estimativa = estimar_prazo(
            datetime.date(2026, 3, 5), "prazo de 8 dias", "Habeas Corpus", "PA"
        )
        assert estimativa.vencimento == datetime.date(2026, 3, 16)

    @pytest.mark.parametrize(
        ("classe", "orgao", "penal"),
        [
            ("AUTO DE PRISãO EM FLAGRANTE", "10ª Vara Criminal de Belém", True),
            ("PROCEDIMENTO ESPECIAL DA LEI ANTITóXICOS", "Vara Única de Tomé Açu", True),
            ("CRIMES DE CALúNIA, INJúRIA E DIFAMAçãO DE COMPETêNCIA DO JUIZ SINGULAR", "", True),
            ("AçãO PENAL DE COMPETêNCIA DO JúRI", "", True),
            ("HABEAS CORPUS", "", True),
            ("AçãO PENAL - PROCEDIMENTO SUMARíSSIMO", "", True),
            ("EMBARGOS DE TERCEIRO CíVEL", "2ª Vara do Juizado Especial Cível de Belém", False),
            ("CUMPRIMENTO DE SENTENçA", "2ª Vara de Família de Ananindeua", False),
            ("APELAçãO CíVEL", "8ª Câmara de Direito Civil", False),
        ],
    )
    def test_classes_reais_do_djen(self, classe: str, orgao: str, penal: bool) -> None:
        assert eh_materia_penal(classe, orgao) is penal

    def test_uf_dos_feriados(self) -> None:
        assert uf_para_feriados("TJSP", "PA") == "SP"
        assert uf_para_feriados("TRT8", "PA") == "PA"


def test_html_hostil_vira_texto() -> None:
    html = '<p class="fixed inset-0">Vistos.</p><script>alert(1)</script><p>Intime-se.</p>'
    assert html_para_texto(html) == "Vistos.\nIntime-se."


class _ClienteFalso(DjenClient):
    def __init__(self, por_oab: dict[str, list[dict[str, Any]] | Exception]) -> None:
        super().__init__(dormir=_sem_pausa)
        self.por_oab = por_oab

    async def buscar_por_oab(
        self, oab: str, uf: str, inicio: datetime.date, fim: datetime.date
    ) -> ResultadoBusca:
        valor = self.por_oab.get(oab, [])
        if isinstance(valor, Exception):
            raise valor
        return ResultadoBusca(itens=list(valor))


class TestExecutor:
    async def test_relatorio_novas_e_ja_vistas(self, radar_dir: Path) -> None:
        cadastro.adicionar_advogado("Renan", "40783", "PA", papel="titular")
        cadastro.adicionar_advogado("Associada", "12345", "PA")
        compartilhada = _item(10)
        compartilhada["destinatarioadvogados"].append(
            {"advogado": {"nome": "Y", "numero_oab": "12345", "uf_oab": "PA"}}
        )
        cliente = _ClienteFalso({"40783": [compartilhada, _item(11)], "12345": [compartilhada]})
        referencia = datetime.date(2026, 3, 5)

        primeiro = await executar_radar(referencia, cliente=cliente)
        assert len(primeiro.publicacoes) == 2
        assert len(primeiro.novas) == 2
        p10 = next(p for p in primeiro.publicacoes if p.id == 10)
        assert len(p10.advogados) == 2
        assert (radar_dir / "relatorios" / "2026-03-05.md").exists()
        assert (radar_dir / "relatorios" / "2026-03-05.json").exists()
        texto = gerar_markdown(primeiro)
        assert "13/03/2026" in texto
        assert "| NOVA |" in texto
        assert f"{DJEN_BASE_URL}/api/v1/comunicacao/h10/certidao" in texto

        segundo = await executar_radar(referencia, cliente=cliente)
        assert segundo.novas == []

    async def test_cancelamento_reaparece_como_novo_alerta(self) -> None:
        cadastro.adicionar_advogado("Renan", "40783", "PA")
        referencia = datetime.date(2026, 3, 5)
        await executar_radar(referencia, cliente=_ClienteFalso({"40783": [_item(20)]}))
        cancelada = _item(20, motivo_cancelamento="Publicada por engano")
        relatorio = await executar_radar(referencia, cliente=_ClienteFalso({"40783": [cancelada]}))
        assert relatorio.novas[0].cancelada
        assert relatorio.novas[0].prazo is None
        assert "CANCELADA" in gerar_markdown(relatorio)

    async def test_falha_de_um_advogado_nao_derruba_os_outros(self) -> None:
        cadastro.adicionar_advogado("Renan", "40783", "PA")
        cadastro.adicionar_advogado("Associada", "12345", "PA")
        cliente = _ClienteFalso(
            {"40783": JuridicoAPIError("DJEN", status_code=403), "12345": [_item(30, oab="12345")]}
        )
        relatorio = await executar_radar(datetime.date(2026, 3, 5), cliente=cliente)
        assert len(relatorio.falhas) == 1
        assert len(relatorio.publicacoes) == 1
        assert "consultas com falha" in gerar_markdown(relatorio)

    async def test_sem_publicacoes(self) -> None:
        cadastro.adicionar_advogado("Renan", "40783", "PA")
        relatorio = await executar_radar(datetime.date(2026, 3, 5), cliente=_ClienteFalso({}))
        assert "Nenhuma publicação no período." in gerar_markdown(relatorio)
