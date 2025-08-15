# Clone of Project 9 - SMA Optimization Strategy

QuantConnect algorithm implementing adaptive SMA pair optimization.

## Categories

### [strategy/](strategy/)
Strategy implementation details and optimization notes.

### [backtests/](backtests/)
Backtest results and performance analysis.

### [bugs/](bugs/)
Bug reports and fixes specific to this algorithm.

## Algorithm Overview

This algorithm implements:
- Dynamic SMA pair selection
- Adaptive position management
- Risk-adjusted signal generation
- Multi-timeframe analysis

## Key Components

- **SymbolData Class**: Manages per-symbol data and indicators
- **LogCollector**: Efficient logging with daily caps
- **Signal Generation**: SMA crossover detection
- **Position Management**: Entry/exit logic based on signals

## Backtest History

Multiple backtests have been run with timestamps:
- 2025-08-04_21-35-24
- 2025-08-04_22-18-41
- 2025-08-04_22-23-15
- 2025-08-04_22-24-12
- 2025-08-04_22-31-06
- 2025-08-05_00-04-37
- 2025-08-05_17-52-10

## Configuration

Key parameters defined in main.py:
- MAX_SMA_DAY: Maximum SMA period
- DBG: Debug mode flag
- Daily log cap: 300 messages