import logging
from pathlib import Path
from typing import List, Optional

import joblib
import pandas as pd
from tabulate import tabulate

from freqtrade.data.btanalysis import (get_latest_backtest_filename, load_backtest_data,
                                       load_backtest_stats)
from freqtrade.exceptions import OperationalException


logger = logging.getLogger(__name__)


def _load_signal_candles(backtest_dir: Path):
    if backtest_dir.is_dir():
        scpf = Path(backtest_dir,
                    Path(get_latest_backtest_filename(backtest_dir)).stem + "_signals.pkl"
                    )
    else:
        scpf = Path(backtest_dir.parent / f"{backtest_dir.stem}_signals.pkl")

    try:
        scp = open(scpf, "rb")
        signal_candles = joblib.load(scp)
        logger.info(f"Loaded signal candles: {str(scpf)}")
    except Exception as e:
        logger.error("Cannot load signal candles from pickled results: ", e)

    return signal_candles


def _process_candles_and_indicators(pairlist, strategy_name, trades, signal_candles):
    analysed_trades_dict = {}
    analysed_trades_dict[strategy_name] = {}

    try:
        logger.info(f"Processing {strategy_name} : {len(pairlist)} pairs")

        for pair in pairlist:
            if pair in signal_candles[strategy_name]:
                analysed_trades_dict[strategy_name][pair] = _analyze_candles_and_indicators(
                                                              pair,
                                                              trades,
                                                              signal_candles[strategy_name][pair])
    except Exception as e:
        print(f"Cannot process entry/exit reasons for {strategy_name}: ", e)

    return analysed_trades_dict


def _analyze_candles_and_indicators(pair, trades, signal_candles):
    buyf = signal_candles

    if len(buyf) > 0:
        buyf = buyf.set_index('date', drop=False)
        trades_red = trades.loc[trades['pair'] == pair].copy()

        trades_inds = pd.DataFrame()

        if trades_red.shape[0] > 0 and buyf.shape[0] > 0:
            for t, v in trades_red.open_date.items():
                allinds = buyf.loc[(buyf['date'] < v)]
                if allinds.shape[0] > 0:
                    tmp_inds = allinds.iloc[[-1]]

                    trades_red.loc[t, 'signal_date'] = tmp_inds['date'].values[0]
                    trades_red.loc[t, 'enter_reason'] = trades_red.loc[t, 'enter_tag']
                    tmp_inds.index.rename('signal_date', inplace=True)
                    trades_inds = pd.concat([trades_inds, tmp_inds])

            if 'signal_date' in trades_red:
                trades_red['signal_date'] = pd.to_datetime(trades_red['signal_date'], utc=True)
                trades_red.set_index('signal_date', inplace=True)

                try:
                    trades_red = pd.merge(trades_red, trades_inds, on='signal_date', how='outer')
                except Exception as e:
                    raise e
        return trades_red
    else:
        return pd.DataFrame()


