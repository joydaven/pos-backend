"""
Slack notification utilities for POS order processing alerts.

Sends webhook messages to a Slack channel when critical order failures occur
(e.g. payment captured but WooCommerce order creation failed).
"""

import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = getattr(settings, 'SLACK_WEBHOOK_URL', None)


def _post_to_slack(payload: dict) -> bool:
    url = SLACK_WEBHOOK_URL
    if not url:
        logger.warning("SLACK_WEBHOOK_URL is not configured — skipping Slack notification")
        return False
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            logger.info("Slack notification sent successfully")
            return True
        else:
            logger.error(f"Slack webhook returned {resp.status_code}: {resp.text}")
            return False
    except Exception as exc:
        logger.error(f"Failed to send Slack notification: {exc}")
        return False


def notify_order_queued(
    order_number: str,
    failed_step: str,
    error_message: str,
    payment_amount=None,
    payment_method: str = '',
    transaction_id: str = '',
    customer_name: str = '',
    customer_email: str = '',
    pos_order_id: str = '',
):
    """
    Send a Slack alert when an order is queued because a post-payment step failed.
    """
    step_labels = {
        'pos_order_save': 'POS Order Save',
        'woocommerce_order': 'WooCommerce Order Creation',
        'ghl_sync': 'GHL Membership Sync',
        'credit_points': 'Credit Service Points',
        'receipt': 'Receipt Email',
    }
    step_display = step_labels.get(failed_step, failed_step)

    amount_str = f"${payment_amount:.2f}" if payment_amount else "N/A"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "PAYMENT TAKEN — Order Queued (DO NOT REPROCESS)",
                "emoji": True,
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Order Number:*\n{order_number}"},
                {"type": "mrkdwn", "text": f"*Failed Step:*\n{step_display}"},
                {"type": "mrkdwn", "text": f"*Payment Amount:*\n{amount_str}"},
                {"type": "mrkdwn", "text": f"*Payment Method:*\n{payment_method or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Transaction ID:*\n{transaction_id or 'N/A'}"},
                {"type": "mrkdwn", "text": f"*Customer:*\n{customer_name or 'N/A'} ({customer_email or 'N/A'})"},
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Error:*\n```{error_message[:500]}```"
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        ":rotating_light: *The payment has already been captured.* "
                        "This order has been placed in the retry queue. "
                        "DO NOT create a new order or charge the customer again."
                    )
                }
            ]
        }
    ]

    payload = {
        "text": f"ALERT: Order {order_number} queued — payment already taken, {step_display} failed",
        "blocks": blocks,
    }

    return _post_to_slack(payload)


def notify_order_queue_resolved(
    order_number: str,
    failed_step: str,
    resolved_by: str = '',
    method: str = 'auto-retry',
):
    """Notify Slack when a queued order has been successfully resolved."""
    payload = {
        "text": f"Order {order_number} queue entry resolved ({method}) by {resolved_by or 'system'}",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f":white_check_mark: *Queued order resolved*\n"
                        f"*Order:* {order_number}\n"
                        f"*Step:* {failed_step}\n"
                        f"*Method:* {method}\n"
                        f"*Resolved by:* {resolved_by or 'system'}"
                    ),
                }
            }
        ]
    }
    return _post_to_slack(payload)
