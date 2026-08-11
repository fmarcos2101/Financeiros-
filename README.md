# Financeiros

Robô pessoal de análise de mercado cripto, gestão de risco, memória de decisões e **paper trading**.

> Fase atual: paper trading com estado persistente + loop agendado. Sem ordens reais.

## Ideia

Construir por partes:

1. **Dados** — candles/preço via API pública Binance (`data-api.binance.vision`)
2. **Análise** — SMA cross + filtros de entrada (tendência/RSI/volume configuráveis) + risco
3. **Saídas** — stop-loss / trailing / take-profit (prioridade sobre entrada)
4. **Capital** — position sizing, portfólio persistido e **fundo reserva**
5. **Memória** — SQLite com decisões, fills, lições e ciclos
6. **Execução** — paper broker (live fica para depois)
7. **Runtime** — `run-once` ou `run-loop` com logs
8. **Backtest** — replay offline no histórico com métricas
9. **Circuit breaker** — pausa novas compras em perda diária/semanal excessiva
10. **Relatório diário** — resumo + alertas (também no `run-loop`)

### Saídas automáticas

Com posição aberta, o ciclo checa primeiro:

- **Stop-loss** — se o preço cair `stop_loss_pct` abaixo da entrada
- **Trailing stop** — após `trailing_activation_pct` de lucro, o stop sobe com o pico
- **Take-profit** — se o preço subir `take_profit_pct` acima da entrada

Se stop e TP caem no mesmo candle, prevalece o **stop** (cenário conservador).

### Relatório diário

```bash
financeiros report
financeiros report --date 2026-08-10 --text
```

Gera JSON/TXT em `data/logs/reports/` com PnL do dia, trades, circuit breaker e alertas  
(perda próxima do limite, muitos stops, robô haltado, etc.).  
No `run-loop`, o relatório do dia anterior é emitido automaticamente na virada UTC.

### Circuit breaker

Se a wealth cair mais que o limite do **dia** (default 3%) ou da **semana** (default 7%):

- novas **compras** são bloqueadas
- **saídas** (stop/trailing/TP) continuam ativas
- halt diário pode liberar sozinho no próximo dia UTC (`auto_resume_next_day`)

```bash
financeiros status
financeiros halt --yes --reason "pausa manual"
financeiros resume --yes
```

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
  stop_loss_pct: 0.04
  take_profit_pct: 0.06
  trailing_enabled: true
  trailing_pct: 0.03
  trailing_activation_pct: 0.025

capital:
  reserve_enabled: true
  reserve_skim_pct: 0.20
```

## Backtest e tune

```bash
financeiros backtest --days 60
financeiros backtest --days 90 --symbols BTCUSDT --trades
financeiros tune --days 60 --save data/logs/tune-last.json
```

O `tune` testa combinações de stop/TP/trailing no mesmo histórico e ranqueia as melhores.

## Testes

```bash
pytest -q
```

## Próximos passos sugeridos

- Backtest out-of-sample
- Live trading só depois de métricas estáveis em paper + backtest/tune
