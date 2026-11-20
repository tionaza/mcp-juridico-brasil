"""Radar de publicações e prazos: monitoramento diário do DJEN por OAB."""

from mcp_juridico_brasil.radar.cadastro import (
    Advogado,
    adicionar_advogado,
    definir_ativo,
    listar_advogados,
)
from mcp_juridico_brasil.radar.executor import RelatorioRadar, executar_radar, gerar_markdown

__all__ = [
    "Advogado",
    "RelatorioRadar",
    "adicionar_advogado",
    "definir_ativo",
    "executar_radar",
    "gerar_markdown",
    "listar_advogados",
]
