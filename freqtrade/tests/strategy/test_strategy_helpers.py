import numpy as np
import pandas as pd
import pytest

from freqtrade.data.dataprovider import DataProvider
from freqtrade.enums import CandleType
from freqtrade.resolvers.strategy_resolver import StrategyResolver
from freqtrade.strategy import merge_informative_pair, stoploss_from_absolute, stoploss_from_open
from tests.conftest import generate_test_data, get_patched_exchange


def test_merge_informative_pair():
    data = generate_test_data('15m', 40)
    informative = generate_test_data('1h', 40)

    result = merge_informative_pair(data, informative, '15m', '1h', ffill=True)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == len(data)
    assert 'date' in result.columns
    assert result['date'].equals(data['date'])
    assert 'date_1h' in result.columns

    assert 'open' in result.columns
    assert 'open_1h' in result.columns
    assert result['open'].equals(data['open'])

    assert 'close' in result.columns
    assert 'close_1h' in result.columns
    assert result['close'].equals(data['close'])

    assert 'volume' in result.columns
    assert 'volume_1h' in result.columns
    assert result['volume'].equals(data['volume'])

    # First 3 rows are empty
    assert result.iloc[0]['date_1h'] is pd.NaT
    assert result.iloc[1]['date_1h'] is pd.NaT
    assert result.iloc[2]['date_1h'] is pd.NaT
    # Next 4 rows contain the starting date (0:00)
    assert result.iloc[3]['date_1h'] == result.iloc[0]['date']
    assert result.iloc[4]['date_1h'] == result.iloc[0]['date']
    assert result.iloc[5]['date_1h'] == result.iloc[0]['date']
    assert result.iloc[6]['date_1h'] == result.iloc[0]['date']
    # Next 4 rows contain the next Hourly date original date row 4
    assert result.iloc[7]['date_1h'] == result.iloc[4]['date']
    assert result.iloc[8]['date_1h'] == result.iloc[4]['date']

    informative = generate_test_data('1h', 40)
    result = merge_informative_pair(data, informative, '15m', '1h', ffill=False)
    # First 3 rows are empty
    assert result.iloc[0]['date_1h'] is pd.NaT
    assert result.iloc[1]['date_1h'] is pd.NaT
    assert result.iloc[2]['date_1h'] is pd.NaT
    # Next 4 rows contain the starting date (0:00)
    assert result.iloc[3]['date_1h'] == result.iloc[0]['date']
    assert result.iloc[4]['date_1h'] is pd.NaT
    assert result.iloc[5]['date_1h'] is pd.NaT
    assert result.iloc[6]['date_1h'] is pd.NaT
    # Next 4 rows contain the next Hourly date original date row 4
    assert result.iloc[7]['date_1h'] == result.iloc[4]['date']
    assert result.iloc[8]['date_1h'] is pd.NaT


def test_merge_informative_pair_same():
    data = generate_test_data('15m', 40)
    informative = generate_test_data('15m', 40)

    result = merge_informative_pair(data, informative, '15m', '15m', ffill=True)
    assert isinstance(result, pd.DataFrame)
    assert len(result) == len(data)
    assert 'date' in result.columns
    assert result['date'].equals(data['date'])
    assert 'date_15m' in result.columns

    assert 'open' in result.columns
    assert 'open_15m' in result.columns
    assert result['open'].equals(data['open'])

    assert 'close' in result.columns
    assert 'close_15m' in result.columns
    assert result['close'].equals(data['close'])

    assert 'volume' in result.columns
    assert 'volume_15m' in result.columns
    assert result['volume'].equals(data['volume'])

    # Dates match 1:1
    assert result['date_15m'].equals(result['date'])


def test_merge_informative_pair_lower():
    data = generate_test_data('1h', 40)
    informative = generate_test_data('15m', 40)

    with pytest.raises(ValueError, match=r"Tried to merge a faster timeframe .*"):
        merge_informative_pair(data, informative, '1h', '15m', ffill=True)


def test_merge_informative_pair_empty():
    data = generate_test_data('1h', 40)
    informative = pd.DataFrame(columns=data.columns)

    result = merge_informative_pair(data, informative, '1h', '2h', ffill=True)
    assert result['date'].equals(data['date'])

    assert list(result.columns) == [
        'date',
        'open',
        'high',
        'low',
        'close',
        'volume',
        'date_2h',
        'open_2h',
        'high_2h',
        'low_2h',
        'close_2h',
        'volume_2h'
    ]
    # We merge an empty dataframe, so all values should be NaN
    for col in ['date_2h', 'open_2h', 'high_2h', 'low_2h', 'close_2h', 'volume_2h']:
        assert result[col].isnull().all()


def test_merge_informative_pair_suffix():
    data = generate_test_data('15m', 20)
    informative = generate_test_data('1h', 20)

    result = merge_informative_pair(data, informative, '15m', '1h',
                                    append_timeframe=False, suffix="suf")

    assert 'date' in result.columns
    assert result['date'].equals(data['date'])
    assert 'date_suf' in result.columns

    assert 'open_suf' in result.columns
    assert 'open_1h' not in result.columns

    assert list(result.columns) == [
        'date',
        'open',
        'high',
        'low',
        'close',
        'volume',
        'date_suf',
        'open_suf',
        'high_suf',
        'low_suf',
        'close_suf',
        'volume_suf'
    ]


