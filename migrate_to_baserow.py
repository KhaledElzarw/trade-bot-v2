import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE_URL = os.getenv('BASEROW_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('BASEROW_TOKEN', 'DxYIq8BLJ0dTpHFDShfatGeCkNh7sTFA').strip()
DATABASE_ID = int(os.getenv('BASEROW_DATABASE_ID', '200'))
BOT_KEY = os.getenv('TRADEBOT_BOT_KEY', 'tradebot-grid-paper')
BOT_NAME = os.getenv('TRADEBOT_BOT_NAME', 'Tradebot Grid Paper')

HERE = Path(__file__).resolve().parent

TABLE_IDS = {
    'bots': 758,
    'runtime_snapshots': 759,
    'positions': 760,
    'orders': 761,
    'fills': 762,
    'grid_levels': 763,
    'events': 764,
    'daily_stats': 765,
    'strategy_configs': 766,
    'alerts': 767,
}

RUN_TAG = os.getenv('TRADEBOT_MIGRATION_RUN_TAG', 'tradebot-migration-v1')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def q10(value):
    if value is None:
        return None
    return round(float(value), 10)


def req(method: str, path: str, *, params=None, body=None):
    headers = {'Authorization': f'Token {TOKEN}'}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    r = requests.request(method, f'{BASE_URL}{path}', headers=headers, params=params, json=body, timeout=30)
    r.raise_for_status()
    if not r.text:
        return None
    return r.json()


def list_rows(table_id: int, size=200):
    out = []
    page = 1
    while True:
        payload = req('GET', f'/api/database/rows/table/{table_id}/', params={'user_field_names': 'true', 'size': size, 'page': page})
        results = payload.get('results', [])
        out.extend(results)
        if not payload.get('next'):
            break
        page += 1
    return out


def create_row(table_id: int, body: dict):
    return req('POST', f'/api/database/rows/table/{table_id}/', params={'user_field_names': 'true'}, body=body)


def update_row(table_id: int, row_id: int, body: dict):
    return req('PATCH', f'/api/database/rows/table/{table_id}/{row_id}/', params={'user_field_names': 'true'}, body=body)


def upsert_by_field(table: str, field: str, value, body: dict, *, placeholder_pred=None):
    table_id = TABLE_IDS[table]
    rows = list_rows(table_id)
    for row in rows:
        if row.get(field) == value:
            return update_row(table_id, row['id'], body), 'updated'
    if placeholder_pred is not None:
        placeholders = [r for r in rows if placeholder_pred(r)]
        if placeholders:
            placeholders.sort(key=lambda r: r['id'])
            return update_row(table_id, placeholders[0]['id'], body), 'updated-placeholder'
    return create_row(table_id, body), 'created'


def read_json(name: str):
    with open(HERE / name, 'r', encoding='utf-8') as f:
        return json.load(f)


def read_jsonl(name: str):
    path = HERE / name
    if not path.exists():
        return []
    out = []
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def ensure_bot(state, engine_status):
    body = {
        'bot_key': BOT_KEY,
        'name': BOT_NAME,
        'bot_mode': state.get('mode', engine_status.get('mode', 'paper')),
        'exchange': 'binance',
        'symbol': state.get('symbol', engine_status.get('symbol', 'BTCUSDT')),
        'base_asset': state.get('symbol', 'BTCUSDT')[:-4],
        'quote_asset': state.get('symbol', 'BTCUSDT')[-4:],
        'strategy': 'grid',
        'timeframe': state.get('interval', engine_status.get('interval', '1m')),
        'status': 'active' if not state.get('paused', False) else 'paused',
        'created_at': engine_status.get('tsUtc') or now_iso(),
        'updated_at': now_iso(),
    }
    row, action = upsert_by_field('bots', 'bot_key', BOT_KEY, body, placeholder_pred=lambda r: not r.get('bot_key') and str(r.get('name')).lower() == 'false')
    return row, action


def migrate_runtime(bot_row, state, runtime_state, engine_status, cumulative, trades):
    summary = {'created': 0, 'updated': 0, 'updated-placeholder': 0}
    bot_link = [bot_row['id']]
    pos = engine_status.get('position') or {}
    snapshot_time = engine_status.get('tsUtc') or now_iso()
    runtime_payload = {
        'bot': bot_link,
        'snapshot_time': snapshot_time,
        'equity_usdt': q10(engine_status.get('equityUsdt', 0.0)),
        'cash_usdt': q10(engine_status.get('usdt', 0.0)),
        'asset_qty': q10(engine_status.get('btc', 0.0)),
        'asset_value_usdt': q10((engine_status.get('btc', 0.0) or 0.0) * (engine_status.get('price', 0.0) or 0.0)),
        'position_side': 'long' if pos.get('qtyBtc') else 'flat',
        'position_qty': q10(pos.get('qtyBtc', 0.0) or 0.0),
        'position_entry_price': q10(pos.get('entryPrice', 0.0) or 0.0),
        'unrealized_pnl_usdt': q10(pos.get('unrealizedPnlUsdt', 0.0) or 0.0),
        'unrealized_pnl_pct': q10(pos.get('unrealizedPnlPct', 0.0) or 0.0),
        'realized_pnl_usdt': q10(cumulative.get('realizedPnlUsdt', 0.0)),
        'fees_total_usdt': q10(cumulative.get('feesPaidUsdt', 0.0)),
        'drawdown_pct': q10(engine_status.get('stats', {}).get('maxDrawdownPct', 0.0)),
        'heartbeat_age_sec': 0,
        'engine_state': 'running',
        'raw_payload': json.dumps({'engine_status': engine_status, 'runtime_state': runtime_state}),
        'price': q10(engine_status.get('price', 0.0)),
        'symbol': engine_status.get('symbol', state.get('symbol', 'BTCUSDT')),
    }
    _, action = upsert_by_field('runtime_snapshots', 'snapshot_time', snapshot_time, runtime_payload, placeholder_pred=lambda r: not r.get('snapshot_time') and not r.get('bot'))
    summary[action] += 1

    position_id = f"{BOT_KEY}:{pos.get('entryTimeUtc', 'flat')}"
    position_payload = {
        'position_id': position_id,
        'bot': bot_link,
        'position_key': position_id,
        'symbol': engine_status.get('symbol', state.get('symbol', 'BTCUSDT')),
        'side': 'long',
        'status': 'open' if pos.get('qtyBtc') else 'closed',
        'entry_time': pos.get('entryTimeUtc') or snapshot_time,
        'exit_time': None,
        'entry_price': q10(pos.get('entryPrice', 0.0) or 0.0),
        'exit_price': q10(0.0),
        'qty': q10(pos.get('qtyBtc', 0.0) or 0.0),
        'gross_pnl_usdt': q10(cumulative.get('realizedPnlUsdt', 0.0)),
        'net_pnl_usdt': q10(cumulative.get('realizedPnlUsdt', 0.0) - cumulative.get('feesPaidUsdt', 0.0)),
        'pnl_pct': q10(pos.get('unrealizedPnlPct', 0.0) or 0.0),
        'fees_usdt': q10(cumulative.get('feesPaidUsdt', 0.0)),
        'stop_price': q10(pos.get('stop', 0.0) or 0.0),
        'take_profit_price': q10(pos.get('tp') or 0.0),
        'close_reason': '',
        'notes': 'Derived from runtime_state + engine_status',
    }
    position_row, action = upsert_by_field('positions', 'position_id', position_id, position_payload, placeholder_pred=lambda r: (not r.get('position_id')) and (not r.get('bot')))
    summary[action] += 1

    grid = runtime_state.get('grid') or {}
    all_orders = list_rows(TABLE_IDS['orders'])
    all_grids = list_rows(TABLE_IDS['grid_levels'])
    existing_orders = {r.get('order_num'): r for r in all_orders if r.get('order_num')}
    existing_grids = {str(r.get('level_index')) + ':' + str(r.get('side')): r for r in all_grids if r.get('level_index') is not None and r.get('side') is not None}
    placeholder_orders = sorted([r for r in all_orders if not r.get('order_num') and not r.get('bot')], key=lambda r: r['id'])
    placeholder_grids = sorted([r for r in all_grids if r.get('level_index') is None and not r.get('bot')], key=lambda r: r['id'])

    for idx, order in enumerate(grid.get('orders', []), start=1):
        order_num = f"grid-{idx}"
        body = {
            'bot': bot_link,
            'position': [position_row['id']] if position_row else [],
            'order_num': order_num,
            'client_order_id': order_num,
            'symbol': engine_status.get('symbol', state.get('symbol', 'BTCUSDT')),
            'side': order['side'].lower(),
            'order_type': 'limit',
            'status': 'open',
            'price': q10(order['price']),
            'avg_fill_price': q10(0.0),
            'qty': q10(order['qty_btc']),
            'filled_qty': q10(0.0),
            'remaining_qty': q10(order['qty_btc']),
            'notional_usdt': q10(order['qty_btc'] * order['price']),
            'reduce_only': False,
            'post_only': False,
            'time_in_force': 'GTC',
            'created_at': snapshot_time,
            'updated_at': now_iso(),
            'filled_at': None,
            'raw_payload': json.dumps(order),
        }
        if order_num in existing_orders:
            update_row(TABLE_IDS['orders'], existing_orders[order_num]['id'], body)
            summary['updated'] += 1
            order_row = existing_orders[order_num]
        elif placeholder_orders:
            order_row = update_row(TABLE_IDS['orders'], placeholder_orders.pop(0)['id'], body)
            summary['updated'] += 1
        else:
            order_row = create_row(TABLE_IDS['orders'], body)
            summary['created'] += 1
        key = f"{idx}:{order['side'].lower()}"
        grid_body = {
            'bot': bot_link,
            'level_index': idx,
            'side': order['side'].lower(),
            'price': q10(order['price']),
            'qty': q10(order['qty_btc']),
            'notional_usdt': q10(order['qty_btc'] * order['price']),
            'status': 'open',
            'linked_order': [order_row['id']],
            'created_at': snapshot_time,
            'updated_at': now_iso(),
        }
        if key in existing_grids:
            update_row(TABLE_IDS['grid_levels'], existing_grids[key]['id'], grid_body)
            summary['updated'] += 1
        elif placeholder_grids:
            update_row(TABLE_IDS['grid_levels'], placeholder_grids.pop(0)['id'], grid_body)
            summary['updated'] += 1
        else:
            create_row(TABLE_IDS['grid_levels'], grid_body)
            summary['created'] += 1

    all_events = list_rows(TABLE_IDS['events'])
    existing_events = {r.get('event_time') + '|' + str(r.get('event_type')): r for r in all_events if r.get('event_time') and r.get('event_type')}
    placeholder_events = sorted([r for r in all_events if not r.get('event_time') and not r.get('bot')], key=lambda r: r['id'])
    for ev in trades:
        ev_type = str(ev.get('event', 'info')).lower()
        mapped = {
            'engine_start': 'info',
            'enter': 'enter',
            'exit': 'exit',
            'grid_init': 'grid_init',
        }.get(ev_type, 'info')
        sev = 'error' if mapped == 'error' else 'info'
        et = ev.get('tsUtc') or snapshot_time
        key = et + '|' + mapped
        body = {
            'bot': bot_link,
            'event_time': et,
            'event_type': mapped,
            'title': ev.get('event', 'INFO'),
            'price': q10(ev.get('price', 0.0) or 0.0),
            'position_qty': q10(ev.get('qtyBtc', 0.0) or 0.0),
            'message': json.dumps(ev),
            'raw_payload': json.dumps(ev),
            'severity': sev,
        }
        if key in existing_events:
            update_row(TABLE_IDS['events'], existing_events[key]['id'], body)
            summary['updated'] += 1
        elif placeholder_events:
            update_row(TABLE_IDS['events'], placeholder_events.pop(0)['id'], body)
            summary['updated'] += 1
        else:
            create_row(TABLE_IDS['events'], body)
            summary['created'] += 1

    daily = {
        'bot': bot_link,
        'day': cumulative.get('sinceUtc', snapshot_time)[:10],
        'trades_count': cumulative.get('trades', 0),
        'wins': cumulative.get('wins', 0),
        'losses': cumulative.get('losses', 0),
        'win_rate_pct': q10((100.0 * cumulative.get('wins', 0) / cumulative.get('trades', 1)) if cumulative.get('trades', 0) else 0.0),
        'gross_pnl_usdt': q10(cumulative.get('realizedPnlUsdt', 0.0)),
        'net_pnl_usdt': q10(cumulative.get('realizedPnlUsdt', 0.0) - cumulative.get('feesPaidUsdt', 0.0)),
        'fees_usdt': q10(cumulative.get('feesPaidUsdt', 0.0)),
        'best_trade_usdt': q10(0.0),
        'worst_trade_usdt': q10(0.0),
        'volume_usdt': q10(sum((t.get('notionalUsdt', 0.0) or 0.0) for t in trades)),
        'ending_equity_usdt': q10(engine_status.get('equityUsdt', 0.0)),
        'max_drawdown_pct': q10(engine_status.get('stats', {}).get('maxDrawdownPct', 0.0)),
        'notes': 'Migrated from cumulative.json and trades.jsonl',
    }
    _, action = upsert_by_field('daily_stats', 'day', daily['day'], daily, placeholder_pred=lambda r: not r.get('day') and not r.get('bot'))
    summary[action] += 1

    cfg = {
        'bot': bot_link,
        'config_name': 'current',
        'config_version': 'v1',
        'active': True,
        'paper_limit_slip_bps': q10(0.0),
        'paper_market_slip_bps': q10(12.0),
        'fee_bps': q10(state.get('feeBps', 10)),
        'grid_spacing_pct': q10(state.get('gridSpacingPct', 0.008)),
        'grid_levels_count': state.get('gridLevels', 12),
        'stop_loss_pct': q10(0.0),
        'take_profit_pct': q10(0.0),
        'max_position_usdt': q10(state.get('paperStartUsdt', 500.0) * state.get('positionCapPct', 0.3)),
        'config_json': json.dumps(state),
        'created_at': snapshot_time,
        'updated_at': now_iso(),
    }
    _, action = upsert_by_field('strategy_configs', 'config_name', 'current', cfg, placeholder_pred=lambda r: str(r.get('config_name')).lower() == 'false' and not r.get('bot'))
    summary[action] += 1
    return summary


def regression_check(bot_row):
    checks = {}
    bot_id = bot_row['id']
    for table in TABLE_IDS:
        rows = list_rows(TABLE_IDS[table])
        if table == 'bots':
            checks[table] = len([r for r in rows if r.get('bot_key') == BOT_KEY])
        else:
            checks[table] = len([r for r in rows if any(x.get('id') == bot_id for x in (r.get('bot') or []))])
    return checks


def purge_tradebot_rows(bot_row):
    bot_id = bot_row['id']
    for table in ['runtime_snapshots', 'positions', 'orders', 'fills', 'grid_levels', 'events', 'daily_stats', 'strategy_configs', 'alerts']:
        table_id = TABLE_IDS[table]
        rows = list_rows(table_id)
        for row in rows:
            if any(x.get('id') == bot_id for x in (row.get('bot') or [])):
                req('DELETE', f'/api/database/rows/table/{table_id}/{row["id"]}/', params={'user_field_names': 'true'})


def main():
    if not TOKEN:
        raise RuntimeError('Missing BASEROW_TOKEN')
    state = read_json('state.json')
    runtime_state = read_json('runtime_state.json')
    engine_status = read_json('engine_status.json')
    cumulative = read_json('cumulative.json')
    trades = read_jsonl('trades.jsonl')
    bot_row, action = ensure_bot(state, engine_status)
    purge_tradebot_rows(bot_row)
    summary = migrate_runtime(bot_row, state, runtime_state, engine_status, cumulative, trades)
    checks = regression_check(bot_row)
    print(json.dumps({'bot_action': action, 'summary': summary, 'regression': checks}, indent=2))


if __name__ == '__main__':
    main()
