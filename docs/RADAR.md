# Radar de publicações e prazos

O Radar consulta todo dia o DJEN (Comunica PJe) pela OAB de cada advogado do
escritório, junta as intimações, estima o vencimento de cada prazo e grava um
relatório em Markdown e JSON.

> O Radar é apoio à controladoria. Ele não substitui a consulta oficial ao DJEN
> nem a conferência do prazo pelo advogado responsável.

## Requisito: rodar no Brasil

O DJEN recusa conexões de fora do Brasil (HTTP 403). Rode o Radar no
computador do escritório ou num servidor em região brasileira (por exemplo
AWS `sa-east-1` ou GCP `southamerica-east1`).

## Instalação no Windows

1. Instale o [uv](https://docs.astral.sh/uv/) no PowerShell:
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
2. Instale o pacote a partir do repositório do escritório:
   `uv tool install git+https://github.com/tionaza/mcp-juridico-brasil`
3. Feche e abra o PowerShell. O comando `mcp-juridico` passa a existir.

## Cadastro de advogados

Cada advogado é identificado por número da OAB + UF. O papel é `titular` ou
`associado`. Cadastrar a mesma OAB de novo atualiza o nome e reativa o cadastro.

```powershell
mcp-juridico radar advogado adicionar --nome "Renan Marcelo dos Santos Nazaré" --oab 40783 --uf PA --papel titular
mcp-juridico radar advogado adicionar --nome "Nome do Associado" --oab 12345 --uf PA
mcp-juridico radar advogado listar
mcp-juridico radar advogado desativar --oab 12345 --uf PA
```

Desativar para de monitorar sem apagar o histórico.

## Execução

```powershell
mcp-juridico radar executar                  # ontem e hoje (horário de Brasília)
mcp-juridico radar executar --dias 30        # últimos 30 dias, útil na primeira vez
mcp-juridico radar executar --data 2026-11-20 --dias 7
```

O relatório sai na tela e é gravado em `%USERPROFILE%\.mcp-juridico-radar\relatorios\`
(`AAAA-MM-DD.md` e `.json`). Para usar outra pasta, defina a variável `RADAR_DIR`.
Publicações já vistas em execuções anteriores aparecem como "já vista"; uma
publicação cancelada pelo tribunal volta como alerta "CANCELADA".

Para rodar todo dia às 7h, crie uma tarefa no Agendador de Tarefas do Windows:

```powershell
schtasks /Create /SC DAILY /ST 07:00 /TN "Radar DJEN" /TR "cmd /c mcp-juridico radar executar > %USERPROFILE%\.mcp-juridico-radar\ultima-execucao.txt 2>&1"
```

## Pelo Claude (MCP)

Com o servidor MCP configurado, o Claude ganha quatro ferramentas:
`radar_listar_advogados`, `radar_cadastrar_advogado`, `radar_desativar_advogado`
e `radar_executar`. Exemplo de pedido: "rode o Radar dos últimos 7 dias e me diga
o que vence esta semana".

## Como o prazo é estimado

- **Data da publicação:** primeiro dia útil seguinte à disponibilização no DJEN
  (Lei 11.419/2006, art. 4º, §3º).
- **Início da contagem:** primeiro dia útil seguinte à publicação (art. 224 do CPC).
- **Quantidade de dias:** lida do texto quando ele diz "no prazo de N dias" ou
  "N horas"; sem isso, usa 15 dias e avisa no relatório. Em processo sigiloso
  o texto não vem no DJEN, então o relatório pede conferência nos autos.
- **Cível, família, juizados e trabalhista:** dias úteis, com feriados nacionais,
  estaduais da UF do tribunal e recesso de 20/12 a 20/1 (arts. 219 e 220 do CPC).
- **Criminal:** dias corridos (art. 798 do CPP), prorrogando o vencimento que
  cair em dia não útil. A matéria é reconhecida pela classe ou pelo órgão
  (vara criminal, júri, flagrante, habeas corpus, Lei de Drogas etc.).

Não entram no cálculo: feriados municipais, pontos facultativos, suspensões de
expediente, prazos em dobro (arts. 180, 183, 186 e 229 do CPC) e prazos próprios
de cada tribunal.

## O que o Radar faz com a API do DJEN

Segue as regras da skill `djen` (repositório `skills-datajud-djen`): consulta as
sete variantes de sufixo da OAB (`40783`, `40783-O`, `40783-A`…), pagina com 50
itens, repete páginas vazias transitórias, deduplica pelo `id` da comunicação e
converte o HTML da publicação em texto puro antes de mostrar.
