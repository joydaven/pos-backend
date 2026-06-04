#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║          DS POS — Product Sync Analyzer & Sync Tool             ║
║  1) Analyze: Compare POS vs WooCommerce prices/stock            ║
║  2) Sync:    Push WooCommerce prices into POS database          ║
║  Results published to Google Sheets with conditional colors.    ║
╚══════════════════════════════════════════════════════════════════╝

Usage:
  sudo -u www-data /path/to/venv/bin/python3 analyze_product_sync.py
"""

import os
import sys
import time
import signal
import threading
from pathlib import Path
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
from collections import defaultdict

# ── Django bootstrap ────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'doctorsstudio.settings')
import django
django.setup()

import pymysql
import gspread
from google.oauth2.service_account import Credentials
from core.secrets import get_woo_db_config
from crm.models import Product, ProductVariation, ProductSimple, ProductInventory, InventoryLocation

# ── Google Sheets config from .env ──────────────────────────────
from django.conf import settings
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
GOOGLE_SERVICE_ACCOUNT_FILE = os.environ.get('GOOGLE_SERVICE_ACCOUNT_FILE', '')
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# ═══════════════════════════════════════════════════════════════
# ANSI Colors & Styling
# ═══════════════════════════════════════════════════════════════
class C:
    """ANSI color codes for terminal output."""
    RESET   = '\033[0m'
    BOLD    = '\033[1m'
    DIM     = '\033[2m'
    ITALIC  = '\033[3m'
    ULINE   = '\033[4m'
    # Foreground
    RED     = '\033[91m'
    GREEN   = '\033[92m'
    YELLOW  = '\033[93m'
    BLUE    = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN    = '\033[96m'
    WHITE   = '\033[97m'
    GRAY    = '\033[90m'
    ORANGE  = '\033[38;5;208m'
    PINK    = '\033[38;5;213m'
    LIME    = '\033[38;5;154m'
    # Background
    BG_RED    = '\033[41m'
    BG_GREEN  = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE   = '\033[44m'
    BG_CYAN   = '\033[46m'
    BG_GRAY   = '\033[100m'
    BG_WHITE  = '\033[107m'

def clr(text, *styles):
    """Apply multiple ANSI styles to text."""
    return ''.join(styles) + str(text) + C.RESET

def banner(text, color=C.CYAN, width=66):
    """Print a boxed banner."""
    border = '═' * (width - 2)
    print(f"\n{color}╔{border}╗{C.RESET}")
    padded = text.center(width - 4)
    print(f"{color}║ {C.BOLD}{padded}{C.RESET}{color} ║{C.RESET}")
    print(f"{color}╚{border}╝{C.RESET}")

def section(text, color=C.YELLOW):
    """Print a section header."""
    print(f"\n{color}{C.BOLD}{'─' * 60}{C.RESET}")
    print(f"{color}{C.BOLD}  {text}{C.RESET}")
    print(f"{color}{C.BOLD}{'─' * 60}{C.RESET}")

def stat_line(label, value, color=C.WHITE, indent=2):
    """Print a labeled stat."""
    pad = ' ' * indent
    print(f"{pad}{C.GRAY}{label}:{C.RESET} {color}{C.BOLD}{value}{C.RESET}")

def ok(text):     print(f"  {C.GREEN}✓{C.RESET} {text}")
def warn(text):   print(f"  {C.YELLOW}⚠{C.RESET} {text}")
def err(text):    print(f"  {C.RED}✗{C.RESET} {text}")
def info(text):   print(f"  {C.CYAN}ℹ{C.RESET} {text}")
def bullet(text): print(f"  {C.GRAY}•{C.RESET} {text}")

# ═══════════════════════════════════════════════════════════════
# Spinner Animation
# ═══════════════════════════════════════════════════════════════
class Spinner:
    """Animated spinner for long operations."""
    FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']

    def __init__(self, message='Working...'):
        self.message = message
        self._stop = threading.Event()
        self._thread = None

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f'\r  {C.CYAN}{frame}{C.RESET} {self.message}')
            sys.stdout.flush()
            i += 1
            time.sleep(0.08)
        sys.stdout.write(f'\r  {C.GREEN}✓{C.RESET} {self.message}\n')
        sys.stdout.flush()

    def __enter__(self):
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()

# ═══════════════════════════════════════════════════════════════
# Progress Bar
# ═══════════════════════════════════════════════════════════════
def progress_bar(current, total, width=40, label=''):
    """Render an inline progress bar."""
    if total == 0:
        pct = 100
    else:
        pct = int(current / total * 100)
    filled = int(width * current / max(total, 1))
    bar = f"{C.GREEN}{'█' * filled}{C.GRAY}{'░' * (width - filled)}{C.RESET}"
    sys.stdout.write(f'\r  {bar} {C.BOLD}{pct:3d}%{C.RESET} {C.DIM}{label}{C.RESET}')
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write('\n')

# ═══════════════════════════════════════════════════════════════
# Price Table Rendering
# ═══════════════════════════════════════════════════════════════
def price_str(val):
    """Format a price value for display."""
    if val is None or val == '' or val == 'None':
        return clr('—', C.DIM)
    try:
        d = Decimal(str(val))
        if d == 0:
            return clr('$0.00', C.DIM)
        return f'${d:,.2f}'
    except (InvalidOperation, ValueError, TypeError):
        return clr(str(val), C.DIM)

def arrow_diff(local_val, woo_val, is_mismatch):
    """Show local → woo with color if mismatch."""
    l = price_str(local_val)
    w = price_str(woo_val)
    if is_mismatch:
        return f"{C.RED}{l}{C.RESET} → {C.GREEN}{w}{C.RESET}"
    return f"{C.DIM}{l}{C.RESET}"

def safe_decimal(value):
    """Convert to Decimal safely, return None for empty/invalid."""
    if value is None or value == '' or value == 'None' or value == 0:
        return None
    try:
        d = Decimal(str(value).strip())
        return d
    except (InvalidOperation, ValueError, TypeError):
        return None

def prices_match(local_val, woo_val):
    """Compare two price values, treating None/0/'' as equivalent."""
    ld = safe_decimal(local_val)
    wd = safe_decimal(woo_val)
    if ld is None and wd is None:
        return True
    if ld is None or wd is None:
        # One is None and other isn't — if the non-None is 0 treat as match
        if ld is not None and ld == 0:
            return wd is None
        if wd is not None and wd == 0:
            return ld is None
        return False
    return ld == wd

# ═══════════════════════════════════════════════════════════════
# Database Connection
# ═══════════════════════════════════════════════════════════════
@contextmanager
def woo_db_connection():
    """Direct connection to WooCommerce Cloud SQL database."""
    woo_cfg = get_woo_db_config()
    conn = None
    try:
        conn = pymysql.connect(
            host=woo_cfg['db_host'],
            port=int(woo_cfg['db_port']),
            user=woo_cfg['db_username'],
            password=woo_cfg['db_password'],
            database=woo_cfg['db_name'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=15,
        )
        yield conn
    finally:
        if conn:
            conn.close()

# ═══════════════════════════════════════════════════════════════
# Data Fetchers
# ═══════════════════════════════════════════════════════════════
def fetch_woo_simple_prices(cursor, woo_ids):
    """Fetch prices for simple/bundle/subscription products directly from wp_postmeta."""
    if not woo_ids:
        return {}
    CHUNK = 500
    results = {}
    for start in range(0, len(woo_ids), CHUNK):
        chunk = woo_ids[start:start + CHUNK]
        placeholders = ','.join(['%s'] * len(chunk))
        query = f"""
            SELECT 
                p.ID as product_id,
                p.post_title as name,
                p.post_status,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_price' THEN pm.meta_value END), '') as price,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_regular_price' THEN pm.meta_value END), '') as regular_price,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_sale_price' THEN pm.meta_value END), '') as sale_price
            FROM wp_posts p
            LEFT JOIN wp_postmeta pm ON p.ID = pm.post_id
                AND pm.meta_key IN ('_price', '_regular_price', '_sale_price')
            WHERE p.ID IN ({placeholders})
            GROUP BY p.ID, p.post_title, p.post_status
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            results[row['product_id']] = row
    return results

