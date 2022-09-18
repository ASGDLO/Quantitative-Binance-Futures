# Freqtrade basics

This page provides you some basic concepts on how Freqtrade works and operates.

## Freqtrade terminology

* **Strategy**: Your trading strategy, telling the bot what to do.
* **Trade**: Open position.
* **Open Order**: Order which is currently placed on the exchange, and is not yet complete.
* **Pair**: Tradable pair, usually in the format of Base/Quote (e.g. XRP/USDT).
* **Timeframe**: Candle length to use (e.g. `"5m"`, `"1h"`, ...).
* **Indicators**: Technical indicators (SMA, EMA, RSI, ...).
* **Limit order**: Limit orders which execute at the defined limit price or better.
* **Market order**: Guaranteed to fill, may move price depending on the order size.

## Fee handling

All profit calculations of Freqtrade include fees. For Backtesting / Hyperopt / Dry-run modes, the exchange default fee is used (lowest tier on the exchange). For live operations, fees are used as applied by the exchange (this includes BNB rebates etc.).

## Bot execution logic

Starting freqtrade in dry-run or live mode (using `freqtrade trade`) will start the bot and start the bot iteration loop.
By default, loop runs every few seconds (`internals.process_throttle_secs`) and does roughly the following in the following sequence:

* Fetch open trades from persistence.
* Calculate current list of tradable pairs.
* Download OHLCV data for the pairlist including all [informative pairs](strategy-customization.md#get-data-for-non-tradeable-pairs)  
  This step is only executed once per Candle to avoid unnecessary network traffic.
* Call `bot_loop_start()` strategy callback.
* Analyze strategy per pair.
  * Call `populate_indicators()`
  * Call `populate_buy_trend()`
  * Call `populate_sell_trend()`
* Check timeouts for open orders.
  * Calls `check_buy_timeout()` strategy callback for open buy orders.
  * Calls `check_sell_timeout()` strategy callback for open sell orders.
* Verifies existing positions and eventually places sell orders.
  * Considers stoploss, ROI and sell-signal, `custom_sell()` and `custom_stoploss()`.
  * Determine sell-price based on `ask_strategy` configuration setting or by using the `custom_exit_price()` callback.
  * Before a sell order is placed, `confirm_trade_exit()` strategy callback is called.
* Check position adjustments for open trades if enabled by calling `adjust_trade_position()` and place additional order if required.
* Check if trade-slots are still available (if `max_open_trades` is reached).
* Verifies buy signal trying to enter new positions.
  * Determine buy-price based on `bid_strategy` configuration setting, or by using the `custom_entry_price()` callback.
  * Determine stake size by calling the `custom_stake_amount()` callback.
  * Before a buy order is placed, `confirm_trade_entry()` strategy callback is called.

This loop will be repeated again and again until the bot is stopped.

## Backtesting / Hyperopt execution logic

[backtesting](backtesting.md) or [hyperopt](hyperopt.md) do only part of the above logic, since most of the trading operations are fully simulated.

* Load historic data for configured pairlist.
* Calls `bot_loop_start()` once.
* Calculate indicators (calls `populate_indicators()` once per pair).
* Calculate buy / sell signals (calls `populate_buy_trend()` and `populate_sell_trend()` once per pair).
* Loops per candle simulating entry and exit points.
  * Confirm trade buy / sell (calls `confirm_trade_entry()` and `confirm_trade_exit()` if implemented in the strategy).
  * Call `custom_entry_price()` (if implemented in the strategy) to determine entry price (Prices are moved to be within the opening candle).
  * Determine stake size by calling the `custom_stake_amount()` callback.
  * Check position adjustments for open trades if enabled and call `adjust_trade_position()` to determine if an additional order is requested.
  * Call `custom_stoploss()` and `custom_sell()` to find custom exit points.
  * For sells based on sell-signal and custom-sell: Call `custom_exit_price()` to determine exit price (Prices are moved to be within the closing candle).
  * Check for Order timeouts, either via the `unfilledtimeout` configuration, or via `check_buy_timeout()` / `check_sell_timeout()` strategy callbacks.
* Generate backtest report output

!!! Note
    Both Backtesting and Hyperopt include exchange default Fees in the calculation. Custom fees can be passed to backtesting / hyperopt by specifying the `--fee` argument.
