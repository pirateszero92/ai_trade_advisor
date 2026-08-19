# AI Trade Advisor

An intelligent trading assistant powered by Smart Money Concepts (SMC) analysis
and a multi-provider AI fallback chain (Local LLM → Gemini → OpenRouter).

## Architecture

```
ai_trade_advisor/
├── backend/                    # Python FastAPI backend
│   ├── app/
│   │   ├── main.py             # FastAPI app factory
│   │   ├── api/                # REST + WebSocket routers
│   │   │   ├── signals.py      # Signal analysis endpoint
│   │   │   ├── chart.py        # OHLCV + SMC overlay
│   │   │   ├── trades.py       # Trade management
│   │   │   ├── settings_api.py # LLM & prompt management
│   │   │   ├── journal_api.py  # Trade journal
│   │   │   └── ws.py           # Real-time WebSocket
│   │   ├── core/
│   │   │   ├── config.py       # Pydantic settings
│   │   │   └── security.py     # API key auth
│   │   └── engines/
│   │       ├── smc_engine.py   # SMC analysis (OB/FVG/Sweep/BOS)
│   │       ├── market_data.py  # Multi-source OHLCV fetcher
│   │       ├── ai_engine.py    # LLM fallback chain
│   │       ├── risk_engine.py  # Position sizing & risk checks
│   │       ├── strategy_engine.py  # Rule-based strategy filter
│   │       └── execution_engine.py # Paper/live order execution
│   ├── config/
│   │   ├── providers.yaml      # LLM provider settings
│   │   └── strategy.yaml       # Strategy rules
│   ├── prompts/
│   │   ├── advisor_v1.md       # Active system prompt
│   │   └── active_prompt.txt   # Points to active prompt
│   ├── tests/                  # pytest test suite
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── requirements.txt
│   └── .env.example
└── mobile/                     # Flutter mobile app (Phase 2)
```

## Quick Start

### 1. Setup environment

```bash
cd backend
cp .env.example .env
# Edit .env with your API keys
```

### 2. Run with Docker Compose

```bash
cd backend
docker compose up -d
```

### 3. Run locally (development)

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 4. API Docs

Open [http://localhost:8000/docs](http://localhost:8000/docs) for interactive Swagger UI.

## Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/signals/analyse` | Full SMC + AI analysis |
| GET  | `/api/v1/signals/quick` | Fast SMC-only signal |
| GET  | `/api/v1/chart/ohlcv` | OHLCV candle data |
| GET  | `/api/v1/chart/overlay` | SMC overlay data |
| POST | `/api/v1/trades/place` | Place paper/live order |
| GET  | `/api/v1/trades/` | List all trades |
| POST | `/api/v1/journal/entries` | Create journal entry |
| GET  | `/api/v1/journal/stats` | Performance statistics |
| GET  | `/api/v1/settings/llm/providers` | LLM provider status |
| GET  | `/api/v1/settings/llm/test/{provider}` | Test LLM connectivity |
| WS   | `/ws/signals` | Real-time signal stream |
| WS   | `/ws/chat` | Real-time AI advisor chat |

## SMC Concepts Detected

- **BOS** — Break of Structure
- **CHoCH** — Change of Character
- **Order Blocks** — Last opposing candle before impulse move
- **FVG** — Fair Value Gap (price imbalance)
- **Equal Highs/Lows** — Buy/sell-side liquidity pools
- **Liquidity Sweeps** — Stop hunts above highs or below lows
- **Premium/Discount Zones** — Relative to swing range (Fibonacci 0.382/0.618)

## LLM Fallback Chain

1. **Local** — LM Studio or any OpenAI-compatible local model
2. **Gemini** — Google Gemini 2.0 Flash
3. **OpenRouter** — Claude, GPT-4o, Mixtral, etc.

## Market Support

| Market | Provider |
|--------|----------|
| Crypto | ccxt (Binance, Bybit, 100+ exchanges) |
| Forex/Gold | MetaTrader 5 |
| Stocks | yfinance / Alpaca |

## License

MIT