def test_merge_informative_pair_suffix_append_timeframe():
    data = generate_test_data('15m', 20)
    informative = generate_test_data('1h', 20)

    with pytest.raises(ValueError, match=r"You can not specify `append_timeframe` .*"):
        merge_informative_pair(data, informative, '15m', '1h', suffix="suf")


@pytest.mark.parametrize("side,profitrange", [
    # profit range for long is [-1, inf] while for shorts is [-inf, 1]
    ("long", [-0.99, 2, 30]),
    ("short", [-2.0, 0.99, 30]),
])
def test_stoploss_from_open(side, profitrange):
    open_price_ranges = [
        [0.01, 1.00, 30],
        [1, 100, 30],
        [100, 10000, 30],
    ]

    for open_range in open_price_ranges:
        for open_price in np.linspace(*open_range):
            for desired_stop in np.linspace(-0.50, 0.50, 30):

                if side == 'long':
                    # -1 is not a valid current_profit, should return 1
                    assert stoploss_from_open(desired_stop, -1) == 1
                else:
                    # 1 is not a valid current_profit for shorts, should return 1
                    assert stoploss_from_open(desired_stop, 1, True) == 1

                for current_profit in np.linspace(*profitrange):
                    if side == 'long':
                        current_price = open_price * (1 + current_profit)
                        expected_stop_price = open_price * (1 + desired_stop)
                        stoploss = stoploss_from_open(desired_stop, current_profit)
                        stop_price = current_price * (1 - stoploss)
                    else:
                        current_price = open_price * (1 - current_profit)
                        expected_stop_price = open_price * (1 - desired_stop)
                        stoploss = stoploss_from_open(desired_stop, current_profit, True)
                        stop_price = current_price * (1 + stoploss)

                    assert stoploss >= 0
                    # Technically the formula can yield values greater than 1 for shorts
                    # eventhough it doesn't make sense because the position would be liquidated
                    if side == 'long':
                        assert stoploss <= 1

                    # there is no correct answer if the expected stop price is above
                    # the current price
                    if ((side == 'long' and expected_stop_price > current_price)
                            or (side == 'short' and expected_stop_price < current_price)):
                        assert stoploss == 0
                    else:
                        assert pytest.approx(stop_price) == expected_stop_price


@pytest.mark.parametrize("side,rel_stop,curr_profit,leverage,expected", [
    # profit range for long is [-1, inf] while for shorts is [-inf, 1]
    ("long", 0, -1, 1, 1),
    ("long", 0, 0.1, 1, 0.09090909),
    ("long", -0.1, 0.1, 1, 0.18181818),
    ("long", 0.1, 0.2, 1, 0.08333333),
    ("long", 0.1, 0.5, 1, 0.266666666),
    ("long", 0.1, 5, 1, 0.816666666),  # 500% profit, set stoploss to 10% above open price
    ("long", 0, 5, 10,  3.3333333),  # 500% profit, set stoploss break even
    ("long", 0.1, 5, 10,  3.26666666),  # 500% profit, set stoploss to 10% above open price
    ("long", -0.1, 5, 10,  3.3999999),  # 500% profit, set stoploss to 10% belowopen price

    ("short", 0, 0.1, 1, 0.1111111),
    ("short", -0.1, 0.1, 1, 0.2222222),
    ("short", 0.1, 0.2, 1, 0.125),
    ("short", 0.1, 1, 1, 1),
    ("short", -0.01, 5, 10, 10.01999999),  # 500% profit at 10x
])
def test_stoploss_from_open_leverage(side, rel_stop, curr_profit, leverage, expected):

    stoploss = stoploss_from_open(rel_stop, curr_profit, side == 'short', leverage)
    assert pytest.approx(stoploss) == expected
    open_rate = 100
    if stoploss != 1:
        if side == 'long':
            current_rate = open_rate * (1 + curr_profit / leverage)
            stop = current_rate * (1 - stoploss / leverage)
            assert pytest.approx(stop) == open_rate * (1 + rel_stop / leverage)
        else:
            current_rate = open_rate * (1 - curr_profit / leverage)
            stop = current_rate * (1 + stoploss / leverage)
            assert pytest.approx(stop) == open_rate * (1 - rel_stop / leverage)


