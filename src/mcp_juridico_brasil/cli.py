"""CLI do MCP Juridico Brasil."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="mcp-juridico",
    help="MCP Juridico Brasil - acompanhamento de processos via DataJud CNJ.",
    add_completion=False,
)


@app.command()
def serve() -> None:
    """Inicia o servidor MCP em modo stdio."""
    from mcp_juridico_brasil.server import main

    main()


@app.command()
def version() -> None:
    """Exibe a versao instalada."""
    from mcp_juridico_brasil import __version__

    typer.echo(f"mcp-juridico-brasil {__version__}")


@app.command()
def tribunais() -> None:
    """Lista os tribunais suportados."""
    from mcp_juridico_brasil.datajud.tribunais import listar_tribunais

    for t in listar_tribunais():
        typer.echo(t)


radar_app = typer.Typer(help="Radar de publicações e prazos no DJEN.", no_args_is_help=True)
advogado_app = typer.Typer(help="Cadastro de advogados monitorados.", no_args_is_help=True)
radar_app.add_typer(advogado_app, name="advogado")
app.add_typer(radar_app, name="radar")


@advogado_app.command("adicionar")
def advogado_adicionar(
    nome: str = typer.Option(..., help="Nome completo do advogado."),
    oab: str = typer.Option(..., help="Número da OAB."),
    uf: str = typer.Option(..., help="UF da inscrição, ex.: PA."),
    papel: str = typer.Option("associado", help="titular ou associado."),
    email: str = typer.Option("", help="E-mail para o resumo (opcional)."),
) -> None:
    """Cadastra ou reativa um advogado no Radar."""
    from mcp_juridico_brasil._core import JuridicoValidationError
    from mcp_juridico_brasil.radar.cadastro import adicionar_advogado

    try:
        advogado = adicionar_advogado(nome, oab, uf, papel, email)
    except JuridicoValidationError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Cadastrado: {advogado.rotulo}, {advogado.papel}")


@advogado_app.command("listar")
def advogado_listar(
    todos: bool = typer.Option(False, "--todos", help="Inclui advogados desativados."),
) -> None:
    """Lista os advogados monitorados."""
    from mcp_juridico_brasil.radar.cadastro import listar_advogados

    advogados = listar_advogados(incluir_inativos=todos)
    if not advogados:
        typer.echo("Nenhum advogado cadastrado.")
    for a in advogados:
        estado = "" if a.ativo else " [desativado]"
        typer.echo(f"{a.rotulo}, {a.papel}{estado}")


@advogado_app.command("desativar")
def advogado_desativar(
    oab: str = typer.Option(..., help="Número da OAB."),
    uf: str = typer.Option(..., help="UF da inscrição."),
) -> None:
    """Para de monitorar um advogado, mantendo o histórico."""
    from mcp_juridico_brasil._core import JuridicoValidationError
    from mcp_juridico_brasil.radar.cadastro import definir_ativo

    try:
        advogado = definir_ativo(oab, uf, ativo=False)
    except JuridicoValidationError as exc:
        typer.echo(exc.message, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Desativado: {advogado.rotulo}")


@radar_app.command("executar")
def radar_executar(
    data: str = typer.Option("", help="Último dia da janela (AAAA-MM-DD). Padrão: hoje."),
    dias: int = typer.Option(2, help="Dias consultados até a data (padrão: ontem e hoje)."),
) -> None:
    """Busca as publicações no DJEN, estima os prazos e grava o relatório."""
    import asyncio
    import datetime

    from mcp_juridico_brasil.radar.cadastro import listar_advogados
    from mcp_juridico_brasil.radar.executor import executar_radar, gerar_markdown

    if not listar_advogados():
        typer.echo(
            "Nenhum advogado cadastrado. Use: mcp-juridico radar advogado adicionar", err=True
        )
        raise typer.Exit(1)
    referencia = datetime.date.fromisoformat(data) if data else None
    relatorio = asyncio.run(executar_radar(referencia, dias))
    typer.echo(gerar_markdown(relatorio))
    for arquivo in relatorio.arquivos:
        typer.echo(f"Gravado em {arquivo}", err=True)
    if relatorio.falhas:
        raise typer.Exit(2)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
