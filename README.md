# Financeiros

Robô pessoal de análise de mercado cripto, gestão de risco, memória de decisões e **paper trading**.

> Fase atual: esqueleto operacional em modo paper. Sem ordens reais.

## Ideia

Construir por partes:

1. **Dados** — candles/preço via API pública Binance (`data-api.binance.vision`)
2. **Análise** — sinal (SMA cross) + filtros de risco
3. **Capital** — position sizing e portfólio em memória
4. **Memória** — SQLite com decisões, fills e lições
5. **Execução** — paper broker (live fica para depois)

## Estrutura

```text
config/default.yaml          # universo, risco, capital
src/financeiros/
  data/                      # ingestão de mercado
  analysis/                  # sinais + risco
  capital/                   # portfólio e sizing
  memory/                    # SQLite (não repetir erros)
  execution/                 # paper trading
  pipeline.py                # orquestra um ciclo
  cli.py                     # interface de linha de comando
tests/
data/memory/                 # banco local (gitignored)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

## Uso

Rodar um ciclo (busca candles reais da Binance, decide, opcionalmente executa paper):

```bash
financeiros run-once
# ou
python -m financeiros.cli run-once
```

Ver decisões recentes:

```bash
financeiros memory --limit 10
```

Registrar uma lição aprendida:

```bash
financeiros lesson "Não aumentar posição após 3 stops seguidos" --symbol BTCUSDT
```

## Testes

```bash
pytest -q
```

## Próximos passos sugeridos

- Persistência do portfólio entre ciclos
- Loop agendado (`run-loop`) com intervalo configurável
- Mais features de mercado (funding, open interest)
- Backtest offline sobre histórico
- Live trading só depois de métricas estáveis em paper
