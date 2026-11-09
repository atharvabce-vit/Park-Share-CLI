import base64
import hashlib
import hmac
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class PaymentError(Exception):
    pass


def _request(key_id, key_secret, path, payload=None):
    if not key_id or not key_secret:
        raise PaymentError("Razorpay is not configured.")
    credentials = base64.b64encode(
        (key_id + ":" + key_secret).encode("utf-8")
    ).decode("ascii")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        "https://api.razorpay.com/v1/" + path,
        data=body,
        headers={
            "Authorization": "Basic " + credentials,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST" if body is not None else "GET",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise PaymentError(
            "Razorpay API request failed with HTTP " + str(error.code) + "."
        ) from error
    except (URLError, TimeoutError) as error:
        raise PaymentError("Razorpay API is unavailable.") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PaymentError("Razorpay returned an invalid response.") from error


def create_order(key_id, key_secret, booking_id, amount_paise):
    return _request(key_id, key_secret, "orders", {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": "parkshare-" + str(booking_id),
        "notes": {"parkshare_booking_id": str(booking_id)},
    })


def verify_checkout_signature(key_id, key_secret, order_id, payment_id, signature):
    if not key_secret or not order_id or not payment_id or not signature:
        return False
    message = (order_id + "|" + payment_id).encode("utf-8")
    expected = hmac.new(key_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def fetch_payment(key_id, key_secret, payment_id):
    if not re.fullmatch(r"[A-Za-z0-9_]+", payment_id or ""):
        raise PaymentError("Razorpay payment ID is invalid.")
    return _request(key_id, key_secret, "payments/" + payment_id)


def verify_webhook_signature(body, signature, webhook_secret):
    if not webhook_secret or not signature:
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
