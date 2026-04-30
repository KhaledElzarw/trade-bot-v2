import json
import os
from datetime import datetime, timezone

import requests

BASE_URL = os.getenv('BASEROW_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('BASEROW_TOKEN', 'DxYIq8BLJ0dTpHFDShfatGeCkNh7sTFA').strip()
BOT_KEY = os.getenv('TRADEBOT_BOT_KEY', 'tradebot-grid-paper')
BOT_NAME = os.getenv('TRADEBOT_BOT_NAME', 'Tradebot Grid Paper')
ENABLED = os.getenv('TRADEBOT_BASEROW_SYNC', '1').strip().lower() not in {'0', 'false', 'no', 'off'}

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


def q10(value):
    if value is None:
        return None
    return round(float(value), 10)


class BaserowSync:
    def __init__(self):
        self.enabled = ENABLED and bool(TOKEN)
        self.bot_row = None
        self._warned = False
        self.snapshot_row_id = None

    def _now_iso(self):
        return datetime.now(timezone.utc).isoformat()

    def _req(self, method: str, path: str, *, params=None, body=None):
        headers = {'Authorization': f'Token {TOKEN}'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        r = requests.request(method, f'{BASE_URL}{path}', headers=headers, params=params, json=body, timeout=5)
        r.raise_for_status()
        if not r.text:
            return None
        return r.json()

    def _list_rows(self, table_id: int, size=200):
        out = []
        page = 1
        while True:
            payload = self._req('GET', f'/api/database/rows/table/{table_id}/', params={'user_field_names': 'true', 'size': size, 'page': page})
            out.extend(payload.get('results', []))
            if not payload.get('next'):
                break
            page += 1
        return out

    def _create_row(self, table_id: int, body: dict):
        return self._req('POST', f'/api/database/rows/table/{table_id}/', params={'user_field_names': 'true'}, body=body)

    def _update_row(self, table_id: int, row_id: int, body: dict):
        return self._req('PATCH', f'/api/database/rows/table/{table_id}/{row_id}/', params={'user_field_names': 'true'}, body=body)

    def _upsert(self, table: str, match_field: str, match_value, body: dict):
        table_id = TABLE_IDS[table]
        for row in self._list_rows(table_id):
            if row.get(match_field) == match_value:
                return self._update_row(table_id, row['id'], body)
        return self._create_row(table_id, body)

    def _delete_row(self, table: str, row_id: int):
        return self._req('DELETE', f'/api/database/rows/table/{TABLE_IDS[table]}/{row_id}/', params={'user_field_names': 'true'})

    def _ensure_bot(self, state, symbol):
        body = {
            'bot_key': BOT_KEY,
            'name': BOT_NAME,
            'bot_mode': state.get('mode', 'paper'),
            'exchange': 'binance',
            'symbol': symbol,
            'base_asset': symbol[:-4],
            'quote_asset': symbol[-4:],
            'strategy': 'grid',
            'timeframe': state.get('interval', '1m'),
            'status': 'active' if not state.get('paused', False) else 'paused',
            'created_at': self._now_iso(),
            'updated_at': self._now_iso(),
        }
        self.bot_row = self._upsert('bots', 'bot_key', BOT_KEY, body)
        return self.bot_row

    def sync_tick(self, *, state, status_payload, runtime_payload, cumulative_payload):
        if not self.enabled:
            return
        try:
            symbol = status_payload.get('symbol', state.get('symbol', 'BTCUSDT'))
            bot = self._ensure_bot(state, symbol)
            bot_link = [bot['id']]
            pos = status_payload.get('position') or {}
            snap_time = status_payload.get('tsUtc') or self._now_iso()
            snap_body = {
                'bot': bot_link,
                'snapshot_time': snap_time,
                'equity_usdt': q10(status_payload.get('equityUsdt', 0.0)),
                'cash_usdt': q10(status_payload.get('usdt', 0.0)),
                'asset_qty': q10(status_payload.get('btc', 0.0)),
                'asset_value_usdt': q10((status_payload.get('btc', 0.0) or 0.0) * (status_payload.get('price', 0.0) or 0.0)),
                'position_side': 'long' if pos.get('qtyBtc') else 'flat',
                'position_qty': q10(pos.get('qtyBtc', 0.0) or 0.0),
                'position_entry_price': q10(pos.get('entryPrice', 0.0) or 0.0),
                'unrealized_pnl_usdt': q10(pos.get('unrealizedPnlUsdt', 0.0) or 0.0),
                'unrealized_pnl_pct': q10(pos.get('unrealizedPnlPct', 0.0) or 0.0),
                'realized_pnl_usdt': q10(cumulative_payload.get('realizedPnlUsdt', 0.0)),
                'fees_total_usdt': q10(cumulative_payload.get('feesPaidUsdt', 0.0)),
                'drawdown_pct': q10(status_payload.get('stats', {}).get('maxDrawdownPct', 0.0)),
                'heartbeat_age_sec': q10(0),
                'engine_state': 'running',
                'raw_payload': json.dumps({'status': status_payload, 'runtime': runtime_payload}),
                'price': q10(status_payload.get('price', 0.0)),
                'symbol': symbol,
            }
            if self.snapshot_row_id is None:
                existing_snaps = [r for r in self._list_rows(TABLE_IDS['runtime_snapshots']) if any((b.get('id') == bot['id']) for b in (r.get('bot') or []))]
                if existing_snaps:
                    existing_snaps.sort(key=lambda r: r['id'])
                    self.snapshot_row_id = existing_snaps[-1]['id']
            if self.snapshot_row_id is not None:
                self._update_row(TABLE_IDS['runtime_snapshots'], self.snapshot_row_id, snap_body)
            else:
                snap_row = self._create_row(TABLE_IDS['runtime_snapshots'], snap_body)
                self.snapshot_row_id = snap_row['id']
            if pos.get('qtyBtc'):
                position_id = f"{BOT_KEY}:{pos.get('entryTimeUtc')}"
                self._upsert('positions', 'position_id', position_id, {
                    'position_id': position_id,
                    'bot': bot_link,
                    'position_key': position_id,
                    'symbol': symbol,
                    'side': 'long',
                    'status': 'open',
                    'entry_time': pos.get('entryTimeUtc'),
                    'entry_price': q10(pos.get('entryPrice', 0.0) or 0.0),
                    'qty': q10(pos.get('qtyBtc', 0.0) or 0.0),
                    'gross_pnl_usdt': q10(cumulative_payload.get('realizedPnlUsdt', 0.0)),
                    'net_pnl_usdt': q10(cumulative_payload.get('realizedPnlUsdt', 0.0) - cumulative_payload.get('feesPaidUsdt', 0.0)),
                    'pnl_pct': q10(pos.get('unrealizedPnlPct', 0.0) or 0.0),
                    'fees_usdt': q10(cumulative_payload.get('feesPaidUsdt', 0.0)),
                    'stop_price': q10(pos.get('stop', 0.0) or 0.0),
                    'take_profit_price': q10(pos.get('tp') or 0.0),
                    'notes': 'live runtime sync',
                })
            grid = runtime_payload.get('grid') or {}
            orders = grid.get('orders') or []
            current_order_nums = set()
            current_grid_keys = set()
            existing_orders = [r for r in self._list_rows(TABLE_IDS['orders']) if any((b.get('id') == bot['id']) for b in (r.get('bot') or []))]
            existing_grid_levels = [r for r in self._list_rows(TABLE_IDS['grid_levels']) if any((b.get('id') == bot['id']) for b in (r.get('bot') or []))]
            for idx, order in enumerate(orders, start=1):
                order_num = f'grid-{idx}'
                current_order_nums.add(order_num)
                side_lower = order['side'].lower()
                current_grid_keys.add((idx, side_lower))
                order_row = self._upsert('orders', 'order_num', order_num, {
                    'bot': bot_link,
                    'order_num': order_num,
                    'client_order_id': order_num,
                    'symbol': symbol,
                    'side': side_lower,
                    'order_type': 'limit',
                    'status': 'open',
                    'price': q10(order['price']),
                    'avg_fill_price': q10(0),
                    'qty': q10(order['qty_btc']),
                    'filled_qty': q10(0),
                    'remaining_qty': q10(order['qty_btc']),
                    'notional_usdt': q10(order['qty_btc'] * order['price']),
                    'reduce_only': False,
                    'post_only': False,
                    'time_in_force': 'GTC',
                    'created_at': snap_time,
                    'updated_at': self._now_iso(),
                    'raw_payload': json.dumps(order),
                })
                grid_match = None
                for row in existing_grid_levels:
                    row_side = str((row.get('side') or {}).get('value') if isinstance(row.get('side'), dict) else row.get('side')).lower()
                    row_idx = int(float(row.get('level_index') or 0)) if row.get('level_index') is not None else None
                    if row_idx == idx and row_side == side_lower:
                        grid_match = row
                        break
                grid_body = {
                    'bot': bot_link,
                    'level_index': idx,
                    'side': side_lower,
                    'price': q10(order['price']),
                    'qty': q10(order['qty_btc']),
                    'notional_usdt': q10(order['qty_btc'] * order['price']),
                    'status': 'open',
                    'linked_order': [order_row['id']],
                    'created_at': snap_time,
                    'updated_at': self._now_iso(),
                }
                if grid_match:
                    self._update_row(TABLE_IDS['grid_levels'], grid_match['id'], grid_body)
                else:
                    self._create_row(TABLE_IDS['grid_levels'], grid_body)
            for row in existing_orders:
                if row.get('order_num') and row.get('order_num') not in current_order_nums:
                    self._delete_row('orders', row['id'])
            for row in existing_grid_levels:
                row_side = str((row.get('side') or {}).get('value') if isinstance(row.get('side'), dict) else row.get('side')).lower()
                row_idx = int(float(row.get('level_index') or 0)) if row.get('level_index') is not None else None
                if (row_idx, row_side) not in current_grid_keys:
                    self._delete_row('grid_levels', row['id'])
            self._upsert('daily_stats', 'day', cumulative_payload.get('sinceUtc', snap_time)[:10], {
                'bot': bot_link,
                'day': cumulative_payload.get('sinceUtc', snap_time)[:10],
                'trades_count': q10(cumulative_payload.get('trades', 0)),
                'wins': q10(cumulative_payload.get('wins', 0)),
                'losses': q10(cumulative_payload.get('losses', 0)),
                'win_rate_pct': q10((100.0 * cumulative_payload.get('wins', 0) / cumulative_payload.get('trades', 1)) if cumulative_payload.get('trades', 0) else 0.0),
                'gross_pnl_usdt': q10(cumulative_payload.get('realizedPnlUsdt', 0.0)),
                'net_pnl_usdt': q10(cumulative_payload.get('realizedPnlUsdt', 0.0) - cumulative_payload.get('feesPaidUsdt', 0.0)),
                'fees_usdt': q10(cumulative_payload.get('feesPaidUsdt', 0.0)),
                'best_trade_usdt': q10(0),
                'worst_trade_usdt': q10(0),
                'volume_usdt': q10(0),
                'ending_equity_usdt': q10(status_payload.get('equityUsdt', 0.0)),
                'max_drawdown_pct': q10(status_payload.get('stats', {}).get('maxDrawdownPct', 0.0)),
                'notes': 'live runtime sync',
            })
            self._upsert('strategy_configs', 'config_name', 'current', {
                'bot': bot_link,
                'config_name': 'current',
                'config_version': 'v1',
                'active': True,
                'paper_limit_slip_bps': q10(state.get('paperLimitSlipBps', 3.0)),
                'paper_market_slip_bps': q10(state.get('paperMarketSlipBps', 12.0)),
                'fee_bps': q10(state.get('feeBps', 10)),
                'grid_spacing_pct': q10(state.get('gridSpacingPct', 0.008)),
                'grid_levels_count': q10(state.get('gridLevels', 12)),
                'stop_loss_pct': q10(0),
                'take_profit_pct': q10(0),
                'max_position_usdt': q10(state.get('paperStartUsdt', 500.0) * state.get('positionCapPct', 0.3)),
                'config_json': json.dumps(state),
                'created_at': snap_time,
                'updated_at': self._now_iso(),
            })
        except Exception as e:
            if not self._warned:
                print(f'[BaserowSync] sync_tick failed: {e}', flush=True)
                self._warned = True

    def sync_event(self, *, state, event, cumulative_payload):
        if not self.enabled:
            return
        try:
            symbol = event.get('symbol', state.get('symbol', 'BTCUSDT'))
            bot = self._ensure_bot(state, symbol)
            bot_link = [bot['id']]
            ts = event.get('tsUtc') or self._now_iso()
            key = f"{ts}|{event.get('event')}|{event.get('reason', '')}"
            self._upsert('events', 'raw_payload', json.dumps({'event_key': key}), {
                'bot': bot_link,
                'event_time': ts,
                'event_type': {
                    'ENGINE_START': 'info',
                    'ENTER': 'enter',
                    'EXIT': 'exit',
                    'GRID_INIT': 'grid_init',
                }.get(event.get('event', ''), 'info'),
                'title': event.get('event', 'INFO'),
                'price': q10(event.get('price', 0.0) or 0.0),
                'position_qty': q10(event.get('qtyBtc', 0.0) or 0.0),
                'message': json.dumps(event),
                'raw_payload': json.dumps({'event_key': key}),
                'severity': 'info',
            })
        except Exception as e:
            if not self._warned:
                print(f'[BaserowSync] sync_event failed: {e}', flush=True)
                self._warned = True
