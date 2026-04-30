import os
import requests

BASE_URL = os.getenv('BASEROW_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('BASEROW_TOKEN', 'DxYIq8BLJ0dTpHFDShfatGeCkNh7sTFA').strip()
BOT_ID = 3
TABLE_IDS = {
    'orders': 761,
    'grid_levels': 763,
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


def main():
    deleted = []
    orders = [r for r in list_rows(TABLE_IDS['orders']) if any((b.get('id') == BOT_ID) for b in (r.get('bot') or []))]
    seen_order_nums = set()
    for row in sorted(orders, key=lambda r: r['id'], reverse=True):
        order_num = row.get('order_num')
        if order_num in seen_order_nums:
            delete_row(TABLE_IDS['orders'], row['id'])
            deleted.append(('orders', row['id']))
        else:
            seen_order_nums.add(order_num)

    grid_rows = [r for r in list_rows(TABLE_IDS['grid_levels']) if any((b.get('id') == BOT_ID) for b in (r.get('bot') or []))]
    seen_grid = set()
    for row in sorted(grid_rows, key=lambda r: r['id'], reverse=True):
        side = str((row.get('side') or {}).get('value') if isinstance(row.get('side'), dict) else row.get('side')).lower()
        idx = int(float(row.get('level_index') or 0)) if row.get('level_index') is not None else None
        key = (idx, side)
        if key in seen_grid:
            delete_row(TABLE_IDS['grid_levels'], row['id'])
            deleted.append(('grid_levels', row['id']))
        else:
            seen_grid.add(key)

    print({'deleted': deleted, 'count': len(deleted)})


if __name__ == '__main__':
    main()
