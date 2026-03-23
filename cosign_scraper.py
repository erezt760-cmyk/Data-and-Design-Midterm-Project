#!/usr/bin/env python3
"""
COSIGN Instagram Scraper
Hits Instagram's internal API directly using session cookies.
Saves results to Supabase.
"""

import requests
import time
import json
import sys
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────
SUPABASE_URL = 'https://earxpawzerixfembhvxe.supabase.co'
SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImVhcnhwYXd6ZXJpeGZlbWJodnhlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzE5NzIyMjUsImV4cCI6MjA4NzU0ODIyNX0.Z7ojOjXpyYOe0AxahBRrJ_0aQOWxlMJioD64owUhLYU'
USER_ID = 'tal'

COOKIES = {
    'ps_n': '1',
    'datr': 'W1mBaUUsse2bgBcSMjnNFid4',
    'ds_user_id': '46089167270',
    'csrftoken': 'onSFhbz2x2Da5qTM87GtuLYpPV70D2fR',
    'ig_did': 'DE71787C-ADF8-4FD2-A008-564491BD6401',
    'ps_l': '1',
    'mid': 'aTedmQAEAAFSysC2oZ_Nm1gr_0hm',
    'sessionid': '46089167270%3Au1xLE9p2yeWZdP%3A11%3AAYi87SjV_KQ57fpQLJfPnjQbVJdDWhz3x2JoFpeFBg',
    'rur': '"NHA\\05446089167270\\0541803675749:01fefc4e5286cf4a219b9074fe4064d816905081df0c6d1c03744a9d7ab25a986260254c"',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': '*/*',
    'Accept-Language': 'en-US,en;q=0.9',
    'X-CSRFToken': 'onSFhbz2x2Da5qTM87GtuLYpPV70D2fR',
    'X-IG-App-ID': '936619743392459',
    'X-Requested-With': 'XMLHttpRequest',
    'Referer': 'https://www.instagram.com/',
}

# ── INSTAGRAM API ─────────────────────────────────────────────
def get_user_id(username):
    """Get Instagram user ID from username."""
    url = f'https://www.instagram.com/api/v1/users/web_profile_info/?username={username}'
    r = requests.get(url, cookies=COOKIES, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    return data['data']['user']['id']

def get_following(username, max_results=10000):
    """Fetch complete following list for a username."""
    print(f'  Getting user ID for @{username}...')
    user_id = get_user_id(username)
    print(f'  User ID: {user_id}')

    following = []
    next_max_id = None
    page = 0

    while True:
        page += 1
        url = f'https://www.instagram.com/api/v1/friendships/{user_id}/following/'
        params = {'count': 200}
        if next_max_id:
            params['max_id'] = next_max_id

        r = requests.get(url, cookies=COOKIES, headers=HEADERS, params=params)

        if r.status_code == 401:
            print('  ❌ Cookies expired — please refresh cookies')
            return None
        if r.status_code == 429:
            print('  ⏳ Rate limited — waiting 60s...')
            time.sleep(60)
            continue
        r.raise_for_status()

        data = r.json()
        users = data.get('users', [])
        following.extend(users)
        print(f'  Page {page}: got {len(users)} users (total: {len(following)})')

        next_max_id = data.get('next_max_id')
        if not next_max_id or len(following) >= max_results:
            break

        # Be polite — don't hammer Instagram
        time.sleep(1.5)

    return following

# ── SUPABASE ──────────────────────────────────────────────────
def supabase_request(method, path, data=None):
    """Make a request to Supabase REST API."""
    url = f'{SUPABASE_URL}/rest/v1/{path}'
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }
    r = requests.request(method, url, headers=headers, json=data)
    if not r.ok:
        print(f'  Supabase error {r.status_code}: {r.text[:200]}')
    return r

def get_watchlist():
    """Get all handles to scan."""
    r = supabase_request('GET', f'watchlist?select=handle&user_id=eq.{USER_ID}&order=created_at')
    return [row['handle'] for row in r.json()]

def upsert_follows(handle, users, scan_time):
    """Upsert follows to Supabase in batches."""
    if not users:
        return

    # Deduplicate
    seen = set()
    rows = []
    for u in users:
        uid = str(u.get('pk') or u.get('id') or u.get('username'))
        if not uid or uid in seen:
            continue
        seen.add(uid)
        rows.append({
            'handle': handle,
            'uid': uid,
            'user_id': USER_ID,
            'username': u.get('username', ''),
            'full_name': u.get('full_name', ''),
            'profile_pic_url': u.get('profile_pic_url', ''),
            'is_verified': u.get('is_verified', False),
            'is_private': u.get('is_private', False),
            'last_seen_at': scan_time,
        })

    print(f'  Upserting {len(rows)} unique users to Supabase...')
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i+batch_size]
        r = supabase_request(
            'POST',
            'snapshots?on_conflict=handle,uid,user_id',
            batch
        )
        # Need upsert header
        url = f'{SUPABASE_URL}/rest/v1/snapshots'
        headers = {
            'apikey': SUPABASE_KEY,
            'Authorization': f'Bearer {SUPABASE_KEY}',
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=minimal',
        }
        resp = requests.post(url, headers=headers, json=batch)
        if resp.ok:
            print(f'  ✓ Batch {i//batch_size + 1} saved ({len(batch)} rows)')
        else:
            print(f'  ✗ Batch error: {resp.status_code} {resp.text[:200]}')

def log_scan(handle, count, scan_time):
    """Log scan to scans table."""
    supabase_request('POST', 'scans', {
        'handle': handle,
        'follows_count': count,
        'scanned_at': scan_time,
        'user_id': USER_ID,
    })

# ── MAIN ──────────────────────────────────────────────────────
def scan_handle(handle):
    print(f'\n📡 Scanning @{handle}...')
    users = get_following(handle)
    if users is None:
        return False

    scan_time = datetime.utcnow().isoformat()
    upsert_follows(handle, users, scan_time)
    log_scan(handle, len(users), scan_time)
    print(f'  ✅ @{handle} done — {len(users)} follows scraped')
    return True

def main():
    if len(sys.argv) > 1:
        # Scan specific handles passed as arguments
        handles = sys.argv[1:]
    else:
        # Scan all handles in watchlist
        print('📋 Loading watchlist from Supabase...')
        handles = get_watchlist()
        print(f'   Found {len(handles)} handles: {handles}')

    if not handles:
        print('No handles to scan.')
        return

    for handle in handles:
        success = scan_handle(handle)
        if not success:
            print(f'  Skipping remaining handles due to auth error')
            break
        if len(handles) > 1:
            print(f'  Waiting 5s before next handle...')
            time.sleep(5)

    print('\n✅ All done!')

if __name__ == '__main__':
    main()
