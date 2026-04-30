import json
import os
import requests

BASE_URL = os.getenv('BASEROW_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('BASEROW_TOKEN', 'DxYIq8BLJ0dTpHFDShfatGeCkNh7sTFA').strip()
BOT_KEY = os.getenv('TRADEBOT_BOT_KEY', 'tradebot-grid-paper')

TABLES = {
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


def req(method: str, path: str, *, params=None):
    headers = {'Authorization': f'Token {TOKEN}'}
    r = requests.request(method, f'{BASE_URL}{path}', headers=headers, params=params, timeout=30)
    r.raise_for_status()
    if not r.text:
        return None
    return r.json()


def list_rows(table_id: int, size=200):
    out = []
    page = 1
    while True:
        payload = req('GET', f'/api/database/rows/table/{table_id}/', params={'user_field_names': 'true', 'size': size, 'page': page})
        out.extend(payload.get('results', []))
        if not payload.get('next'):
            break
        page += 1
    return out


def delete_row(table_id: int, row_id: int):
    return req('DELETE', f'/api/database/rows/table/{table_id}/{row_id}/', params={'user_field_names': 'true'})


def is_placeholder_bot(row):
    return not row.get('bot_key') and str(row.get('name')).lower() == 'false'


def is_placeholder_generic(row):
    text = json.dumps(row).lower()
    return ('"bot": []' in text or '"bot": null' in text) and ('"false"' in text or 'null' in text)


def main():
    deleted = []

    bots = list_rows(TABLES['bots'])
    target_bots = [r for r in bots if r.get('bot_key') == BOT_KEY]
    keep_bot_ids = set()
    if target_bots:
        target_bots.sort(key=lambda r: r['id'])
        keep_bot_ids.add(target_bots[-1]['id'])
    for row in bots:
        if is_placeholder_bot(row) or (row.get('bot_key') == BOT_KEY and row['id'] not in keep_bot_ids):
            delete_row(TABLES['bots'], row['id'])
            deleted.append(('bots', row['id']))

    for name, table_id in TABLES.items():
        if name == 'bots':
            continue
        rows = list_rows(table_id)
        tradebot_rows = []
        placeholders = []
        for row in rows:
            bot_links = row.get('bot') or []
            bot_ids = {b.get('id') for b in bot_links if isinstance(b, dict)}
            if keep_bot_ids & bot_ids:
                tradebot_rows.append(row)
            elif is_placeholder_generic(row):
                placeholders.append(row)
        # delete obvious placeholders
        for row in placeholders:
            delete_row(table_id, row['id'])
            deleted.append((name, row['id']))
        # dedupe tradebot rows per table-specific key
        key_fn = {
            'runtime_snapshots': lambda r: r.get('snapshot_time'),
            'positions': lambda r: r.get('position_id'),
            'orders': lambda r: r.get('order_num'),
            'fills': lambda r: (r.get('fill_time'), json.dumps(r.get('order_id') or [])),
            'grid_levels': lambda r: (r.get('level_index'), json.dumps(r.get('side'))),
            'events': lambda r: (r.get('event_time'), json.dumps(r.get('event_type'))),
            'daily_stats': lambda r: r.get('day'),
            'strategy_configs': lambda r: r.get('config_name'),
            'alerts': lambda r: (r.get('opened_at'), r.get('summary')),
        }[name]
        seen = {}
        for row in sorted(tradebot_rows, key=lambda r: r['id']):
            k = key_fn(row)
            if k in seen:
                delete_row(table_id, row['id'])
                deleted.append((name, row['id']))
            else:
                seen[k] = row['id']

    print(json.dumps({'deleted': deleted, 'count': len(deleted), 'keep_bot_ids': list(keep_bot_ids)}, indent=2))


if __name__ == '__main__':
    main()
