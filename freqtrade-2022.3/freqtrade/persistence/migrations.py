import logging
from typing import List

from sqlalchemy import inspect, text


logger = logging.getLogger(__name__)


def get_table_names_for_table(inspector, tabletype):
    return [t for t in inspector.get_table_names() if t.startswith(tabletype)]


def has_column(columns: List, searchname: str) -> bool:
    return len(list(filter(lambda x: x["name"] == searchname, columns))) == 1


def get_column_def(columns: List, column: str, default: str) -> str:
    return default if not has_column(columns, column) else column


def get_backup_name(tabs, backup_prefix: str):
    table_back_name = backup_prefix
    for i, table_back_name in enumerate(tabs):
        table_back_name = f'{backup_prefix}{i}'
        logger.debug(f'trying {table_back_name}')

    return table_back_name


def get_last_sequence_ids(engine, trade_back_name, order_back_name):
    order_id: int = None
    trade_id: int = None

    if engine.name == 'postgresql':
        with engine.begin() as connection:
            trade_id = connection.execute(text("select nextval('trades_id_seq')")).fetchone()[0]
            order_id = connection.execute(text("select nextval('orders_id_seq')")).fetchone()[0]
        with engine.begin() as connection:
            connection.execute(text(
                f"ALTER SEQUENCE orders_id_seq rename to {order_back_name}_id_seq_bak"))
            connection.execute(text(
                f"ALTER SEQUENCE trades_id_seq rename to {trade_back_name}_id_seq_bak"))
    return order_id, trade_id


def set_sequence_ids(engine, order_id, trade_id):

    if engine.name == 'postgresql':
        with engine.begin() as connection:
            if order_id:
                connection.execute(text(f"ALTER SEQUENCE orders_id_seq RESTART WITH {order_id}"))
            if trade_id:
                connection.execute(text(f"ALTER SEQUENCE trades_id_seq RESTART WITH {trade_id}"))


def migrate_trades_and_orders_table(
        decl_base, inspector, engine,
        trade_back_name: str, cols: List,
        order_back_name: str, cols_order: List):
    fee_open = get_column_def(cols, 'fee_open', 'fee')
    fee_open_cost = get_column_def(cols, 'fee_open_cost', 'null')
    fee_open_currency = get_column_def(cols, 'fee_open_currency', 'null')
    fee_close = get_column_def(cols, 'fee_close', 'fee')
    fee_close_cost = get_column_def(cols, 'fee_close_cost', 'null')
    fee_close_currency = get_column_def(cols, 'fee_close_currency', 'null')
    open_rate_requested = get_column_def(cols, 'open_rate_requested', 'null')
    close_rate_requested = get_column_def(cols, 'close_rate_requested', 'null')
    stop_loss = get_column_def(cols, 'stop_loss', '0.0')
    stop_loss_pct = get_column_def(cols, 'stop_loss_pct', 'null')
    initial_stop_loss = get_column_def(cols, 'initial_stop_loss', '0.0')
    initial_stop_loss_pct = get_column_def(cols, 'initial_stop_loss_pct', 'null')
    stoploss_order_id = get_column_def(cols, 'stoploss_order_id', 'null')
    stoploss_last_update = get_column_def(cols, 'stoploss_last_update', 'null')
    max_rate = get_column_def(cols, 'max_rate', '0.0')
    min_rate = get_column_def(cols, 'min_rate', 'null')
    sell_reason = get_column_def(cols, 'sell_reason', 'null')
    strategy = get_column_def(cols, 'strategy', 'null')
    buy_tag = get_column_def(cols, 'buy_tag', 'null')
    # If ticker-interval existed use that, else null.
    if has_column(cols, 'ticker_interval'):
        timeframe = get_column_def(cols, 'timeframe', 'ticker_interval')
    else:
        timeframe = get_column_def(cols, 'timeframe', 'null')

    open_trade_value = get_column_def(cols, 'open_trade_value',
                                      f'amount * open_rate * (1 + {fee_open})')
    close_profit_abs = get_column_def(
        cols, 'close_profit_abs',
        f"(amount * close_rate * (1 - {fee_close})) - {open_trade_value}")
    sell_order_status = get_column_def(cols, 'sell_order_status', 'null')
    amount_requested = get_column_def(cols, 'amount_requested', 'amount')

    # Schema migration necessary
    with engine.begin() as connection:
        connection.execute(text(f"alter table trades rename to {trade_back_name}"))

    with engine.begin() as connection:
        # drop indexes on backup table in new session
        for index in inspector.get_indexes(trade_back_name):
            if engine.name == 'mysql':
                connection.execute(text(f"drop index {index['name']} on {trade_back_name}"))
            else:
                connection.execute(text(f"drop index {index['name']}"))

    order_id, trade_id = get_last_sequence_ids(engine, trade_back_name, order_back_name)

    drop_orders_table(engine, order_back_name)

    # let SQLAlchemy create the schema as required
    decl_base.metadata.create_all(engine)

    # Copy data back - following the correct schema
    with engine.begin() as connection:
        connection.execute(text(f"""insert into trades
            (id, exchange, pair, is_open,
            fee_open, fee_open_cost, fee_open_currency,
            fee_close, fee_close_cost, fee_close_currency, open_rate,
            open_rate_requested, close_rate, close_rate_requested, close_profit,
            stake_amount, amount, amount_requested, open_date, close_date, open_order_id,
            stop_loss, stop_loss_pct, initial_stop_loss, initial_stop_loss_pct,
            stoploss_order_id, stoploss_last_update,
            max_rate, min_rate, sell_reason, sell_order_status, strategy, buy_tag,
            timeframe, open_trade_value, close_profit_abs
            )
        select id, lower(exchange), pair,
            is_open, {fee_open} fee_open, {fee_open_cost} fee_open_cost,
            {fee_open_currency} fee_open_currency, {fee_close} fee_close,
            {fee_close_cost} fee_close_cost, {fee_close_currency} fee_close_currency,
            open_rate, {open_rate_requested} open_rate_requested, close_rate,
            {close_rate_requested} close_rate_requested, close_profit,
            stake_amount, amount, {amount_requested}, open_date, close_date, open_order_id,
            {stop_loss} stop_loss, {stop_loss_pct} stop_loss_pct,
            {initial_stop_loss} initial_stop_loss,
            {initial_stop_loss_pct} initial_stop_loss_pct,
            {stoploss_order_id} stoploss_order_id, {stoploss_last_update} stoploss_last_update,
            {max_rate} max_rate, {min_rate} min_rate, {sell_reason} sell_reason,
            {sell_order_status} sell_order_status,
            {strategy} strategy, {buy_tag} buy_tag, {timeframe} timeframe,
            {open_trade_value} open_trade_value, {close_profit_abs} close_profit_abs
            from {trade_back_name}
            """))

    migrate_orders_table(engine, order_back_name, cols_order)
    set_sequence_ids(engine, order_id, trade_id)


