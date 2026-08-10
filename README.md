# Financeiros

Robô pessoal de análise de mercado cripto, gestão de risco, memória de decisões e **paper trading**.

> Fase atual: paper trading com estado persistente + loop agendado. Sem ordens reais.

## Ideia

Construir por partes:

1. **Dados** — candles/preço via API pública Binance (`data-api.binance.vision`)
2. **Análise** — sinal (SMA cross) + filtros de risco
3. **Saídas** — stop-loss / take-profit automáticos (prioridade sobre entrada)
4. **Capital** — position sizing, portfólio persistido e **fundo reserva**
5. **Memória** — SQLite com decisões, fills, lições e ciclos
6. **Execução** — paper broker (live fica para depois)
7. **Runtime** — `run-once` ou `run-loop` com logs
8. **Backtest** — replay offline no histórico com métricas

### Saídas automáticas

Com posição aberta, o ciclo checa primeiro:

- **Stop-loss** — se o preço (low/close) cair `stop_loss_pct` abaixo da entrada
- **Take-profit** — se o preço (high/close) subir `take_profit_pct` acima da entrada

Se stop e TP caem no mesmo candle, prevalece o **stop** (cenário conservador).  
Stop-loss também grava uma lição automática na memória.

### Fundo reserva

Em cada **venda com lucro**, uma fatia do PnL realizado (`reserve_skim_pct`, default 20%) sai do caixa de trading e vai para a reserva.  
A reserva **não entra** no sizing/risco de novas compras — fica protegida.

## Estrutura

```text
config/default.yaml          # universo, risco, capital, intervalo
src/financeiros/
  data/                      # ingestão de mercado
  analysis/                  # sinais + risco
  capital/                   # portfólio e sizing
  memory/                    # SQLite (estado + aprendizado)
  execution/                 # paper trading
  pipeline.py                # orquestra um ciclo
  cli.py                     # interface de linha de comando
tests/
data/memory/                 # banco local (gitignored)
data/logs/                   # logs do robô (gitignored)
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

Rodar um ciclo (estado é salvo no SQLite):

```bash
financeiros run-once
```

Rodar em loop (default: a cada 1h; Ctrl+C para parar):

```bash
financeiros run-loop
financeiros run-loop --interval 60 --max-cycles 3
```

Ver portfólio / reserva / último ciclo:

```bash
financeiros status
financeiros reserve
financeiros cycles --limit 10
```

Memória e lições:

```bash
financeiros memory --limit 10
financeiros lesson "Não aumentar posição após 3 stops seguidos" --symbol BTCUSDT
```

Resetar caixa/posições (mantém histórico de decisões):

```bash
financeiros reset-portfolio --yes
financeiros reset-portfolio --yes --keep-reserve   # preserva o fundo reserva
```

Ajuste em `config/default.yaml`:

```yaml
exits:
  enabled: true
  stop_loss_pct: 0.03
  take_profit_pct: 0.06

capital:
  reserve_enabled: true
  reserve_skim_pct: 0.20
```

## Backtest

Replay da mesma lógica (sinal, risco, stop/TP, reserva) no histórico da Binance:

```bash
financeiros backtest --days 60
financeiros backtest --days 90 --symbols BTCUSDT --trades
financeiros backtest --days 30 --save data/logs/backtest-last.json
```

Métricas principais: retorno total, max drawdown, win rate, profit factor, stops/TPs, reserva acumulada.

## Testes

```bash
pytest -q
```

## Próximos passos sugeridos

- Trailing stop
- Mais features de mercado (funding, open interest)
- Live trading só depois de métricas estáveis em paper + backtest