def fetch_woo_variation_prices(cursor, parent_woo_ids):
    """Fetch variation prices from wp_postmeta for variable products."""
    if not parent_woo_ids:
        return {}
    CHUNK = 500
    results = defaultdict(list)
    for start in range(0, len(parent_woo_ids), CHUNK):
        chunk = parent_woo_ids[start:start + CHUNK]
        placeholders = ','.join(['%s'] * len(chunk))
        query = f"""
            SELECT 
                v.ID as variation_id,
                v.post_parent as parent_id,
                v.post_status,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_price' THEN pm.meta_value END), '') as price,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_regular_price' THEN pm.meta_value END), '') as regular_price,
                COALESCE(MAX(CASE WHEN pm.meta_key = '_sale_price' THEN pm.meta_value END), '') as sale_price
            FROM wp_posts v
            LEFT JOIN wp_postmeta pm ON v.ID = pm.post_id
                AND pm.meta_key IN ('_price', '_regular_price', '_sale_price')
            WHERE v.post_parent IN ({placeholders})
              AND v.post_type = 'product_variation'
              AND v.post_status IN ('publish', 'private')
            GROUP BY v.ID, v.post_parent, v.post_status
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            results[row['parent_id']].append(row)
    return results

def fetch_woo_product_statuses(cursor, woo_ids):
    """Check which WooCommerce product IDs still exist and their status."""
    if not woo_ids:
        return {}
    CHUNK = 500
    results = {}
    for start in range(0, len(woo_ids), CHUNK):
        chunk = woo_ids[start:start + CHUNK]
        placeholders = ','.join(['%s'] * len(chunk))
        query = f"""
            SELECT ID as product_id, post_status, post_title as name
            FROM wp_posts
            WHERE ID IN ({placeholders}) AND post_type = 'product'
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            results[row['product_id']] = row
    return results