def _do_group_table_output(bigdf, glist):
    for g in glist:
        # 0: summary wins/losses grouped by enter tag
        if g == "0":
            group_mask = ['enter_reason']
            wins = bigdf.loc[bigdf['profit_abs'] >= 0] \
                        .groupby(group_mask) \
                        .agg({'profit_abs': ['sum']})

            wins.columns = ['profit_abs_wins']
            loss = bigdf.loc[bigdf['profit_abs'] < 0] \
                        .groupby(group_mask) \
                        .agg({'profit_abs': ['sum']})
            loss.columns = ['profit_abs_loss']

            new = bigdf.groupby(group_mask).agg({'profit_abs': [
                                                    'count',
                                                    lambda x: sum(x > 0),
                                                    lambda x: sum(x <= 0)]})
            new = pd.concat([new, wins, loss], axis=1).fillna(0)

            new['profit_tot'] = new['profit_abs_wins'] - abs(new['profit_abs_loss'])
            new['wl_ratio_pct'] = (new.iloc[:, 1] / new.iloc[:, 0] * 100).fillna(0)
            new['avg_win'] = (new['profit_abs_wins'] / new.iloc[:, 1]).fillna(0)
            new['avg_loss'] = (new['profit_abs_loss'] / new.iloc[:, 2]).fillna(0)

            new.columns = ['total_num_buys', 'wins', 'losses', 'profit_abs_wins', 'profit_abs_loss',
                           'profit_tot', 'wl_ratio_pct', 'avg_win', 'avg_loss']

            sortcols = ['total_num_buys']

            _print_table(new, sortcols, show_index=True)

        else:
            agg_mask = {'profit_abs': ['count', 'sum', 'median', 'mean'],
                        'profit_ratio': ['sum', 'median', 'mean']}
            agg_cols = ['num_buys', 'profit_abs_sum', 'profit_abs_median',
                        'profit_abs_mean', 'median_profit_pct', 'mean_profit_pct',
                        'total_profit_pct']
            sortcols = ['profit_abs_sum', 'enter_reason']

            # 1: profit summaries grouped by enter_tag
            if g == "1":
                group_mask = ['enter_reason']

            # 2: profit summaries grouped by enter_tag and exit_tag
            if g == "2":
                group_mask = ['enter_reason', 'exit_reason']

            # 3: profit summaries grouped by pair and enter_tag
            if g == "3":
                group_mask = ['pair', 'enter_reason']

            # 4: profit summaries grouped by pair, enter_ and exit_tag (this can get quite large)
            if g == "4":
                group_mask = ['pair', 'enter_reason', 'exit_reason']
            if group_mask:
                new = bigdf.groupby(group_mask).agg(agg_mask).reset_index()
                new.columns = group_mask + agg_cols
                new['median_profit_pct'] = new['median_profit_pct'] * 100
                new['mean_profit_pct'] = new['mean_profit_pct'] * 100
                new['total_profit_pct'] = new['total_profit_pct'] * 100

                _print_table(new, sortcols)
            else:
                logger.warning("Invalid group mask specified.")


def _print_results(analysed_trades, stratname, analysis_groups,
                   enter_reason_list, exit_reason_list,
                   indicator_list, columns=None):
    if columns is None:
        columns = ['pair', 'open_date', 'close_date', 'profit_abs', 'enter_reason', 'exit_reason']

    bigdf = pd.DataFrame()
    for pair, trades in analysed_trades[stratname].items():
        bigdf = pd.concat([bigdf, trades], ignore_index=True)

    if bigdf.shape[0] > 0 and ('enter_reason' in bigdf.columns):
        if analysis_groups:
            _do_group_table_output(bigdf, analysis_groups)

        if enter_reason_list and "all" not in enter_reason_list:
            bigdf = bigdf.loc[(bigdf['enter_reason'].isin(enter_reason_list))]

        if exit_reason_list and "all" not in exit_reason_list:
            bigdf = bigdf.loc[(bigdf['exit_reason'].isin(exit_reason_list))]

        if "all" in indicator_list:
            print(bigdf)
        elif indicator_list is not None:
            available_inds = []
            for ind in indicator_list:
                if ind in bigdf:
                    available_inds.append(ind)
            ilist = ["pair", "enter_reason", "exit_reason"] + available_inds
            _print_table(bigdf[ilist], sortcols=['exit_reason'], show_index=False)
    else:
        print("\\_ No trades to show")


def _print_table(df, sortcols=None, show_index=False):
    if (sortcols is not None):
        data = df.sort_values(sortcols)
    else:
        data = df

    print(
        tabulate(
            data,
            headers='keys',
            tablefmt='psql',
            showindex=show_index
        )
    )


def process_entry_exit_reasons(backtest_dir: Path,
                               pairlist: List[str],
                               analysis_groups: Optional[List[str]] = ["0", "1", "2"],
                               enter_reason_list: Optional[List[str]] = ["all"],
                               exit_reason_list: Optional[List[str]] = ["all"],
                               indicator_list: Optional[List[str]] = []):
    try:
        backtest_stats = load_backtest_stats(backtest_dir)
        for strategy_name, results in backtest_stats['strategy'].items():
            trades = load_backtest_data(backtest_dir, strategy_name)

            if not trades.empty:
                signal_candles = _load_signal_candles(backtest_dir)
                analysed_trades_dict = _process_candles_and_indicators(pairlist, strategy_name,
                                                                       trades, signal_candles)
                _print_results(analysed_trades_dict,
                               strategy_name,
                               analysis_groups,
                               enter_reason_list,
                               exit_reason_list,
                               indicator_list)

    except ValueError as e:
        raise OperationalException(e) from e
