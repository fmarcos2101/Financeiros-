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
7. **Runtime** — `run-once` ou `run-loop` com logs + heartbeat
8. **Backtest** — replay offline no histórico com métricas
9. **Circuit breaker** — pausa novas compras em perda diária/semanal excessiva
10. **Relatório diário** — resumo + alertas (também no `run-loop`)
11. **Validação OOS** — holdout simples ou walk-forward
12. **Health** — `financeiros health` checa se o loop ainda está vivo
13. **Dashboard** — monitor local (wealth, posições, ciclos, decisões)

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

## Backtest, tune e validação OOS

```bash
financeiros backtest --days 60
financeiros tune --days 60 --save data/logs/tune-last.json
financeiros validate --train-days 60 --holdout-days 30
financeiros validate --train-days 60 --holdout-days 30 --no-tune
financeiros validate --walk-forward --folds 3 --train-days 60 --holdout-days 30 --no-tune
```

O `validate` retuna (opcional) só no período de **treino** e mede o resultado no **holdout**  
nunca visto — com veredicto PASS/FAIL para reduzir overfitting.  
Com `--walk-forward`, roda vários splits deslocados e exige maioria dos folds PASS.

## Testes

```bash
pytest -q
```

## Paper em loop

```bash
# direto
financeiros run-loop --interval 3600

# ou via script
./scripts/start-paper-loop.sh
```

Acompanhe com:

```bash
financeiros status
financeiros health
financeiros report --text
financeiros memory --limit 20

# dashboard local (somente leitura)
financeiros dashboard
# http://127.0.0.1:8787
./scripts/start-dashboard.sh
```

O `run-loop` / `run-once` gravam `data/logs/heartbeat.json`.  
`financeiros health` falha (exit 1) se o heartbeat estiver ausente ou mais antigo que  
`runtime.heartbeat_stale_seconds` (default 2h).

## Binance / live trading

**Hoje o robô não opera na sua conta Binance.**  
Ele só lê preços públicos e simula fills em paper no SQLite local.

As variáveis `BINANCE_API_KEY` / `BINANCE_API_SECRET` no `.env` estão reservadas  
para uma fase futura. Live exigiria:

1. paper estável + validação OOS/walk-forward consistente  
2. um `LiveBroker` com assinatura de ordens  
3. API key com permissão de **trade** (sem withdraw) e limites bem baixos no início

## Próximos passos sugeridos

- Acompanhar paper por alguns dias (`dashboard` / `status` / `health` / `report`)
- Live trading só depois de walk-forward OOS PASS estável + paper consistente
