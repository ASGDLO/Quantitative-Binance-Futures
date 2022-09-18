# pragma pylint: disable=unused-argument, unused-variable, protected-access, invalid-name

"""
This module manage Telegram communication
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from html import escape
from itertools import chain
from math import isnan
from typing import Any, Callable, Dict, List, Optional, Union

import arrow
from tabulate import tabulate
from telegram import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ParseMode, ReplyKeyboardMarkup, Update)
from telegram.error import BadRequest, NetworkError, TelegramError
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, Updater
from telegram.utils.helpers import escape_markdown

from freqtrade.__init__ import __version__
from freqtrade.constants import DUST_PER_COIN
from freqtrade.enums import RPCMessageType
from freqtrade.exceptions import OperationalException
from freqtrade.misc import chunks, plural, round_coin_value
from freqtrade.persistence import Trade
from freqtrade.rpc import RPC, RPCException, RPCHandler


logger = logging.getLogger(__name__)

logger.debug('Included module rpc.telegram ...')

MAX_TELEGRAM_MESSAGE_LENGTH = 4096


def authorized_only(command_handler: Callable[..., None]) -> Callable[..., Any]:
    """
    Decorator to check if the message comes from the correct chat_id
    :param command_handler: Telegram CommandHandler
    :return: decorated function
    """

    def wrapper(self, *args, **kwargs):
        """ Decorator logic """
        update = kwargs.get('update') or args[0]

        # Reject unauthorized messages
        if update.callback_query:
            cchat_id = int(update.callback_query.message.chat.id)
        else:
            cchat_id = int(update.message.chat_id)

        chat_id = int(self._config['telegram']['chat_id'])
        if cchat_id != chat_id:
            logger.info(
                'Rejected unauthorized message from: %s',
                update.message.chat_id
            )
            return wrapper
        # Rollback session to avoid getting data stored in a transaction.
        Trade.query.session.rollback()
        logger.debug(
            'Executing handler: %s for chat_id: %s',
            command_handler.__name__,
            chat_id
        )
        try:
            return command_handler(self, *args, **kwargs)
        except BaseException:
            logger.exception('Exception occurred within Telegram module')

    return wrapper


class Telegram(RPCHandler):
    """  This class handles all telegram communication """

    def __init__(self, rpc: RPC, config: Dict[str, Any]) -> None:
        """
        Init the Telegram call, and init the super class RPCHandler
        :param rpc: instance of RPC Helper class
        :param config: Configuration object
        :return: None
        """
        super().__init__(rpc, config)

        self._updater: Updater
        self._init_keyboard()
        self._init()

    def _init_keyboard(self) -> None:
        """
        Validates the keyboard configuration from telegram config
        section.
        """
        self._keyboard: List[List[Union[str, KeyboardButton]]] = [
            ['/daily', '/profit', '/balance'],
            ['/status', '/status table', '/performance'],
            ['/count', '/start', '/stop', '/help']
        ]
        # do not allow commands with mandatory arguments and critical cmds
        # like /forcesell and /forcebuy
        # TODO: DRY! - its not good to list all valid cmds here. But otherwise
        #       this needs refactoring of the whole telegram module (same
        #       problem in _help()).
        valid_keys: List[str] = [r'/start$', r'/stop$', r'/status$', r'/status table$',
                                 r'/trades$', r'/performance$', r'/buys', r'/sells', r'/mix_tags',
                                 r'/daily$', r'/daily \d+$', r'/profit$', r'/profit \d+',
                                 r'/stats$', r'/count$', r'/locks$', r'/balance$',
                                 r'/stopbuy$', r'/reload_config$', r'/show_config$',
                                 r'/logs$', r'/whitelist$', r'/blacklist$', r'/bl_delete$',
                                 r'/weekly$', r'/weekly \d+$', r'/monthly$', r'/monthly \d+$',
                                 r'/forcebuy$', r'/edge$', r'/health$', r'/help$', r'/version$']
        # Create keys for generation
        valid_keys_print = [k.replace('$', '') for k in valid_keys]

        # custom keyboard specified in config.json
        cust_keyboard = self._config['telegram'].get('keyboard', [])
        if cust_keyboard:
            combined = "(" + ")|(".join(valid_keys) + ")"
            # check for valid shortcuts
            invalid_keys = [b for b in chain.from_iterable(cust_keyboard)
                            if not re.match(combined, b)]
            if len(invalid_keys):
                err_msg = ('config.telegram.keyboard: Invalid commands for '
                           f'custom Telegram keyboard: {invalid_keys}'
                           f'\nvalid commands are: {valid_keys_print}')
                raise OperationalException(err_msg)
            else:
                self._keyboard = cust_keyboard
                logger.info('using custom keyboard from '
                            f'config.json: {self._keyboard}')

    def _init(self) -> None:
        """
        Initializes this module with the given config,
        registers all known command handlers
        and starts polling for message updates
        """
        self._updater = Updater(token=self._config['telegram']['token'], workers=0,
                                use_context=True)

        # Register command handler and start telegram message polling
        handles = [
            CommandHandler('status', self._status),
            CommandHandler('profit', self._profit),
            CommandHandler('balance', self._balance),
            CommandHandler('start', self._start),
            CommandHandler('stop', self._stop),
            CommandHandler('forcesell', self._forcesell),
            CommandHandler('forcebuy', self._forcebuy),
            CommandHandler('trades', self._trades),
            CommandHandler('delete', self._delete_trade),
            CommandHandler('performance', self._performance),
            CommandHandler('buys', self._buy_tag_performance),
            CommandHandler('sells', self._sell_reason_performance),
            CommandHandler('mix_tags', self._mix_tag_performance),
            CommandHandler('stats', self._stats),
            CommandHandler('daily', self._daily),
            CommandHandler('weekly', self._weekly),
            CommandHandler('monthly', self._monthly),
            CommandHandler('count', self._count),
            CommandHandler('locks', self._locks),
            CommandHandler(['unlock', 'delete_locks'], self._delete_locks),
            CommandHandler(['reload_config', 'reload_conf'], self._reload_config),
            CommandHandler(['show_config', 'show_conf'], self._show_config),
            CommandHandler('stopbuy', self._stopbuy),
            CommandHandler('whitelist', self._whitelist),
            CommandHandler('blacklist', self._blacklist),
            CommandHandler(['blacklist_delete', 'bl_delete'], self._blacklist_delete),
            CommandHandler('logs', self._logs),
            CommandHandler('edge', self._edge),
            CommandHandler('health', self._health),
            CommandHandler('help', self._help),
            CommandHandler('version', self._version),
        ]
        callbacks = [
            CallbackQueryHandler(self._status_table, pattern='update_status_table'),
            CallbackQueryHandler(self._daily, pattern='update_daily'),
            CallbackQueryHandler(self._weekly, pattern='update_weekly'),
            CallbackQueryHandler(self._monthly, pattern='update_monthly'),
            CallbackQueryHandler(self._profit, pattern='update_profit'),
            CallbackQueryHandler(self._balance, pattern='update_balance'),
            CallbackQueryHandler(self._performance, pattern='update_performance'),
            CallbackQueryHandler(self._buy_tag_performance, pattern='update_buy_tag_performance'),
            CallbackQueryHandler(self._sell_reason_performance,
                                 pattern='update_sell_reason_performance'),
            CallbackQueryHandler(self._mix_tag_performance, pattern='update_mix_tag_performance'),
            CallbackQueryHandler(self._count, pattern='update_count'),
            CallbackQueryHandler(self._forcebuy_inline),
        ]
        for handle in handles:
            self._updater.dispatcher.add_handler(handle)

        for callback in callbacks:
            self._updater.dispatcher.add_handler(callback)

        self._updater.start_polling(
            bootstrap_retries=-1,
            timeout=20,
            read_latency=60,  # Assumed transmission latency
            drop_pending_updates=True,
        )
        logger.info(
            'rpc.telegram is listening for following commands: %s',
            [h.command for h in handles]
        )

    def cleanup(self) -> None:
        """
        Stops all running telegram threads.
        :return: None
        """
        # This can take up to `timeout` from the call to `start_polling`.
        self._updater.stop()

    def _format_buy_msg(self, msg: Dict[str, Any]) -> str:
        if self._rpc._fiat_converter:
            msg['stake_amount_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['stake_amount'], msg['stake_currency'], msg['fiat_currency'])
        else:
            msg['stake_amount_fiat'] = 0
        is_fill = msg['type'] == RPCMessageType.BUY_FILL
        emoji = '\N{CHECK MARK}' if is_fill else '\N{LARGE BLUE CIRCLE}'

        message = (
            f"{emoji} *{msg['exchange']}:* {'Bought' if is_fill else 'Buying'} {msg['pair']}"
            f" (#{msg['trade_id']})\n"
            )
        message += f"*Buy Tag:* `{msg['buy_tag']}`\n" if msg.get('buy_tag', None) else ""
        message += f"*Amount:* `{msg['amount']:.8f}`\n"

        if msg['type'] == RPCMessageType.BUY_FILL:
            message += f"*Open Rate:* `{msg['open_rate']:.8f}`\n"

        elif msg['type'] == RPCMessageType.BUY:
            message += f"*Open Rate:* `{msg['limit']:.8f}`\n"\
                       f"*Current Rate:* `{msg['current_rate']:.8f}`\n"

        message += f"*Total:* `({round_coin_value(msg['stake_amount'], msg['stake_currency'])}"

        if msg.get('fiat_currency', None):
            message += f", {round_coin_value(msg['stake_amount_fiat'], msg['fiat_currency'])}"

        message += ")`"
        return message

    def _format_sell_msg(self, msg: Dict[str, Any]) -> str:
        msg['amount'] = round(msg['amount'], 8)
        msg['profit_percent'] = round(msg['profit_ratio'] * 100, 2)
        msg['duration'] = msg['close_date'].replace(
            microsecond=0) - msg['open_date'].replace(microsecond=0)
        msg['duration_min'] = msg['duration'].total_seconds() / 60

        msg['buy_tag'] = msg['buy_tag'] if "buy_tag" in msg.keys() else None
        msg['emoji'] = self._get_sell_emoji(msg)

        # Check if all sell properties are available.
        # This might not be the case if the message origin is triggered by /forcesell
        if (all(prop in msg for prop in ['gain', 'fiat_currency', 'stake_currency'])
                and self._rpc._fiat_converter):
            msg['profit_fiat'] = self._rpc._fiat_converter.convert_amount(
                msg['profit_amount'], msg['stake_currency'], msg['fiat_currency'])
            msg['profit_extra'] = (
                f" ({msg['gain']}: {msg['profit_amount']:.8f} {msg['stake_currency']}"
                f" / {msg['profit_fiat']:.3f} {msg['fiat_currency']})")
        else:
            msg['profit_extra'] = ''
        is_fill = msg['type'] == RPCMessageType.SELL_FILL
        message = (
            f"{msg['emoji']} *{msg['exchange']}:* "
            f"{'Sold' if is_fill else 'Selling'} {msg['pair']} (#{msg['trade_id']})\n"
            f"*{'Profit' if is_fill else 'Unrealized Profit'}:* "
            f"`{msg['profit_ratio']:.2%}{msg['profit_extra']}`\n"
            f"*Buy Tag:* `{msg['buy_tag']}`\n"
            f"*Sell Reason:* `{msg['sell_reason']}`\n"
            f"*Duration:* `{msg['duration']} ({msg['duration_min']:.1f} min)`\n"
            f"*Amount:* `{msg['amount']:.8f}`\n"
            f"*Open Rate:* `{msg['open_rate']:.8f}`\n")

        if msg['type'] == RPCMessageType.SELL:
            message += (f"*Current Rate:* `{msg['current_rate']:.8f}`\n"
                        f"*Close Rate:* `{msg['limit']:.8f}`")

        elif msg['type'] == RPCMessageType.SELL_FILL:
            message += f"*Close Rate:* `{msg['close_rate']:.8f}`"

        return message

    def compose_message(self, msg: Dict[str, Any], msg_type: RPCMessageType) -> str:
        if msg_type in [RPCMessageType.BUY, RPCMessageType.BUY_FILL]:
            message = self._format_buy_msg(msg)

        elif msg_type in [RPCMessageType.SELL, RPCMessageType.SELL_FILL]:
            message = self._format_sell_msg(msg)

        elif msg_type in (RPCMessageType.BUY_CANCEL, RPCMessageType.SELL_CANCEL):
            msg['message_side'] = 'buy' if msg_type == RPCMessageType.BUY_CANCEL else 'sell'
            message = ("\N{WARNING SIGN} *{exchange}:* "
                       "Cancelling open {message_side} Order for {pair} (#{trade_id}). "
                       "Reason: {reason}.".format(**msg))

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER:
            message = (
                "*Protection* triggered due to {reason}. "
                "`{pair}` will be locked until `{lock_end_time}`."
            ).format(**msg)

        elif msg_type == RPCMessageType.PROTECTION_TRIGGER_GLOBAL:
            message = (
                "*Protection* triggered due to {reason}. "
                "*All pairs* will be locked until `{lock_end_time}`."
            ).format(**msg)

        elif msg_type == RPCMessageType.STATUS:
            message = '*Status:* `{status}`'.format(**msg)

        elif msg_type == RPCMessageType.WARNING:
            message = '\N{WARNING SIGN} *Warning:* `{status}`'.format(**msg)

        elif msg_type == RPCMessageType.STARTUP:
            message = '{status}'.format(**msg)

        else:
            raise NotImplementedError('Unknown message type: {}'.format(msg_type))
        return message

    def send_msg(self, msg: Dict[str, Any]) -> None:
        """ Send a message to telegram channel """

        default_noti = 'on'

        msg_type = msg['type']
        noti = ''
        if msg_type == RPCMessageType.SELL:
            sell_noti = self._config['telegram'] \
                .get('notification_settings', {}).get(str(msg_type), {})
            # For backward compatibility sell still can be string
            if isinstance(sell_noti, str):
                noti = sell_noti
            else:
                noti = sell_noti.get(str(msg['sell_reason']), default_noti)
        else:
            noti = self._config['telegram'] \
                .get('notification_settings', {}).get(str(msg_type), default_noti)

        if noti == 'off':
            logger.info(f"Notification '{msg_type}' not sent.")
            # Notification disabled
            return

        message = self.compose_message(msg, msg_type)

        self._send_msg(message, disable_notification=(noti == 'silent'))

    def _get_sell_emoji(self, msg):
        """
        Get emoji for sell-side
        """

        if float(msg['profit_percent']) >= 5.0:
            return "\N{ROCKET}"
        elif float(msg['profit_percent']) >= 0.0:
            return "\N{EIGHT SPOKED ASTERISK}"
        elif msg['sell_reason'] == "stop_loss":
            return "\N{WARNING SIGN}"
        else:
            return "\N{CROSS MARK}"

    def _prepare_entry_details(self, filled_orders: List, base_currency: str, is_open: bool):
        """
        Prepare details of trade with entry adjustment enabled
        """
        lines: List[str] = []
        if len(filled_orders) > 0:
            first_avg = filled_orders[0]["safe_price"]

        for x, order in enumerate(filled_orders):
            if order['ft_order_side'] != 'buy':
                continue
            cur_entry_datetime = arrow.get(order["order_filled_date"])
            cur_entry_amount = order["amount"]
            cur_entry_average = order["safe_price"]
            lines.append("  ")
            if x == 0:
                lines.append(f"*Entry #{x+1}:*")
                lines.append(
                    f"*Entry Amount:* {cur_entry_amount} ({order['cost']:.8f} {base_currency})")
                lines.append(f"*Average Entry Price:* {cur_entry_average}")
            else:
                sumA = 0
                sumB = 0
                for y in range(x):
                    sumA += (filled_orders[y]["amount"] * filled_orders[y]["safe_price"])
                    sumB += filled_orders[y]["amount"]
                prev_avg_price = sumA / sumB
                price_to_1st_entry = ((cur_entry_average - first_avg) / first_avg)
                minus_on_entry = 0
                if prev_avg_price:
                    minus_on_entry = (cur_entry_average - prev_avg_price) / prev_avg_price

                dur_entry = cur_entry_datetime - arrow.get(filled_orders[x-1]["order_filled_date"])
                days = dur_entry.days
                hours, remainder = divmod(dur_entry.seconds, 3600)
                minutes, seconds = divmod(remainder, 60)
                lines.append(f"*Entry #{x+1}:* at {minus_on_entry:.2%} avg profit")
                if is_open:
                    lines.append("({})".format(cur_entry_datetime
                                               .humanize(granularity=["day", "hour", "minute"])))
                lines.append(
                    f"*Entry Amount:* {cur_entry_amount} ({order['cost']:.8f} {base_currency})")
                lines.append(f"*Average Entry Price:* {cur_entry_average} "
                             f"({price_to_1st_entry:.2%} from 1st entry rate)")
                lines.append(f"*Order filled at:* {order['order_filled_date']}")
                lines.append(f"({days}d {hours}h {minutes}m {seconds}s from previous entry)")
        return lines

    @authorized_only
    def _status(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status.
        Returns the current TradeThread status
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        if context.args and 'table' in context.args:
            self._status_table(update, context)
            return

        try:

            # Check if there's at least one numerical ID provided.
            # If so, try to get only these trades.
            trade_ids = []
            if context.args and len(context.args) > 0:
                trade_ids = [int(i) for i in context.args if i.isnumeric()]

            results = self._rpc._rpc_trade_status(trade_ids=trade_ids)
            position_adjust = self._config.get('position_adjustment_enable', False)
            max_entries = self._config.get('max_entry_position_adjustment', -1)
            messages = []
            for r in results:
                r['open_date_hum'] = arrow.get(r['open_date']).humanize()
                r['num_entries'] = len([o for o in r['orders'] if o['ft_order_side'] == 'buy'])
                r['sell_reason'] = r.get('sell_reason', "")
                lines = [
                    "*Trade ID:* `{trade_id}`" +
                    ("` (since {open_date_hum})`" if r['is_open'] else ""),
                    "*Current Pair:* {pair}",
                    "*Amount:* `{amount} ({stake_amount} {base_currency})`",
                    "*Entry Tag:* `{buy_tag}`" if r['buy_tag'] else "",
                    "*Exit Reason:* `{sell_reason}`" if r['sell_reason'] else "",
                ]

                if position_adjust:
                    max_buy_str = (f"/{max_entries + 1}" if (max_entries > 0) else "")
                    lines.append("*Number of Entries:* `{num_entries}`" + max_buy_str)

                lines.extend([
                    "*Open Rate:* `{open_rate:.8f}`",
                    "*Close Rate:* `{close_rate:.8f}`" if r['close_rate'] else "",
                    "*Open Date:* `{open_date}`",
                    "*Close Date:* `{close_date}`" if r['close_date'] else "",
                    "*Current Rate:* `{current_rate:.8f}`" if r['is_open'] else "",
                    ("*Current Profit:* " if r['is_open'] else "*Close Profit: *")
                    + "`{profit_ratio:.2%}`",
                ])

                if r['is_open']:
                    if (r['stop_loss_abs'] != r['initial_stop_loss_abs']
                            and r['initial_stop_loss_ratio'] is not None):
                        # Adding initial stoploss only if it is different from stoploss
                        lines.append("*Initial Stoploss:* `{initial_stop_loss_abs:.8f}` "
                                     "`({initial_stop_loss_ratio:.2%})`")

                    # Adding stoploss and stoploss percentage only if it is not None
                    lines.append("*Stoploss:* `{stop_loss_abs:.8f}` " +
                                 ("`({stop_loss_ratio:.2%})`" if r['stop_loss_ratio'] else ""))
                    lines.append("*Stoploss distance:* `{stoploss_current_dist:.8f}` "
                                 "`({stoploss_current_dist_ratio:.2%})`")
                    if r['open_order']:
                        if r['sell_order_status']:
                            lines.append("*Open Order:* `{open_order}` - `{sell_order_status}`")
                        else:
                            lines.append("*Open Order:* `{open_order}`")

                lines_detail = self._prepare_entry_details(
                    r['orders'], r['base_currency'], r['is_open'])
                lines.extend(lines_detail if lines_detail else "")

                # Filter empty lines using list-comprehension
                messages.append("\n".join([line for line in lines if line]).format(**r))

            for msg in messages:
                self._send_msg(msg)

        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _status_table(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /status table.
        Returns the current TradeThread status in table format
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            fiat_currency = self._config.get('fiat_display_currency', '')
            statlist, head, fiat_profit_sum = self._rpc._rpc_status_table(
                self._config['stake_currency'], fiat_currency)

            show_total = not isnan(fiat_profit_sum) and len(statlist) > 1
            max_trades_per_msg = 50
            """
            Calculate the number of messages of 50 trades per message
            0.99 is used to make sure that there are no extra (empty) messages
            As an example with 50 trades, there will be int(50/50 + 0.99) = 1 message
            """
            messages_count = max(int(len(statlist) / max_trades_per_msg + 0.99), 1)
            for i in range(0, messages_count):
                trades = statlist[i * max_trades_per_msg:(i + 1) * max_trades_per_msg]
                if show_total and i == messages_count - 1:
                    # append total line
                    trades.append(["Total", "", "", f"{fiat_profit_sum:.2f} {fiat_currency}"])

                message = tabulate(trades,
                                   headers=head,
                                   tablefmt='simple')
                if show_total and i == messages_count - 1:
                    # insert separators line between Total
                    lines = message.split("\n")
                    message = "\n".join(lines[:-1] + [lines[1]] + [lines[-1]])
                self._send_msg(f"<pre>{message}</pre>", parse_mode=ParseMode.HTML,
                               reload_able=True, callback_path="update_status_table",
                               query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _daily(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /daily <n>
        Returns a daily profit (in BTC) over the last n days.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config['stake_currency']
        fiat_disp_cur = self._config.get('fiat_display_currency', '')
        try:
            timescale = int(context.args[0]) if context.args else 7
        except (TypeError, ValueError, IndexError):
            timescale = 7
        try:
            stats = self._rpc._rpc_daily_profit(
                timescale,
                stake_cur,
                fiat_disp_cur
            )
            stats_tab = tabulate(
                [[day['date'],
                  f"{round_coin_value(day['abs_profit'], stats['stake_currency'])}",
                  f"{day['fiat_value']:.3f} {stats['fiat_display_currency']}",
                  f"{day['trade_count']} trades"] for day in stats['data']],
                headers=[
                    'Day',
                    f'Profit {stake_cur}',
                    f'Profit {fiat_disp_cur}',
                    'Trades',
                ],
                tablefmt='simple')
            message = f'<b>Daily Profit over the last {timescale} days</b>:\n<pre>{stats_tab}</pre>'
            self._send_msg(message, parse_mode=ParseMode.HTML, reload_able=True,
                           callback_path="update_daily", query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _weekly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /weekly <n>
        Returns a weekly profit (in BTC) over the last n weeks.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config['stake_currency']
        fiat_disp_cur = self._config.get('fiat_display_currency', '')
        try:
            timescale = int(context.args[0]) if context.args else 8
        except (TypeError, ValueError, IndexError):
            timescale = 8
        try:
            stats = self._rpc._rpc_weekly_profit(
                timescale,
                stake_cur,
                fiat_disp_cur
            )
            stats_tab = tabulate(
                [[week['date'],
                  f"{round_coin_value(week['abs_profit'], stats['stake_currency'])}",
                  f"{week['fiat_value']:.3f} {stats['fiat_display_currency']}",
                  f"{week['trade_count']} trades"] for week in stats['data']],
                headers=[
                    'Monday',
                    f'Profit {stake_cur}',
                    f'Profit {fiat_disp_cur}',
                    'Trades',
                ],
                tablefmt='simple')
            message = f'<b>Weekly Profit over the last {timescale} weeks ' \
                      f'(starting from Monday)</b>:\n<pre>{stats_tab}</pre> '
            self._send_msg(message, parse_mode=ParseMode.HTML, reload_able=True,
                           callback_path="update_weekly", query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _monthly(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /monthly <n>
        Returns a monthly profit (in BTC) over the last n months.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config['stake_currency']
        fiat_disp_cur = self._config.get('fiat_display_currency', '')
        try:
            timescale = int(context.args[0]) if context.args else 6
        except (TypeError, ValueError, IndexError):
            timescale = 6
        try:
            stats = self._rpc._rpc_monthly_profit(
                timescale,
                stake_cur,
                fiat_disp_cur
            )
            stats_tab = tabulate(
                [[month['date'],
                  f"{round_coin_value(month['abs_profit'], stats['stake_currency'])}",
                  f"{month['fiat_value']:.3f} {stats['fiat_display_currency']}",
                  f"{month['trade_count']} trades"] for month in stats['data']],
                headers=[
                    'Month',
                    f'Profit {stake_cur}',
                    f'Profit {fiat_disp_cur}',
                    'Trades',
                ],
                tablefmt='simple')
            message = f'<b>Monthly Profit over the last {timescale} months' \
                      f'</b>:\n<pre>{stats_tab}</pre> '
            self._send_msg(message, parse_mode=ParseMode.HTML, reload_able=True,
                           callback_path="update_monthly", query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _profit(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /profit.
        Returns a cumulative profit statistics.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config['stake_currency']
        fiat_disp_cur = self._config.get('fiat_display_currency', '')

        start_date = datetime.fromtimestamp(0)
        timescale = None
        try:
            if context.args:
                timescale = int(context.args[0]) - 1
                today_start = datetime.combine(date.today(), datetime.min.time())
                start_date = today_start - timedelta(days=timescale)
        except (TypeError, ValueError, IndexError):
            pass

        stats = self._rpc._rpc_trade_statistics(
            stake_cur,
            fiat_disp_cur,
            start_date)
        profit_closed_coin = stats['profit_closed_coin']
        profit_closed_ratio_mean = stats['profit_closed_ratio_mean']
        profit_closed_percent = stats['profit_closed_percent']
        profit_closed_fiat = stats['profit_closed_fiat']
        profit_all_coin = stats['profit_all_coin']
        profit_all_ratio_mean = stats['profit_all_ratio_mean']
        profit_all_percent = stats['profit_all_percent']
        profit_all_fiat = stats['profit_all_fiat']
        trade_count = stats['trade_count']
        first_trade_date = stats['first_trade_date']
        latest_trade_date = stats['latest_trade_date']
        avg_duration = stats['avg_duration']
        best_pair = stats['best_pair']
        best_pair_profit_ratio = stats['best_pair_profit_ratio']
        if stats['trade_count'] == 0:
            markdown_msg = 'No trades yet.'
        else:
            # Message to display
            if stats['closed_trade_count'] > 0:
                markdown_msg = ("*ROI:* Closed trades\n"
                                f"∙ `{round_coin_value(profit_closed_coin, stake_cur)} "
                                f"({profit_closed_ratio_mean:.2%}) "
                                f"({profit_closed_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
                                f"∙ `{round_coin_value(profit_closed_fiat, fiat_disp_cur)}`\n")
            else:
                markdown_msg = "`No closed trade` \n"

            markdown_msg += (
                f"*ROI:* All trades\n"
                f"∙ `{round_coin_value(profit_all_coin, stake_cur)} "
                f"({profit_all_ratio_mean:.2%}) "
                f"({profit_all_percent} \N{GREEK CAPITAL LETTER SIGMA}%)`\n"
                f"∙ `{round_coin_value(profit_all_fiat, fiat_disp_cur)}`\n"
                f"*Total Trade Count:* `{trade_count}`\n"
                f"*{'First Trade opened' if not timescale else 'Showing Profit since'}:* "
                f"`{first_trade_date}`\n"
                f"*Latest Trade opened:* `{latest_trade_date}\n`"
                f"*Win / Loss:* `{stats['winning_trades']} / {stats['losing_trades']}`"
            )
            if stats['closed_trade_count'] > 0:
                markdown_msg += (f"\n*Avg. Duration:* `{avg_duration}`\n"
                                 f"*Best Performing:* `{best_pair}: {best_pair_profit_ratio:.2%}`")
        self._send_msg(markdown_msg, reload_able=True, callback_path="update_profit",
                       query=update.callback_query)

    @authorized_only
    def _stats(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stats
        Show stats of recent trades
        """
        stats = self._rpc._rpc_stats()

        reason_map = {
            'roi': 'ROI',
            'stop_loss': 'Stoploss',
            'trailing_stop_loss': 'Trail. Stop',
            'stoploss_on_exchange': 'Stoploss',
            'sell_signal': 'Sell Signal',
            'force_sell': 'Forcesell',
            'emergency_sell': 'Emergency Sell',
        }
        sell_reasons_tabulate = [
            [
                reason_map.get(reason, reason),
                sum(count.values()),
                count['wins'],
                count['losses']
            ] for reason, count in stats['sell_reasons'].items()
        ]
        sell_reasons_msg = 'No trades yet.'
        for reason in chunks(sell_reasons_tabulate, 25):
            sell_reasons_msg = tabulate(
                reason,
                headers=['Sell Reason', 'Sells', 'Wins', 'Losses']
            )
            if len(sell_reasons_tabulate) > 25:
                self._send_msg(sell_reasons_msg, ParseMode.MARKDOWN)
                sell_reasons_msg = ''

        durations = stats['durations']
        duration_msg = tabulate(
            [
                ['Wins', str(timedelta(seconds=durations['wins']))
                 if durations['wins'] is not None else 'N/A'],
                ['Losses', str(timedelta(seconds=durations['losses']))
                 if durations['losses'] is not None else 'N/A']
            ],
            headers=['', 'Avg. Duration']
        )
        msg = (f"""```\n{sell_reasons_msg}```\n```\n{duration_msg}```""")

        self._send_msg(msg, ParseMode.MARKDOWN)

    @authorized_only
    def _balance(self, update: Update, context: CallbackContext) -> None:
        """ Handler for /balance """
        try:
            result = self._rpc._rpc_balance(self._config['stake_currency'],
                                            self._config.get('fiat_display_currency', ''))

            balance_dust_level = self._config['telegram'].get('balance_dust_level', 0.0)
            if not balance_dust_level:
                balance_dust_level = DUST_PER_COIN.get(self._config['stake_currency'], 1.0)

            output = ''
            if self._config['dry_run']:
                output += "*Warning:* Simulated balances in Dry Mode.\n"
            starting_cap = round_coin_value(
                result['starting_capital'], self._config['stake_currency'])
            output += f"Starting capital: `{starting_cap}`"
            starting_cap_fiat = round_coin_value(
                result['starting_capital_fiat'], self._config['fiat_display_currency']
            ) if result['starting_capital_fiat'] > 0 else ''
            output += (f" `, {starting_cap_fiat}`.\n"
                       ) if result['starting_capital_fiat'] > 0 else '.\n'

            total_dust_balance = 0
            total_dust_currencies = 0
            for curr in result['currencies']:
                curr_output = ''
                if curr['est_stake'] > balance_dust_level:
                    curr_output = (
                        f"*{curr['currency']}:*\n"
                        f"\t`Available: {curr['free']:.8f}`\n"
                        f"\t`Balance: {curr['balance']:.8f}`\n"
                        f"\t`Pending: {curr['used']:.8f}`\n"
                        f"\t`Est. {curr['stake']}: "
                        f"{round_coin_value(curr['est_stake'], curr['stake'], False)}`\n")
                elif curr['est_stake'] <= balance_dust_level:
                    total_dust_balance += curr['est_stake']
                    total_dust_currencies += 1

                # Handle overflowing message length
                if len(output + curr_output) >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    self._send_msg(output)
                    output = curr_output
                else:
                    output += curr_output

            if total_dust_balance > 0:
                output += (
                    f"*{total_dust_currencies} Other "
                    f"{plural(total_dust_currencies, 'Currency', 'Currencies')} "
                    f"(< {balance_dust_level} {result['stake']}):*\n"
                    f"\t`Est. {result['stake']}: "
                    f"{round_coin_value(total_dust_balance, result['stake'], False)}`\n")
            tc = result['trade_count'] > 0
            stake_improve = f" `({result['starting_capital_ratio']:.2%})`" if tc else ''
            fiat_val = f" `({result['starting_capital_fiat_ratio']:.2%})`" if tc else ''

            output += ("\n*Estimated Value*:\n"
                       f"\t`{result['stake']}: "
                       f"{round_coin_value(result['total'], result['stake'], False)}`"
                       f"{stake_improve}\n"
                       f"\t`{result['symbol']}: "
                       f"{round_coin_value(result['value'], result['symbol'], False)}`"
                       f"{fiat_val}\n")
            self._send_msg(output, reload_able=True, callback_path="update_balance",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _start(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /start.
        Starts TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_start()
        self._send_msg('Status: `{status}`'.format(**msg))

    @authorized_only
    def _stop(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop.
        Stops TradeThread
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_stop()
        self._send_msg('Status: `{status}`'.format(**msg))

    @authorized_only
    def _reload_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /reload_config.
        Triggers a config file reload
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_reload_config()
        self._send_msg('Status: `{status}`'.format(**msg))

    @authorized_only
    def _stopbuy(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /stop_buy.
        Sets max_open_trades to 0 and gracefully sells all open trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        msg = self._rpc._rpc_stopbuy()
        self._send_msg('Status: `{status}`'.format(**msg))

    @authorized_only
    def _forcesell(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /forcesell <id>.
        Sells the given trade at current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """

        trade_id = context.args[0] if context.args and len(context.args) > 0 else None
        if not trade_id:
            self._send_msg("You must specify a trade-id or 'all'.")
            return
        try:
            msg = self._rpc._rpc_forcesell(trade_id)
            self._send_msg('Forcesell Result: `{result}`'.format(**msg))

        except RPCException as e:
            self._send_msg(str(e))

    def _forcebuy_action(self, pair, price=None):
        if pair != 'cancel':
            try:
                self._rpc._rpc_forcebuy(pair, price)
            except RPCException as e:
                self._send_msg(str(e))

    def _forcebuy_inline(self, update: Update, _: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            pair = query.data
            query.answer()
            query.edit_message_text(text=f"Force Buying: {pair}")
            self._forcebuy_action(pair)

    @staticmethod
    def _layout_inline_keyboard(buttons: List[InlineKeyboardButton],
                                cols=3) -> List[List[InlineKeyboardButton]]:
        return [buttons[i:i + cols] for i in range(0, len(buttons), cols)]

    @authorized_only
    def _forcebuy(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /forcebuy <asset> <price>.
        Buys a pair trade at the given or current price
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        if context.args:
            pair = context.args[0]
            price = float(context.args[1]) if len(context.args) > 1 else None
            self._forcebuy_action(pair, price)
        else:
            whitelist = self._rpc._rpc_whitelist()['whitelist']
            pair_buttons = [
                InlineKeyboardButton(text=pair, callback_data=pair) for pair in sorted(whitelist)]
            buttons_aligned = self._layout_inline_keyboard(pair_buttons)

            buttons_aligned.append([InlineKeyboardButton(text='Cancel', callback_data='cancel')])
            self._send_msg(msg="Which pair?",
                           keyboard=buttons_aligned)

    @authorized_only
    def _trades(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /trades <n>
        Returns last n recent trades.
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        stake_cur = self._config['stake_currency']
        try:
            nrecent = int(context.args[0]) if context.args else 10
        except (TypeError, ValueError, IndexError):
            nrecent = 10
        try:
            trades = self._rpc._rpc_trade_history(
                nrecent
            )
            trades_tab = tabulate(
                [[arrow.get(trade['close_date']).humanize(),
                  trade['pair'] + " (#" + str(trade['trade_id']) + ")",
                  f"{(trade['close_profit']):.2%} ({trade['close_profit_abs']})"]
                 for trade in trades['trades']],
                headers=[
                    'Close Date',
                    'Pair (ID)',
                    f'Profit ({stake_cur})',
                ],
                tablefmt='simple')
            message = (f"<b>{min(trades['trades_count'], nrecent)} recent trades</b>:\n"
                       + (f"<pre>{trades_tab}</pre>" if trades['trades_count'] > 0 else ''))
            self._send_msg(message, parse_mode=ParseMode.HTML)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _delete_trade(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete <id>.
        Delete the given trade
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            if not context.args or len(context.args) == 0:
                raise RPCException("Trade-id not set.")
            trade_id = int(context.args[0])
            msg = self._rpc._rpc_delete(trade_id)
            self._send_msg((
                '`{result_msg}`\n'
                'Please make sure to take care of this asset on the exchange manually.'
            ).format(**msg))

        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /performance.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            trades = self._rpc._rpc_performance()
            output = "<b>Performance:</b>\n"
            for i, trade in enumerate(trades):
                stat_line = (
                    f"{i+1}.\t <code>{trade['pair']}\t"
                    f"{round_coin_value(trade['profit_abs'], self._config['stake_currency'])} "
                    f"({trade['profit_ratio']:.2%}) "
                    f"({trade['count']})</code>\n")

                if len(output + stat_line) >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    self._send_msg(output, parse_mode=ParseMode.HTML)
                    output = stat_line
                else:
                    output += stat_line

            self._send_msg(output, parse_mode=ParseMode.HTML,
                           reload_able=True, callback_path="update_performance",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _buy_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /buys PAIR .
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            pair = None
            if context.args and isinstance(context.args[0], str):
                pair = context.args[0]

            trades = self._rpc._rpc_buy_tag_performance(pair)
            output = "<b>Buy Tag Performance:</b>\n"
            for i, trade in enumerate(trades):
                stat_line = (
                    f"{i+1}.\t <code>{trade['buy_tag']}\t"
                    f"{round_coin_value(trade['profit_abs'], self._config['stake_currency'])} "
                    f"({trade['profit_ratio']:.2%}) "
                    f"({trade['count']})</code>\n")

                if len(output + stat_line) >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    self._send_msg(output, parse_mode=ParseMode.HTML)
                    output = stat_line
                else:
                    output += stat_line

            self._send_msg(output, parse_mode=ParseMode.HTML,
                           reload_able=True, callback_path="update_buy_tag_performance",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _sell_reason_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /sells.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            pair = None
            if context.args and isinstance(context.args[0], str):
                pair = context.args[0]

            trades = self._rpc._rpc_sell_reason_performance(pair)
            output = "<b>Sell Reason Performance:</b>\n"
            for i, trade in enumerate(trades):
                stat_line = (
                    f"{i+1}.\t <code>{trade['sell_reason']}\t"
                    f"{round_coin_value(trade['profit_abs'], self._config['stake_currency'])} "
                    f"({trade['profit_ratio']:.2%}) "
                    f"({trade['count']})</code>\n")

                if len(output + stat_line) >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    self._send_msg(output, parse_mode=ParseMode.HTML)
                    output = stat_line
                else:
                    output += stat_line

            self._send_msg(output, parse_mode=ParseMode.HTML,
                           reload_able=True, callback_path="update_sell_reason_performance",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _mix_tag_performance(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /mix_tags.
        Shows a performance statistic from finished trades
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            pair = None
            if context.args and isinstance(context.args[0], str):
                pair = context.args[0]

            trades = self._rpc._rpc_mix_tag_performance(pair)
            output = "<b>Mix Tag Performance:</b>\n"
            for i, trade in enumerate(trades):
                stat_line = (
                    f"{i+1}.\t <code>{trade['mix_tag']}\t"
                    f"{round_coin_value(trade['profit_abs'], self._config['stake_currency'])} "
                    f"({trade['profit']:.2%}) "
                    f"({trade['count']})</code>\n")

                if len(output + stat_line) >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    self._send_msg(output, parse_mode=ParseMode.HTML)
                    output = stat_line
                else:
                    output += stat_line

            self._send_msg(output, parse_mode=ParseMode.HTML,
                           reload_able=True, callback_path="update_mix_tag_performance",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _count(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /count.
        Returns the number of trades running
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        try:
            counts = self._rpc._rpc_count()
            message = tabulate({k: [v] for k, v in counts.items()},
                               headers=['current', 'max', 'total stake'],
                               tablefmt='simple')
            message = "<pre>{}</pre>".format(message)
            logger.debug(message)
            self._send_msg(message, parse_mode=ParseMode.HTML,
                           reload_able=True, callback_path="update_count",
                           query=update.callback_query)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /locks.
        Returns the currently active locks
        """
        rpc_locks = self._rpc._rpc_locks()
        if not rpc_locks['locks']:
            self._send_msg('No active locks.', parse_mode=ParseMode.HTML)

        for locks in chunks(rpc_locks['locks'], 25):
            message = tabulate([[
                lock['id'],
                lock['pair'],
                lock['lock_end_time'],
                lock['reason']] for lock in locks],
                headers=['ID', 'Pair', 'Until', 'Reason'],
                tablefmt='simple')
            message = f"<pre>{escape(message)}</pre>"
            logger.debug(message)
            self._send_msg(message, parse_mode=ParseMode.HTML)

    @authorized_only
    def _delete_locks(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /delete_locks.
        Returns the currently active locks
        """
        arg = context.args[0] if context.args and len(context.args) > 0 else None
        lockid = None
        pair = None
        if arg:
            try:
                lockid = int(arg)
            except ValueError:
                pair = arg

        self._rpc._rpc_delete_lock(lockid=lockid, pair=pair)
        self._locks(update, context)

    @authorized_only
    def _whitelist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /whitelist
        Shows the currently active whitelist
        """
        try:
            whitelist = self._rpc._rpc_whitelist()

            message = f"Using whitelist `{whitelist['method']}` with {whitelist['length']} pairs\n"
            message += f"`{', '.join(whitelist['whitelist'])}`"

            logger.debug(message)
            self._send_msg(message)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _blacklist(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /blacklist
        Shows the currently active blacklist
        """
        self.send_blacklist_msg(self._rpc._rpc_blacklist(context.args))

    def send_blacklist_msg(self, blacklist: Dict):
        errmsgs = []
        for pair, error in blacklist['errors'].items():
            errmsgs.append(f"Error adding `{pair}` to blacklist: `{error['error_msg']}`")
        if errmsgs:
            self._send_msg('\n'.join(errmsgs))

        message = f"Blacklist contains {blacklist['length']} pairs\n"
        message += f"`{', '.join(blacklist['blacklist'])}`"

        logger.debug(message)
        self._send_msg(message)

    @authorized_only
    def _blacklist_delete(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /bl_delete
        Deletes pair(s) from current blacklist
        """
        self.send_blacklist_msg(self._rpc._rpc_blacklist_delete(context.args or []))

    @authorized_only
    def _logs(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /logs
        Shows the latest logs
        """
        try:
            try:
                limit = int(context.args[0]) if context.args else 10
            except (TypeError, ValueError, IndexError):
                limit = 10
            logs = RPC._rpc_get_logs(limit)['logs']
            msgs = ''
            msg_template = "*{}* {}: {} \\- `{}`"
            for logrec in logs:
                msg = msg_template.format(escape_markdown(logrec[0], version=2),
                                          escape_markdown(logrec[2], version=2),
                                          escape_markdown(logrec[3], version=2),
                                          escape_markdown(logrec[4], version=2))
                if len(msgs + msg) + 10 >= MAX_TELEGRAM_MESSAGE_LENGTH:
                    # Send message immediately if it would become too long
                    self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)
                    msgs = msg + '\n'
                else:
                    # Append message to messages to send
                    msgs += msg + '\n'

            if msgs:
                self._send_msg(msgs, parse_mode=ParseMode.MARKDOWN_V2)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _edge(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /edge
        Shows information related to Edge
        """
        try:
            edge_pairs = self._rpc._rpc_edge()
            if not edge_pairs:
                message = '<b>Edge only validated following pairs:</b>'
                self._send_msg(message, parse_mode=ParseMode.HTML)

            for chunk in chunks(edge_pairs, 25):
                edge_pairs_tab = tabulate(chunk, headers='keys', tablefmt='simple')
                message = (f'<b>Edge only validated following pairs:</b>\n'
                           f'<pre>{edge_pairs_tab}</pre>')

                self._send_msg(message, parse_mode=ParseMode.HTML)

        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _help(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /help.
        Show commands of the bot
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        forcebuy_text = ("*/forcebuy <pair> [<rate>]:* `Instantly buys the given pair. "
                         "Optionally takes a rate at which to buy "
                         "(only applies to limit orders).` \n")
        message = (
            "_BotControl_\n"
            "------------\n"
            "*/start:* `Starts the trader`\n"
            "*/stop:* Stops the trader\n"
            "*/stopbuy:* `Stops buying, but handles open trades gracefully` \n"
            "*/forcesell <trade_id>|all:* `Instantly sells the given trade or all trades, "
            "regardless of profit`\n"
            f"{forcebuy_text if self._config.get('forcebuy_enable', False) else ''}"
            "*/delete <trade_id>:* `Instantly delete the given trade in the database`\n"
            "*/whitelist:* `Show current whitelist` \n"
            "*/blacklist [pair]:* `Show current blacklist, or adds one or more pairs "
            "to the blacklist.` \n"
            "*/blacklist_delete [pairs]| /bl_delete [pairs]:* "
            "`Delete pair / pattern from blacklist. Will reset on reload_conf.` \n"
            "*/reload_config:* `Reload configuration file` \n"
            "*/unlock <pair|id>:* `Unlock this Pair (or this lock id if it's numeric)`\n"

            "_Current state_\n"
            "------------\n"
            "*/show_config:* `Show running configuration` \n"
            "*/locks:* `Show currently locked pairs`\n"
            "*/balance:* `Show account balance per currency`\n"
            "*/logs [limit]:* `Show latest logs - defaults to 10` \n"
            "*/count:* `Show number of active trades compared to allowed number of trades`\n"
            "*/edge:* `Shows validated pairs by Edge if it is enabled` \n"
            "*/health* `Show latest process timestamp - defaults to 1970-01-01 00:00:00` \n"

            "_Statistics_\n"
            "------------\n"
            "*/status <trade_id>|[table]:* `Lists all open trades`\n"
            "         *<trade_id> :* `Lists one or more specific trades.`\n"
            "                        `Separate multiple <trade_id> with a blank space.`\n"
            "         *table :* `will display trades in a table`\n"
            "                `pending buy orders are marked with an asterisk (*)`\n"
            "                `pending sell orders are marked with a double asterisk (**)`\n"
            "*/buys <pair|none>:* `Shows the buy_tag performance`\n"
            "*/sells <pair|none>:* `Shows the sell reason performance`\n"
            "*/mix_tags <pair|none>:* `Shows combined buy tag + sell reason performance`\n"
            "*/trades [limit]:* `Lists last closed trades (limited to 10 by default)`\n"
            "*/profit [<n>]:* `Lists cumulative profit from all finished trades, "
            "over the last n days`\n"
            "*/performance:* `Show performance of each finished trade grouped by pair`\n"
            "*/daily <n>:* `Shows profit or loss per day, over the last n days`\n"
            "*/weekly <n>:* `Shows statistics per week, over the last n weeks`\n"
            "*/monthly <n>:* `Shows statistics per month, over the last n months`\n"
            "*/stats:* `Shows Wins / losses by Sell reason as well as "
            "Avg. holding durationsfor buys and sells.`\n"
            "*/help:* `This help message`\n"
            "*/version:* `Show version`"
            )

        self._send_msg(message, parse_mode=ParseMode.MARKDOWN)

    @authorized_only
    def _health(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /health
        Shows the last process timestamp
        """
        try:
            health = self._rpc._health()
            message = f"Last process: `{health['last_process_loc']}`"
            self._send_msg(message)
        except RPCException as e:
            self._send_msg(str(e))

    @authorized_only
    def _version(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /version.
        Show version information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        strategy_version = self._rpc._freqtrade.strategy.version()
        version_string = f'*Version:* `{__version__}`'
        if strategy_version is not None:
            version_string += f', *Strategy version: * `{strategy_version}`'

        self._send_msg(version_string)

    @authorized_only
    def _show_config(self, update: Update, context: CallbackContext) -> None:
        """
        Handler for /show_config.
        Show config information information
        :param bot: telegram bot
        :param update: message update
        :return: None
        """
        val = RPC._rpc_show_config(self._config, self._rpc._freqtrade.state)

        if val['trailing_stop']:
            sl_info = (
                f"*Initial Stoploss:* `{val['stoploss']}`\n"
                f"*Trailing stop positive:* `{val['trailing_stop_positive']}`\n"
                f"*Trailing stop offset:* `{val['trailing_stop_positive_offset']}`\n"
                f"*Only trail above offset:* `{val['trailing_only_offset_is_reached']}`\n"
            )

        else:
            sl_info = f"*Stoploss:* `{val['stoploss']}`\n"

        if val['position_adjustment_enable']:
            pa_info = (
                f"*Position adjustment:* On\n"
                f"*Max enter position adjustment:* `{val['max_entry_position_adjustment']}`\n"
            )
        else:
            pa_info = "*Position adjustment:* Off\n"

        self._send_msg(
            f"*Mode:* `{'Dry-run' if val['dry_run'] else 'Live'}`\n"
            f"*Exchange:* `{val['exchange']}`\n"
            f"*Stake per trade:* `{val['stake_amount']} {val['stake_currency']}`\n"
            f"*Max open Trades:* `{val['max_open_trades']}`\n"
            f"*Minimum ROI:* `{val['minimal_roi']}`\n"
            f"*Ask strategy:* ```\n{json.dumps(val['ask_strategy'])}```\n"
            f"*Bid strategy:* ```\n{json.dumps(val['bid_strategy'])}```\n"
            f"{sl_info}"
            f"{pa_info}"
            f"*Timeframe:* `{val['timeframe']}`\n"
            f"*Strategy:* `{val['strategy']}`\n"
            f"*Current state:* `{val['state']}`"
        )

    def _update_msg(self, query: CallbackQuery, msg: str, callback_path: str = "",
                    reload_able: bool = False, parse_mode: str = ParseMode.MARKDOWN) -> None:
        if reload_able:
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Refresh", callback_data=callback_path)],
            ])
        else:
            reply_markup = InlineKeyboardMarkup([[]])
        msg += "\nUpdated: {}".format(datetime.now().ctime())
        if not query.message:
            return
        chat_id = query.message.chat_id
        message_id = query.message.message_id

        try:
            self._updater.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=msg,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except BadRequest as e:
            if 'not modified' in e.message.lower():
                pass
            else:
                logger.warning('TelegramError: %s', e.message)
        except TelegramError as telegram_err:
            logger.warning('TelegramError: %s! Giving up on that message.', telegram_err.message)

    def _send_msg(self, msg: str, parse_mode: str = ParseMode.MARKDOWN,
                  disable_notification: bool = False,
                  keyboard: List[List[InlineKeyboardButton]] = None,
                  callback_path: str = "",
                  reload_able: bool = False,
                  query: Optional[CallbackQuery] = None) -> None:
        """
        Send given markdown message
        :param msg: message
        :param bot: alternative bot
        :param parse_mode: telegram parse mode
        :return: None
        """
        reply_markup: Union[InlineKeyboardMarkup, ReplyKeyboardMarkup]
        if query:
            self._update_msg(query=query, msg=msg, parse_mode=parse_mode,
                             callback_path=callback_path, reload_able=reload_able)
            return
        if reload_able and self._config['telegram'].get('reload', True):
            reply_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("Refresh", callback_data=callback_path)]])
        else:
            if keyboard is not None:
                reply_markup = InlineKeyboardMarkup(keyboard, resize_keyboard=True)
            else:
                reply_markup = ReplyKeyboardMarkup(self._keyboard, resize_keyboard=True)
        try:
            try:
                self._updater.bot.send_message(
                    self._config['telegram']['chat_id'],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
            except NetworkError as network_err:
                # Sometimes the telegram server resets the current connection,
                # if this is the case we send the message again.
                logger.warning(
                    'Telegram NetworkError: %s! Trying one more time.',
                    network_err.message
                )
                self._updater.bot.send_message(
                    self._config['telegram']['chat_id'],
                    text=msg,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_notification=disable_notification,
                )
        except TelegramError as telegram_err:
            logger.warning(
                'TelegramError: %s! Giving up on that message.',
                telegram_err.message
            )