def migrate_open_orders_to_trades(engine):
    with engine.begin() as connection:
        connection.execute(text("""
        insert into orders (ft_trade_id, ft_pair, order_id, ft_order_side, ft_is_open)
        select id ft_trade_id, pair ft_pair, open_order_id,
            case when close_rate_requested is null then 'buy'
            else 'sell' end ft_order_side, 1 ft_is_open
        from trades
        where open_order_id is not null
        union all
        select id ft_trade_id, pair ft_pair, stoploss_order_id order_id,
            'stoploss' ft_order_side, 1 ft_is_open
        from trades
        where stoploss_order_id is not null
        """))


def drop_orders_table(engine, table_back_name: str):
    # Drop and recreate orders table as backup
    # This drops foreign keys, too.

    with engine.begin() as connection:
        connection.execute(text(f"create table {table_back_name} as select * from orders"))
        connection.execute(text("drop table orders"))


def migrate_orders_table(engine, table_back_name: str, cols_order: List):

    ft_fee_base = get_column_def(cols_order, 'ft_fee_base', 'null')
    average = get_column_def(cols_order, 'average', 'null')

    # let SQLAlchemy create the schema as required
    with engine.begin() as connection:
        connection.execute(text(f"""
            insert into orders ( id, ft_trade_id, ft_order_side, ft_pair, ft_is_open, order_id,
            status, symbol, order_type, side, price, amount, filled, average, remaining,
            cost, order_date, order_filled_date, order_update_date, ft_fee_base)
            select id, ft_trade_id, ft_order_side, ft_pair, ft_is_open, order_id,
            status, symbol, order_type, side, price, amount, filled, {average} average, remaining,
            cost, order_date, order_filled_date, order_update_date, {ft_fee_base} ft_fee_base
            from {table_back_name}
            """))


def set_sqlite_to_wal(engine):
    if engine.name == 'sqlite' and str(engine.url) != 'sqlite://':
        # Set Mode to
        with engine.begin() as connection:
            connection.execute(text("PRAGMA journal_mode=wal"))


def check_migrate(engine, decl_base, previous_tables) -> None:
    """
    Checks if migration is necessary and migrates if necessary
    """
    inspector = inspect(engine)

    cols = inspector.get_columns('trades')
    cols_orders = inspector.get_columns('orders')
    tabs = get_table_names_for_table(inspector, 'trades')
    table_back_name = get_backup_name(tabs, 'trades_bak')
    order_tabs = get_table_names_for_table(inspector, 'orders')
    order_table_bak_name = get_backup_name(order_tabs, 'orders_bak')

    # Check if migration necessary
    # Migrates both trades and orders table!
    # if not has_column(cols, 'buy_tag'):
    if 'orders' not in previous_tables or not has_column(cols_orders, 'ft_fee_base'):
        logger.info(f"Running database migration for trades - "
                    f"backup: {table_back_name}, {order_table_bak_name}")
        migrate_trades_and_orders_table(
            decl_base, inspector, engine, table_back_name, cols, order_table_bak_name, cols_orders)

    if 'orders' not in previous_tables and 'trades' in previous_tables:
        logger.info('Moving open orders to Orders table.')
        migrate_open_orders_to_trades(engine)
    set_sqlite_to_wal(engine)