def test_stoploss_from_absolute():
    assert pytest.approx(stoploss_from_absolute(90, 100)) == 1 - (90 / 100)
    assert pytest.approx(stoploss_from_absolute(90, 100)) == 0.1
    assert pytest.approx(stoploss_from_absolute(95, 100)) == 0.05
    assert pytest.approx(stoploss_from_absolute(100, 100)) == 0
    assert pytest.approx(stoploss_from_absolute(110, 100)) == 0
    assert pytest.approx(stoploss_from_absolute(100, 0)) == 1
    assert pytest.approx(stoploss_from_absolute(0, 100)) == 1
    assert pytest.approx(stoploss_from_absolute(0, 100, False, leverage=5)) == 5

    assert pytest.approx(stoploss_from_absolute(90, 100, True)) == 0
    assert pytest.approx(stoploss_from_absolute(100, 100, True)) == 0
    assert pytest.approx(stoploss_from_absolute(110, 100, True)) == -(1 - (110 / 100))
    assert pytest.approx(stoploss_from_absolute(110, 100, True)) == 0.1
    assert pytest.approx(stoploss_from_absolute(105, 100, True)) == 0.05
    assert pytest.approx(stoploss_from_absolute(105, 100, True, 5)) == 0.05 * 5
    assert pytest.approx(stoploss_from_absolute(100, 0, True)) == 1
    assert pytest.approx(stoploss_from_absolute(0, 100, True)) == 0
    assert pytest.approx(stoploss_from_absolute(100, 1, is_short=True)) == 1
    assert pytest.approx(stoploss_from_absolute(100, 1, is_short=True, leverage=5)) == 5


@pytest.mark.parametrize('trading_mode', ['futures', 'spot'])
def test_informative_decorator(mocker, default_conf_usdt, trading_mode):
    candle_def = CandleType.get_default(trading_mode)
    default_conf_usdt['candle_type_def'] = candle_def
    test_data_5m = generate_test_data('5m', 40)
    test_data_30m = generate_test_data('30m', 40)
    test_data_1h = generate_test_data('1h', 40)
    data = {
        ('XRP/USDT', '5m', candle_def): test_data_5m,
        ('XRP/USDT', '30m', candle_def): test_data_30m,
        ('XRP/USDT', '1h', candle_def): test_data_1h,
        ('XRP/BTC', '1h', candle_def): test_data_1h,  # from {base}/BTC
        ('LTC/USDT', '5m', candle_def): test_data_5m,
        ('LTC/USDT', '30m', candle_def): test_data_30m,
        ('LTC/USDT', '1h', candle_def): test_data_1h,
        ('LTC/BTC', '1h', candle_def): test_data_1h,  # from {base}/BTC
        ('NEO/USDT', '30m', candle_def): test_data_30m,
        ('NEO/USDT', '5m', CandleType.SPOT): test_data_5m,  # Explicit request with '' as candletype
        ('NEO/USDT', '15m', candle_def): test_data_5m,  # Explicit request with '' as candletype
        ('NEO/USDT', '1h', candle_def): test_data_1h,
        ('ETH/USDT', '1h', candle_def): test_data_1h,
        ('ETH/USDT', '30m', candle_def): test_data_30m,
        ('ETH/BTC', '1h', CandleType.SPOT): test_data_1h,  # Explicitly selected as spot
    }
    default_conf_usdt['strategy'] = 'InformativeDecoratorTest'
    strategy = StrategyResolver.load_strategy(default_conf_usdt)
    exchange = get_patched_exchange(mocker, default_conf_usdt)
    strategy.dp = DataProvider({}, exchange, None)
    mocker.patch.object(strategy.dp, 'current_whitelist', return_value=[
        'XRP/USDT', 'LTC/USDT', 'NEO/USDT'
    ])

    assert len(strategy._ft_informative) == 7   # Equal to number of decorators used
    informative_pairs = [
        ('XRP/USDT', '1h', candle_def),
        ('XRP/BTC', '1h', candle_def),
        ('LTC/USDT', '1h', candle_def),
        ('LTC/BTC', '1h', candle_def),
        ('XRP/USDT', '30m', candle_def),
        ('LTC/USDT', '30m', candle_def),
        ('NEO/USDT', '1h', candle_def),
        ('NEO/USDT', '30m', candle_def),
        ('NEO/USDT', '5m', candle_def),
        ('NEO/USDT', '15m', candle_def),
        ('NEO/USDT', '2h', CandleType.FUTURES),
        ('ETH/BTC', '1h', CandleType.SPOT),  # One candle remains as spot
        ('ETH/USDT', '30m', candle_def)]
    for inf_pair in informative_pairs:
        assert inf_pair in strategy.gather_informative_pairs()

    def test_historic_ohlcv(pair, timeframe, candle_type):
        return data[
            (pair, timeframe or strategy.timeframe, CandleType.from_string(candle_type))].copy()

    mocker.patch('freqtrade.data.dataprovider.DataProvider.historic_ohlcv',
                 side_effect=test_historic_ohlcv)

    analyzed = strategy.advise_all_indicators(
        {p: data[(p, strategy.timeframe, candle_def)] for p in ('XRP/USDT', 'LTC/USDT')})
    expected_columns = [
        'rsi_1h', 'rsi_30m',                    # Stacked informative decorators
        'neo_usdt_rsi_1h',                      # NEO 1h informative
        'rsi_NEO_USDT_neo_usdt_NEO/USDT_30m',   # Column formatting
        'rsi_from_callable',                    # Custom column formatter
        'eth_btc_rsi_1h',                       # Quote currency not matching stake currency
        'rsi', 'rsi_less',                      # Non-informative columns
        'rsi_5m',                               # Manual informative dataframe
    ]
    for _, dataframe in analyzed.items():
        for col in expected_columns:
            assert col in dataframe.columns
