"""
Audit all active membership subscription customers for saved cards on file.
Checks card info directly from WooCommerce subscription meta_data (no local DB needed).

Usage:
  python manage.py audit_membership_cards
  python manage.py audit_membership_cards --status active
  python manage.py audit_membership_cards --verbose
"""
import logging
from django.core.management.base import BaseCommand
from crm.woocommerce import WooCommerceAPI

logger = logging.getLogger(__name__)

# Meta keys that indicate a saved card exists on the subscription
CARD_META_KEYS = {
    '_authnet_cc_last4',
    '_authnet_cc_type',
    '_authnet_customer_id',
    '_authnet_card_id',
    '_wc_authorize_net_cim_credit_card_last_four',
    '_wc_authorize_net_cim_credit_card_card_type',
    '_authorize_net_cim_credit_card_customer_id',
    '_authorize_net_cim_credit_card_payment_token',
}


class Command(BaseCommand):
    help = 'Check all membership subscription customers for saved cards on file (via WC meta_data)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--status', type=str, default='active',
            help='Subscription status to filter (default: active). Use "any" for all.'
        )
        parser.add_argument(
            '--verbose', action='store_true',
            help='Show detailed output per customer'
        )

    def _extract_card_info(self, meta_data):
        """Extract card info from subscription meta_data."""
        card_info = {}
        for meta in meta_data:
            key = meta.get('key', '')
            value = meta.get('value', '')
            if key in CARD_META_KEYS and value:
                card_info[key] = value
        
        # Build a readable card description
        brand = (card_info.get('_authnet_cc_type') or
                 card_info.get('_wc_authorize_net_cim_credit_card_card_type') or '')
        last4 = (card_info.get('_authnet_cc_last4') or
                 card_info.get('_wc_authorize_net_cim_credit_card_last_four') or '')
        cim_customer = (card_info.get('_authnet_customer_id') or
                        card_info.get('_authorize_net_cim_credit_card_customer_id') or '')
        cim_token = (card_info.get('_authnet_card_id') or
                     card_info.get('_authorize_net_cim_credit_card_payment_token') or '')

        has_card = bool(last4 or cim_customer)
        description = ''
        if brand and last4:
            description = f'{brand} ending in {last4}'
        elif last4:
            description = f'Card ending in {last4}'
        elif cim_customer:
            description = f'CIM Profile {cim_customer}' + (f' / Token {cim_token}' if cim_token else '')

        return has_card, description

    def handle(self, *args, **options):
        status_filter = options['status']
        verbose = options['verbose']

        wc_api = WooCommerceAPI()
        self.stdout.write(self.style.NOTICE(
            f'\nAuditing membership subscriptions (status={status_filter}) — checking WC meta_data for card info...\n'
        ))

        # Fetch all membership subscriptions from WooCommerce (paginated)
        all_subs = []
        page = 1
        per_page = 50
        while True:
            result = wc_api.get_subscriptions(
                page=page, per_page=per_page, status=status_filter
            )
            if result['status'] != 'success' or not result.get('data'):
                break
            subs = result['data']
            for sub in subs:
                line_items = sub.get('line_items', [])
                is_membership = any(
                    'membership' in (item.get('name', '') or '').lower()
                    or 'member' in (item.get('name', '') or '').lower()
                    for item in line_items
                )
                if is_membership:
                    all_subs.append(sub)
            total_pages = int(result.get('headers', {}).get('X-WP-TotalPages', 1))
            if page >= total_pages:
                break
            page += 1

        self.stdout.write(f'Found {len(all_subs)} membership subscriptions\n')

        # Group by customer and check card info per subscription
        customers = {}
        for sub in all_subs:
            cid = sub.get('customer_id')
            if not cid or cid == 0:
                continue

            meta_data = sub.get('meta_data', []) if isinstance(sub.get('meta_data'), list) else []
            has_card, card_desc = self._extract_card_info(meta_data)
            payment_method = sub.get('payment_method', '')
            payment_title = sub.get('payment_method_title', '')

            if cid not in customers:
                billing = sub.get('billing', {})
                email = billing.get('email', '')
                name = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
                customers[cid] = {
                    'name': name,
                    'email': email,
                    'woo_customer_id': cid,
                    'subscriptions': [],
                    'has_card': False,
                    'card_desc': '',
                }

            customers[cid]['subscriptions'].append({
                'id': sub.get('id'),
                'status': sub.get('status'),
                'has_card': has_card,
                'card_desc': card_desc,
                'payment_method': payment_method,
                'payment_title': payment_title,
            })

            if has_card:
                customers[cid]['has_card'] = True
                customers[cid]['card_desc'] = card_desc

        self.stdout.write(f'Unique customers: {len(customers)}\n')
        self.stdout.write('-' * 100 + '\n')

        with_card = []
        without_card = []

        for cid, info in sorted(customers.items(), key=lambda x: x[1]['name']):
            if info['has_card']:
                with_card.append(info)
                if verbose:
                    self.stdout.write(self.style.SUCCESS(
                        f"  [CARD] {info['name']:30s} {info['email']:40s} {info['card_desc']}"
                    ))
            else:
                without_card.append(info)
                sub_info = info['subscriptions'][0]
                pm = sub_info['payment_title'] or sub_info['payment_method'] or 'N/A'
                if verbose:
                    self.stdout.write(self.style.ERROR(
                        f"  [NO CARD] {info['name']:30s} {info['email']:40s} Payment: {pm}"
                    ))

        # Summary
        self.stdout.write('\n' + '=' * 100)
        self.stdout.write(self.style.SUCCESS(f'\n  Customers WITH card on subscription meta: {len(with_card)}'))
        self.stdout.write(self.style.ERROR(f'  Customers WITHOUT card on subscription meta: {len(without_card)}'))
        self.stdout.write(f'  Total unique customers: {len(customers)}\n')

        if without_card:
            self.stdout.write(self.style.ERROR('\n--- Customers WITHOUT card info in subscription meta_data ---'))
            for c in without_card:
                sub_info = c['subscriptions'][0]
                pm = sub_info['payment_title'] or sub_info['payment_method'] or 'N/A'
                sub_ids = [s['id'] for s in c['subscriptions']]
                self.stdout.write(
                    f"  {c['name']:30s} {c['email']:40s} Subs: {sub_ids}  Payment: {pm}"
                )

        if with_card:
            self.stdout.write(self.style.SUCCESS('\n--- Customers WITH card info ---'))
            for c in with_card:
                sub_ids = [s['id'] for s in c['subscriptions']]
                self.stdout.write(
                    f"  {c['name']:30s} {c['email']:40s} Subs: {sub_ids}  {c['card_desc']}"
                )

        self.stdout.write('\n')