def fetch_atum_inventories(cursor, woo_ids):
    """Fetch ATUM multi-inventory stock for given product IDs.
    
    Schema:
      wp_atum_inventories:      id, product_id, name (location), is_main, ...
      wp_atum_inventory_meta:   inventory_id → stock_quantity, stock_status, manage_stock, price, ...
    """
    if not woo_ids:
        return {}
    # Process in chunks to avoid MySQL placeholder limits
    CHUNK = 500
    results = defaultdict(list)
    for start in range(0, len(woo_ids), CHUNK):
        chunk = woo_ids[start:start + CHUNK]
        placeholders = ','.join(['%s'] * len(chunk))
        query = f"""
            SELECT 
                ai.product_id,
                ai.name as location_name,
                ai.is_main,
                COALESCE(aim.stock_quantity, 0) as stock_quantity,
                COALESCE(aim.stock_status, '') as stock_status,
                COALESCE(aim.manage_stock, 0) as manage_stock,
                ai.inbound_stock,
                ai.stock_on_hold
            FROM wp_atum_inventories ai
            LEFT JOIN wp_atum_inventory_meta aim ON ai.id = aim.inventory_id
            WHERE ai.product_id IN ({placeholders})
            ORDER BY ai.product_id, ai.is_main DESC, ai.name
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            results[row['product_id']].append(row)
    return results

def fetch_atum_product_data(cursor, woo_ids):
    """Fetch ATUM product data (purchase price, stock status) for product IDs."""
    if not woo_ids:
        return {}
    CHUNK = 500
    results = {}
    for start in range(0, len(woo_ids), CHUNK):
        chunk = woo_ids[start:start + CHUNK]
        placeholders = ','.join(['%s'] * len(chunk))
        query = f"""
            SELECT product_id, purchase_price, atum_stock_status, multi_inventory, has_location
            FROM wp_atum_product_data
            WHERE product_id IN ({placeholders})
        """
        cursor.execute(query, chunk)
        for row in cursor.fetchall():
            results[row['product_id']] = row
    return results

# ═══════════════════════════════════════════════════════════════
# Category / Product helpers
# ═══════════════════════════════════════════════════════════════
def get_product_category_str(product):
    """Return a short category string for display."""
    if not product.categories:
        return ''
    names = []
    for c in product.categories:
        name = c if isinstance(c, str) else c.get('name', '')
        if name:
            names.append(name)
    return ', '.join(names[:2]) + (f', +{len(names)-2} more' if len(names) > 2 else '')

def get_variation_name(variation):
    """Get human-readable variation name from woo_data attributes."""
    if variation.woo_data and isinstance(variation.woo_data, dict):
        attrs = variation.woo_data.get('attributes', [])
        if attrs:
            return ' / '.join(a.get('option', '') for a in attrs if a.get('option'))
    return f'Variation #{variation.woo_variation_id}'

# ═══════════════════════════════════════════════════════════════
# Analysis Engine — builds flat row data for ALL products
# ═══════════════════════════════════════════════════════════════
def build_analysis_rows(conn):
    """Compare ALL published POS products vs WooCommerce DB.
    
    Returns a list of dicts, one per product/variation row:
      {name, woo_id, type, category, pos_price, woo_price, pos_stock, woo_stock,
       price_status, stock_status, overall_status, is_variation, parent_name, variation_name}
    """
    cursor = conn.cursor()
    products = list(Product.objects.filter(status='publish').order_by('name'))
    total = len(products)
    
    simple_products = [p for p in products if p.product_type in ('simple', 'bundle', 'subscription')]
    variable_products = [p for p in products if p.product_type in ('variable', 'variable_subscription')]
    
    # ── Fetch all WC prices in bulk ───────────────────────────
    all_simple_ids = [p.woo_product_id for p in simple_products]
    all_parent_ids = [p.woo_product_id for p in variable_products]
    
    woo_simple = fetch_woo_simple_prices(cursor, all_simple_ids)
    woo_var = fetch_woo_variation_prices(cursor, all_parent_ids)
    woo_statuses = fetch_woo_product_statuses(cursor, [p.woo_product_id for p in products])
    
    rows = []
    checked = 0
    
    # ── Simple / Bundle / Subscription ────────────────────────
    for p in simple_products:
        checked += 1
        progress_bar(checked, total, label=f'{p.name[:40]}')
        
        woo = woo_simple.get(p.woo_product_id)
        woo_status = woo_statuses.get(p.woo_product_id)
        
        pos_price = safe_decimal(p.price)
        woo_price = safe_decimal(woo['price']) if woo else None
        pos_stock = p.stock_quantity
        woo_stock = None  # main stock from postmeta if needed
        
        # Determine statuses
        if woo_status is None:
            price_st = 'DELETED'
            stock_st = 'DELETED'
            overall = 'DELETED'
        elif woo_status['post_status'] in ('trash', 'draft'):
            price_st = woo_status['post_status'].upper()
            stock_st = woo_status['post_status'].upper()
            overall = woo_status['post_status'].upper()
        else:
            price_st = 'MATCH' if prices_match(pos_price, woo_price) else 'MISMATCH'
            stock_st = ''  # stock handled separately for ATUM
            overall = price_st
        
        rows.append({
            'name': p.name,
            'woo_id': p.woo_product_id,
            'type': p.product_type,
            'category': get_product_category_str(p),
            'pos_price': _fmt_price(pos_price),
            'woo_price': _fmt_price(woo_price),
            'pos_stock': str(int(pos_stock)) if pos_stock is not None else '',
            'woo_stock': '',
            'price_status': price_st,
            'stock_status': stock_st,
            'overall_status': overall,
            'is_variation': False,
            'parent_name': '',
            'variation_name': '',
            '_product': p,
        })
    
    # ── Variable products — one row per variation ─────────────
    for p in variable_products:
        checked += 1
        progress_bar(checked, total, label=f'{p.name[:40]}')
        
        woo_status = woo_statuses.get(p.woo_product_id)
        woo_vars = woo_var.get(p.woo_product_id, [])
        woo_vars_by_id = {v['variation_id']: v for v in woo_vars}
        local_vars = list(ProductVariation.objects.filter(product=p))
        
        if not local_vars:
            # Variable product with no local variations — just add parent row
            pos_price = safe_decimal(p.price)
            rows.append({
                'name': p.name,
                'woo_id': p.woo_product_id,
                'type': p.product_type,
                'category': get_product_category_str(p),
                'pos_price': _fmt_price(pos_price),
                'woo_price': '',
                'pos_stock': str(int(p.stock_quantity)) if p.stock_quantity is not None else '',
                'woo_stock': '',
                'price_status': 'NO VARIATIONS',
                'stock_status': '',
                'overall_status': 'NO VARIATIONS',
                'is_variation': False,
                'parent_name': '',
                'variation_name': '',
                '_product': p,
            })
            continue
        
        for lv in local_vars:
            wv = woo_vars_by_id.get(lv.woo_variation_id)
            pos_price = safe_decimal(lv.price)
            woo_price = safe_decimal(wv['price']) if wv else None
            
            var_name = get_variation_name(lv)
            display_name = f"{p.name} — {var_name}"
            
            if woo_status is None:
                price_st = 'DELETED'
                overall = 'DELETED'
            elif woo_status['post_status'] in ('trash', 'draft'):
                price_st = woo_status['post_status'].upper()
                overall = woo_status['post_status'].upper()
            elif not wv:
                price_st = 'VAR MISSING IN WC'
                overall = 'MISMATCH'
            else:
                price_st = 'MATCH' if prices_match(pos_price, woo_price) else 'MISMATCH'
                overall = price_st
            
            rows.append({
                'name': display_name,
                'woo_id': lv.woo_variation_id or p.woo_product_id,
                'type': 'variation',
                'category': get_product_category_str(p),
                'pos_price': _fmt_price(pos_price),
                'woo_price': _fmt_price(woo_price),
                'pos_stock': str(int(lv.stock_quantity)) if hasattr(lv, 'stock_quantity') and lv.stock_quantity is not None else '',
                'woo_stock': '',
                'price_status': price_st,
                'stock_status': '',
                'overall_status': overall,
                'is_variation': True,
                'parent_name': p.name,
                'variation_name': var_name,
                '_product': p,
                '_variation': lv,
            })
    
    return rows

def _fmt_price(val):
    """Format a decimal value for spreadsheet (plain number, no $)."""
    if val is None:
        return ''
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return str(val)

# ═══════════════════════════════════════════════════════════════
# Sync Engine — update POS prices from WooCommerce
# ═══════════════════════════════════════════════════════════════
def sync_all(rows):
    """Sync everything:
      1. Update POS prices for MISMATCH rows (copy WC price → POS).
      2. Delete POS products that are DELETED/TRASHED/DRAFT in WooCommerce.
    Returns (price_synced, price_failed, deleted_count, delete_failed, updated_rows).
    """
    price_synced = 0
    price_failed = 0
    deleted_count = 0
    delete_failed = 0
    already_deleted_product_ids = set()  # track parent products already deleted (variations share parent)
    
    for row in rows:
        # ── 1. Price sync for MISMATCH rows ───────────────────
        if row['price_status'] == 'MISMATCH' and row['woo_price']:
            try:
                new_price = Decimal(row['woo_price'])
            except (InvalidOperation, ValueError):
                price_failed += 1
                continue
            
            try:
                if row['is_variation']:
                    v = row.get('_variation')
                    if v:
                        v.price = new_price
                        v.regular_price = new_price
                        v.save(update_fields=['price', 'regular_price'])
                        row['price_status'] = 'SYNCED'
                        row['overall_status'] = 'SYNCED'
                        row['pos_price'] = _fmt_price(new_price)
                        price_synced += 1
                    else:
                        price_failed += 1
                else:
                    p = row.get('_product')
                    if p:
                        p.price = new_price
                        p.regular_price = new_price
                        p.save(update_fields=['price', 'regular_price'])
                        row['price_status'] = 'SYNCED'
                        row['overall_status'] = 'SYNCED'
                        row['pos_price'] = _fmt_price(new_price)
                        price_synced += 1
                    else:
                        price_failed += 1
            except Exception as e:
                price_failed += 1
                row['price_status'] = f'FAILED: {str(e)[:30]}'
                row['overall_status'] = 'FAILED'
        
        # ── 2. Delete ghost products (DELETED/TRASHED/DRAFT) ──
        if row['overall_status'] in ('DELETED', 'TRASH', 'DRAFT'):
            p = row.get('_product')
            if not p:
                row['overall_status'] = 'REMOVED FROM POS'
                row['price_status'] = 'REMOVED FROM POS'
                continue
            
            # Check if this parent was already deleted (by a sibling variation row).
            # After Django .delete(), p.pk may become None, so we track by id(obj) too.
            pid = getattr(p, 'pk', None) or getattr(p, '_deleted_id', None)
            if pid in already_deleted_product_ids or id(p) in already_deleted_product_ids:
                row['overall_status'] = 'REMOVED FROM POS'
                row['price_status'] = 'REMOVED FROM POS'
                continue
            
            try:
                product_id = p.pk
                p.delete()  # CASCADE removes variations, inventory, bundles, etc.
                p._deleted_id = product_id  # stash for sibling rows
                already_deleted_product_ids.add(product_id)
                already_deleted_product_ids.add(id(p))  # also track by object identity
                row['overall_status'] = 'REMOVED FROM POS'
                row['price_status'] = 'REMOVED FROM POS'
                deleted_count += 1
            except Exception as e:
                delete_failed += 1
                row['overall_status'] = f'DELETE FAILED: {str(e)[:25]}'
    
    return price_synced, price_failed, deleted_count, delete_failed, rows

# ═══════════════════════════════════════════════════════════════
# Google Sheets — styled output
# ═══════════════════════════════════════════════════════════════
SHEET_HEADERS = [
    "Product Name",        # A=0
    "Woo ID",              # B=1
    "Type",                # C=2
    "Category",            # D=3
    "POS Price",           # E=4
    "WooCommerce Price",   # F=5
    "Price Status",        # G=6
    "Overall Status",      # H=7
]

HEADER_COLORS = {
    # (start_col_idx, end_col_idx): header_bg
    (0, 3): {"red": 0.20, "green": 0.20, "blue": 0.20},    # Identity: charcoal
    (4, 5): {"red": 0.10, "green": 0.27, "blue": 0.53},    # Price: navy blue
    (6, 6): {"red": 0.35, "green": 0.15, "blue": 0.50},    # Price Status: purple
    (7, 7): {"red": 0.25, "green": 0.25, "blue": 0.25},    # Overall: dark grey
}

DATA_TINTS = {
    (0, 3): {"red": 0.97, "green": 0.97, "blue": 0.97},    # near white
    (4, 5): {"red": 0.92, "green": 0.95, "blue": 1.00},    # light blue
    (6, 6): {"red": 0.96, "green": 0.92, "blue": 1.00},    # light purple
    (7, 7): {"red": 0.95, "green": 0.95, "blue": 0.95},    # light grey
}

def _col_letter(index):
    """0-indexed column number to letter(s). 0=A, 25=Z, 26=AA."""
    result = ""
    while True:
        result = chr(ord("A") + index % 26) + result
        index = index // 26 - 1
        if index < 0:
            break
    return result

def _get_sheets_client():
    """Authenticate and return a gspread client."""
    key_path = GOOGLE_SERVICE_ACCOUNT_FILE
    if not Path(key_path).is_absolute():
        key_path = str(Path(__file__).resolve().parent / key_path)
    creds = Credentials.from_service_account_file(key_path, scopes=SCOPES)
    return gspread.authorize(creds)

def _full_clear(sh, ws):
    """Nuke everything: values, formatting, conditional format rules, filters."""
    sheet_id = ws._properties["sheetId"]
    ws.clear()
    requests = []
    # Reset all cell formatting
    requests.append({
        "repeatCell": {
            "range": {"sheetId": sheet_id},
            "cell": {"userEnteredFormat": {}},
            "fields": "userEnteredFormat",
        }
    })
    # Remove basic filter
    requests.append({"clearBasicFilter": {"sheetId": sheet_id}})
    # Unfreeze
    requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": sheet_id,
                "gridProperties": {"frozenRowCount": 0, "frozenColumnCount": 0},
            },
            "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
        }
    })
    # Delete conditional format rules
    try:
        sheet_meta = sh.fetch_sheet_metadata()
        for s in sheet_meta.get("sheets", []):
            if s["properties"]["sheetId"] == sheet_id:
                cf_rules = s.get("conditionalFormats", [])
                for i in range(len(cf_rules) - 1, -1, -1):
                    requests.append({
                        "deleteConditionalFormatRule": {"sheetId": sheet_id, "index": i}
                    })
                break
    except Exception:
        pass
    if requests:
        sh.batch_update({"requests": requests})

def publish_to_sheet(rows):
    """Clear sheet and write fresh analysis/sync data with styling."""
    if not GOOGLE_SHEET_ID:
        err("GOOGLE_SHEET_ID not set in .env — skipping sheet update")
        return
    
    try:
        gc = _get_sheets_client()
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sh.sheet1
        
        ncols = len(SHEET_HEADERS)
        now_et = datetime.now(timezone(timedelta(hours=-5)))
        run_ts = now_et.strftime("%Y-%m-%d %I:%M %p ET")
        
        # Build data rows
        data_rows = []
        for r in rows:
            data_rows.append([
                r['name'],
                str(r['woo_id']),
                r['type'],
                r['category'],
                r['pos_price'],
                r['woo_price'],
                r['price_status'],
                r['overall_status'],
            ])
        
        all_rows = [SHEET_HEADERS] + data_rows
        all_rows = [[str(c) if c is not None else "" for c in row] for row in all_rows]
        
        # Clear and write
        _full_clear(sh, worksheet)
        worksheet.update(values=all_rows, range_name="A1", value_input_option="USER_ENTERED")
        
        last_col = _col_letter(ncols - 1)
        total_rows = len(all_rows)
        sheet_id = worksheet._properties["sheetId"]
        
        # ── Header formatting ─────────────────────────────────
        for (sc, ec), color in HEADER_COLORS.items():
            rng = f"{_col_letter(sc)}1:{_col_letter(ec)}1"
            worksheet.format(rng, {
                "backgroundColor": color,
                "textFormat": {"bold": True, "fontSize": 10,
                               "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                "horizontalAlignment": "CENTER",
            })
        
        # ── Data row tints ────────────────────────────────────
        if total_rows >= 2:
            for (sc, ec), color in DATA_TINTS.items():
                rng = f"{_col_letter(sc)}2:{_col_letter(ec)}{total_rows}"
                worksheet.format(rng, {
                    "backgroundColor": color,
                    "textFormat": {"fontSize": 10},
                })
            
            # Price columns: number format (E, F)
            for c in ["E", "F"]:
                worksheet.format(f"{c}2:{c}{total_rows}", {
                    "numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"},
                })
            
            # Status columns: center + bold (G, H)
            for c in ["G", "H"]:
                worksheet.format(f"{c}2:{c}{total_rows}", {
                    "horizontalAlignment": "CENTER",
                    "textFormat": {"bold": True, "fontSize": 10},
                })
        
        # Freeze header row + first column
        worksheet.freeze(rows=1, cols=1)
        
        # Auto-filter
        try:
            worksheet.set_basic_filter(f"A1:{last_col}{total_rows}")
        except Exception:
            pass
        
        # ── Conditional formatting ────────────────────────────
        rules = []
        start_row_idx = 1  # 0-indexed, data starts row 2 = index 1
        end_row_idx = total_rows
        
        def _bool_rule(col_idx, value, bg_color, text_color=None):
            fmt = {"backgroundColor": bg_color}
            if text_color:
                fmt["textFormat"] = {"foregroundColor": text_color, "bold": True}
            return {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": start_row_idx,
                            "endRowIndex": end_row_idx,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1,
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": value}],
                            },
                            "format": fmt,
                        },
                    },
                    "index": 0,
                }
            }
        
        # Row-level highlight for mismatches (highlight POS Price cell red when MISMATCH)
        def _row_formula_rule(formula, col_start, col_end, bg_color, text_fmt=None):
            fmt = {"backgroundColor": bg_color}
            if text_fmt:
                fmt["textFormat"] = text_fmt
            return {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": start_row_idx,
                            "endRowIndex": end_row_idx,
                            "startColumnIndex": col_start,
                            "endColumnIndex": col_end + 1,
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": formula}],
                            },
                            "format": fmt,
                        },
                    },
                    "index": 0,
                }
            }
        
        # Price Status column (G=6): MISMATCH=red, MATCH=green, SYNCED=blue, DELETED=dark
        rules.append(_bool_rule(6, "MISMATCH",
            {"red": 1.0, "green": 0.80, "blue": 0.80},
            {"red": 0.6, "green": 0.0, "blue": 0.0}))
        rules.append(_bool_rule(6, "MATCH",
            {"red": 0.85, "green": 0.95, "blue": 0.85},
            {"red": 0.0, "green": 0.4, "blue": 0.0}))
        rules.append(_bool_rule(6, "SYNCED",
            {"red": 0.72, "green": 0.88, "blue": 0.72},
            {"red": 0.0, "green": 0.3, "blue": 0.0}))
        rules.append(_bool_rule(6, "DELETED",
            {"red": 0.85, "green": 0.85, "blue": 0.85},
            {"red": 0.4, "green": 0.4, "blue": 0.4}))
        rules.append(_bool_rule(6, "TRASH",
            {"red": 0.85, "green": 0.85, "blue": 0.85},
            {"red": 0.4, "green": 0.4, "blue": 0.4}))
        rules.append(_bool_rule(6, "DRAFT",
            {"red": 0.90, "green": 0.90, "blue": 0.80},
            {"red": 0.45, "green": 0.45, "blue": 0.2}))
        
        # Overall Status column (H=7): same colors
        rules.append(_bool_rule(7, "MISMATCH",
            {"red": 1.0, "green": 0.80, "blue": 0.80},
            {"red": 0.6, "green": 0.0, "blue": 0.0}))
        rules.append(_bool_rule(7, "MATCH",
            {"red": 0.85, "green": 0.95, "blue": 0.85},
            {"red": 0.0, "green": 0.4, "blue": 0.0}))
        rules.append(_bool_rule(7, "SYNCED",
            {"red": 0.72, "green": 0.88, "blue": 0.72},
            {"red": 0.0, "green": 0.3, "blue": 0.0}))
        rules.append(_bool_rule(7, "DELETED",
            {"red": 0.85, "green": 0.85, "blue": 0.85},
            {"red": 0.4, "green": 0.4, "blue": 0.4}))
        rules.append(_bool_rule(7, "TRASH",
            {"red": 0.85, "green": 0.85, "blue": 0.85},
            {"red": 0.4, "green": 0.4, "blue": 0.4}))
        rules.append(_bool_rule(7, "DRAFT",
            {"red": 0.90, "green": 0.90, "blue": 0.80},
            {"red": 0.45, "green": 0.45, "blue": 0.2}))
        rules.append(_bool_rule(7, "FAILED",
            {"red": 1.0, "green": 0.70, "blue": 0.70},
            {"red": 0.5, "green": 0.0, "blue": 0.0}))
        
        # REMOVED FROM POS — orange bg on both status columns
        rules.append(_bool_rule(6, "REMOVED FROM POS",
            {"red": 1.0, "green": 0.87, "blue": 0.70},
            {"red": 0.55, "green": 0.30, "blue": 0.0}))
        rules.append(_bool_rule(7, "REMOVED FROM POS",
            {"red": 1.0, "green": 0.87, "blue": 0.70},
            {"red": 0.55, "green": 0.30, "blue": 0.0}))
        
        # Highlight entire row light orange when REMOVED FROM POS
        rules.append(_row_formula_rule(
            '=$H2="REMOVED FROM POS"', 0, ncols - 1,
            {"red": 1.0, "green": 0.94, "blue": 0.85}))
        
        # Highlight POS Price (E=4) red background when Price Status is MISMATCH
        rules.append(_row_formula_rule(
            '=$G2="MISMATCH"', 4, 4,
            {"red": 1.0, "green": 0.75, "blue": 0.75},
            {"bold": True}))
        
        # Highlight WC Price (F=5) green background when Price Status is MISMATCH
        rules.append(_row_formula_rule(
            '=$G2="MISMATCH"', 5, 5,
            {"red": 0.75, "green": 0.95, "blue": 0.75},
            {"bold": True}))
        
        # Highlight entire row light red/grey when DELETED/TRASH/DRAFT
        rules.append(_row_formula_rule(
            '=$H2="DELETED"', 0, ncols - 1,
            {"red": 0.95, "green": 0.87, "blue": 0.87}))
        rules.append(_row_formula_rule(
            '=$H2="TRASH"', 0, ncols - 1,
            {"red": 0.95, "green": 0.87, "blue": 0.87}))
        rules.append(_row_formula_rule(
            '=$H2="DRAFT"', 0, ncols - 1,
            {"red": 0.95, "green": 0.93, "blue": 0.85}))
        
        if rules:
            sh.batch_update({"requests": rules})
        
        # ── Auto-resize columns ───────────────────────────────
        try:
            sh.batch_update({"requests": [{
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": ncols,
                    }
                }
            }]})
            # Add padding
            meta = sh.fetch_sheet_metadata()
            for s in meta.get("sheets", []):
                if s["properties"]["sheetId"] == sheet_id:
                    cols = s.get("data", [{}])[0].get("columnMetadata", [])
                    pad_requests = []
                    for i, col in enumerate(cols[:ncols]):
                        cur = col.get("pixelSize", 100)
                        pad_requests.append({
                            "updateDimensionProperties": {
                                "range": {
                                    "sheetId": sheet_id,
                                    "dimension": "COLUMNS",
                                    "startIndex": i,
                                    "endIndex": i + 1,
                                },
                                "properties": {"pixelSize": cur + 40},
                                "fields": "pixelSize",
                            }
                        })
                    if pad_requests:
                        sh.batch_update({"requests": pad_requests})
                    break
        except Exception:
            pass
        
        return True
    
    except Exception as exc:
        import traceback
        err(f"Failed to update Google Sheet: {exc}")
        traceback.print_exc()
        return False

# ═══════════════════════════════════════════════════════════════
# CLI Display helpers
# ═══════════════════════════════════════════════════════════════
def display_analysis_summary(rows):
    """Print CLI summary of analysis rows."""
    total = len(rows)
    mismatches = [r for r in rows if r['price_status'] == 'MISMATCH']
    matches = [r for r in rows if r['price_status'] == 'MATCH']
    ghosts = [r for r in rows if r['overall_status'] in ('DELETED', 'TRASH', 'DRAFT')]
    
    banner("ANALYSIS RESULTS", C.CYAN)
    stat_line("Total products/variations analyzed", str(total))
    stat_line("Price matches (POS = WC)", clr(str(len(matches)), C.GREEN))
    stat_line("Price mismatches (POS ≠ WC)", clr(str(len(mismatches)), C.RED if mismatches else C.GREEN))
    stat_line("Ghost products (deleted/trashed/draft in WC)", clr(str(len(ghosts)), C.RED if ghosts else C.GREEN))
    
    if mismatches:
        print(f"\n  {C.RED}{C.BOLD}Top price mismatches:{C.RESET}")
        print(f"  {C.BOLD}{'#':>3}  {'WooID':>7}  {'Product':<45}  {'POS':>10}  {'WC':>10}{C.RESET}")
        print(f"  {C.GRAY}{'─' * 82}{C.RESET}")
        for i, m in enumerate(mismatches[:30], 1):
            name = m['name'][:44] + '…' if len(m['name']) > 45 else m['name']
            pos_p = f"${m['pos_price']}" if m['pos_price'] else '—'
            woo_p = f"${m['woo_price']}" if m['woo_price'] else '—'
            print(f"  {C.WHITE}{i:>3}{C.RESET}  {C.DIM}{m['woo_id']:>7}{C.RESET}  {name:<45}  {C.RED}{pos_p:>10}{C.RESET}  {C.GREEN}{woo_p:>10}{C.RESET}")
        if len(mismatches) > 30:
            print(f"  {C.DIM}... and {len(mismatches) - 30} more (see Google Sheet for full list){C.RESET}")
        print(f"  {C.GRAY}{'─' * 82}{C.RESET}")

def display_sync_summary(price_synced, price_failed, deleted_count, delete_failed, total_mm, total_deleted):
    """Print CLI summary after sync."""
    total_failed = price_failed + delete_failed
    banner("SYNC RESULTS", C.GREEN if total_failed == 0 else C.YELLOW)
    
    stat_line("Price mismatches found", str(total_mm))
    stat_line("Prices synced (WC → POS)", clr(str(price_synced), C.GREEN))
    if price_failed:
        stat_line("Price sync failed", clr(str(price_failed), C.RED))
    
    stat_line("Ghost products found", str(total_deleted))
    stat_line("Products removed from POS", clr(str(deleted_count), C.GREEN if deleted_count else C.DIM))
    if delete_failed:
        stat_line("Delete failed", clr(str(delete_failed), C.RED))
    
    if total_failed == 0:
        stat_line("Overall", clr("All operations successful", C.GREEN))
    else:
        stat_line("Total failures", clr(str(total_failed), C.RED))

# ═══════════════════════════════════════════════════════════════
# Main — 2 options: Analyze / Sync
# ═══════════════════════════════════════════════════════════════
def main():
    signal.signal(signal.SIGINT, lambda *_: (print(f"\n\n  {C.DIM}Bye!{C.RESET}\n"), sys.exit(0)))
    
    banner("DS POS — Product Sync Analyzer", C.CYAN)
    print(f"  {C.DIM}Compares POS prices vs WooCommerce DB (Cloud SQL direct, bypasses API cache)")
    print(f"  Results published to Google Sheets with conditional colors.{C.RESET}")
    
    total_local = Product.objects.filter(status='publish').count()
    info(f"Local POS: {C.BOLD}{total_local}{C.RESET} published products")
    
    while True:
        print()
        print(f"  {C.BOLD}{C.CYAN}What would you like to do?{C.RESET}")
        print(f"  {C.GRAY}{'─' * 50}{C.RESET}")
        print(f"    {C.BOLD}{C.WHITE}1.{C.RESET} {C.YELLOW}Analyze{C.RESET}  {C.DIM}— Compare POS vs WC prices, update Google Sheet{C.RESET}")
        print(f"    {C.BOLD}{C.WHITE}2.{C.RESET} {C.GREEN}Sync{C.RESET}     {C.DIM}— Fix prices + delete ghost products from POS + update Sheet{C.RESET}")
        print(f"    {C.BOLD}{C.GRAY}0.{C.RESET} {C.DIM}Exit{C.RESET}")
        print()
        
        try:
            choice = input(f"  {C.CYAN}▸{C.RESET} Choose (1/2/0): ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        
        if choice == '0' or choice == '':
            print(f"\n  {C.DIM}Goodbye! 👋{C.RESET}\n")
            break
        
        if choice not in ('1', '2'):
            print(f"    {C.RED}Invalid choice.{C.RESET}")
            continue
        
        try:
            # ── Step 1: Always run analysis first ──────────────
            section("Connecting to WooCommerce database...", C.CYAN)
            with woo_db_connection() as conn:
                ok('Connected to WooCommerce database')
                section("Analyzing ALL products — POS vs WooCommerce...", C.YELLOW)
                rows = build_analysis_rows(conn)
            
            ok(f"Analysis complete: {len(rows)} products/variations checked")
            
            mismatches = [r for r in rows if r['price_status'] == 'MISMATCH']
            display_analysis_summary(rows)
            
            # ── Step 2: If sync mode, sync prices + delete ghosts ─
            if choice == '2':
                deleted_rows = [r for r in rows if r['overall_status'] in ('DELETED', 'TRASH', 'DRAFT')]
                if not mismatches and not deleted_rows:
                    ok("Nothing to sync — everything is already in sync!")
                else:
                    print()
                    if mismatches:
                        info(f"Syncing {C.BOLD}{len(mismatches)}{C.RESET} price mismatches from WooCommerce → POS...")
                    if deleted_rows:
                        info(f"Removing {C.BOLD}{len(deleted_rows)}{C.RESET} ghost products from POS...")
                    price_synced, price_failed, deleted_count, delete_failed, rows = sync_all(rows)
                    display_sync_summary(price_synced, price_failed, deleted_count, delete_failed,
                                         len(mismatches), len(deleted_rows))
            
            # ── Step 3: Publish to Google Sheet ────────────────
            section("Publishing results to Google Sheet...", C.BLUE)
            with Spinner("Updating Google Sheet with fresh data..."):
                result = publish_to_sheet(rows)
            if result:
                ok(f"Google Sheet updated! {C.DIM}({GOOGLE_SHEET_ID}){C.RESET}")
                info(f"View: {C.ULINE}https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}{C.RESET}")
            
        except Exception as e:
            err(f"Error: {e}")
            import traceback
            traceback.print_exc()
        
        print(f"\n  {C.GRAY}{'═' * 60}{C.RESET}")
        input(f"  {C.DIM}Press Enter to return to menu...{C.RESET}")

if __name__ == '__main__':
    main()
