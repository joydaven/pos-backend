"""
Management command to bulk-sync Authorize.net CIM profiles to Contact records.

Fetches all CIM customer profiles, matches them to local Contacts by email
(or WooCommerce customer ID), and saves the CIM profile ID on the Contact.

Uses raw HTTP/XML requests to bypass the buggy pyxb SDK parser which fails
with ContentNondeterminismExceededError on newer Authorize.net API responses.

Usage:
    python manage.py sync_cim_profiles          # full sync
    python manage.py sync_cim_profiles --dry-run # preview only
"""
import logging
import requests as http_requests
import xml.etree.ElementTree as ET
from django.core.management.base import BaseCommand
from payments.config import (
    AUTHORIZE_NET_LOGIN_ID,
    AUTHORIZE_NET_TRANSACTION_KEY,
    AUTHORIZE_NET_ENDPOINT,
)

logger = logging.getLogger(__name__)

NS = 'AnetApi/xml/v1/schema/AnetApiSchema.xsd'
NS_PREFIX = '{' + NS + '}'


class Command(BaseCommand):
    help = 'Sync Authorize.net CIM customer profiles to local Contact records by email'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview matches without saving',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Overwrite existing CIM profile IDs on contacts (default: skip them)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        force = options['force']
        from crm.models import Contact

        self.stdout.write("Fetching all CIM customer profile IDs from Authorize.net...")

        # Step 1: Get all profile IDs via raw XML (bypasses buggy pyxb SDK)
        try:
            get_ids_xml = f'''<?xml version="1.0" encoding="utf-8"?>
            <getCustomerProfileIdsRequest xmlns="{NS}">
                <merchantAuthentication>
                    <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                    <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                </merchantAuthentication>
            </getCustomerProfileIdsRequest>'''

            ids_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_ids_xml,
                                          headers={'Content-Type': 'text/xml'}, timeout=30)

            if ids_resp.status_code != 200:
                self.stderr.write(self.style.ERROR(f"HTTP error fetching CIM IDs: {ids_resp.status_code}"))
                return

            ids_root = ET.fromstring(ids_resp.text)
            result_code = ids_root.findtext(f'.//{NS_PREFIX}resultCode')
            if result_code != 'Ok':
                self.stderr.write(self.style.ERROR(f"Failed to fetch CIM profile IDs: {result_code}"))
                return

            profile_ids = [elem.text for elem in ids_root.findall(f'.//{NS_PREFIX}numericString') if elem.text]
            self.stdout.write(f"Found {len(profile_ids)} CIM profiles")

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error fetching profile IDs: {e}"))
            return

        # Step 2: Fetch each profile via raw XML and match by email
        matched = 0
        already_linked = 0
        skipped = 0
        not_found = 0
        errors = 0

        for i, pid in enumerate(profile_ids):
            if (i + 1) % 50 == 0:
                self.stdout.write(f"  Processing {i + 1}/{len(profile_ids)}...")

            try:
                get_profile_xml = f'''<?xml version="1.0" encoding="utf-8"?>
                <getCustomerProfileRequest xmlns="{NS}">
                    <merchantAuthentication>
                        <name>{AUTHORIZE_NET_LOGIN_ID}</name>
                        <transactionKey>{AUTHORIZE_NET_TRANSACTION_KEY}</transactionKey>
                    </merchantAuthentication>
                    <customerProfileId>{pid}</customerProfileId>
                </getCustomerProfileRequest>'''

                profile_resp = http_requests.post(AUTHORIZE_NET_ENDPOINT, data=get_profile_xml,
                                                  headers={'Content-Type': 'text/xml'}, timeout=10)

                if profile_resp.status_code != 200:
                    errors += 1
                    continue

                profile_root = ET.fromstring(profile_resp.text)
                profile_result = profile_root.findtext(f'.//{NS_PREFIX}resultCode')
                if profile_result != 'Ok':
                    errors += 1
                    continue

                profile_email = (profile_root.findtext(f'.//{NS_PREFIX}profile/{NS_PREFIX}email') or '').strip()
                merchant_cust_id = (profile_root.findtext(f'.//{NS_PREFIX}profile/{NS_PREFIX}merchantCustomerId') or '').strip()

                if not profile_email:
                    not_found += 1
                    continue

                # Try to find Contact by email (case-insensitive)
                contact = Contact.objects.filter(email__iexact=profile_email).first()

                # Fallback: try merchantCustomerId as WooCommerce ID (only if numeric)
                if not contact and merchant_cust_id and merchant_cust_id.isdigit():
                    contact = Contact.objects.filter(woo_customer_id=merchant_cust_id).first()

                if not contact:
                    not_found += 1
                    continue

                if contact.authorize_net_customer_profile_id == str(pid):
                    already_linked += 1
                    continue

                # Don't overwrite an existing (different) profile ID unless --force
                # Treat 'none' sentinel as empty (it means "previously searched, not found")
                existing_id = contact.authorize_net_customer_profile_id or ''
                if existing_id and existing_id != 'none' and not force:
                    skipped += 1
                    continue

                if dry_run:
                    self.stdout.write(
                        f"  [DRY RUN] Would link CIM {pid} → Contact {contact.id} "
                        f"({contact.email})"
                    )
                else:
                    contact.authorize_net_customer_profile_id = str(pid)
                    contact.save(update_fields=['authorize_net_customer_profile_id'])

                matched += 1

            except Exception as e:
                logger.warning(f"Error processing CIM profile {pid}: {e}")
                errors += 1
                continue

        prefix = "[DRY RUN] " if dry_run else ""
        self.stdout.write(self.style.SUCCESS(
            f"\n{prefix}Sync complete:\n"
            f"  Total CIM profiles: {len(profile_ids)}\n"
            f"  Newly matched:      {matched}\n"
            f"  Already linked:     {already_linked}\n"
            f"  Skipped (existing): {skipped}\n"
            f"  No local match:     {not_found}\n"
            f"  Errors:             {errors}"
        ))
