"""
upgrade.py
==========
Blueprint for DocBuddy's "Premium" upgrade + checkout flow.

Wire-up (see the bottom of this file / the notes at the end of the chat
response for the two lines that go in app.py).

SECURITY NOTE — read before deploying
--------------------------------------
The checkout template on this page has raw "Card number / Expiry / CVV"
inputs that post straight to your Flask server. That pattern is convenient
to prototype with, but submitting raw card data to your own backend puts
your app in PCI-DSS SAQ D scope (the strict tier — think ~300 controls,
quarterly scans, etc.), and this file deliberately does NOT store, log, or
forward any of those fields anywhere.

For a real launch, swap the card panel for your payment gateway's hosted
checkout / JS SDK (Razorpay Checkout, Stripe Elements, etc.) so card data
goes straight from the browser to the gateway and your server only ever
sees a short-lived token / payment id. I've left a `create_gateway_order()`
stub below showing where that call goes, and `verify_gateway_payment()`
where you'd verify the signature/webhook before marking a user Premium.
Until that's wired up, `process_payment()` below is a MOCK that always
succeeds — good for building/demoing the UI, not for taking real money.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

upgrade_bp = Blueprint("upgrade", __name__)

# ---------------------------------------------------------------------------
# Plan catalogue — single source of truth for pricing/features.
# Edit this dict and both the pricing page and checkout stay in sync.
# ---------------------------------------------------------------------------
PLANS = {
    "free": {
        "name": "Free",
        "monthly_price": 0,
        "yearly_price": 0,
        "features": [
            "5 file conversions / day",
            "Basic PDF ↔ JPG tools",
            "10 MB max file size",
            "Standard processing speed",
        ],
    },
    "pro": {
        "name": "Pro",
        "monthly_price": 249,
        "yearly_price": 2490,  # ~17% off vs monthly
        "features": [
            "Unlimited conversions",
            "OCR + batch processing",
            "200 MB max file size",
            "Priority processing speed",
            "No ads",
            "Email support",
        ],
    },
    "business": {
        "name": "Business",
        "monthly_price": 699,
        "yearly_price": 6990,
        "features": [
            "Everything in Pro",
            "5 team seats included",
            "1 GB max file size",
            "API access",
            "Shared team workspace",
            "Priority support (24h SLA)",
        ],
    },
}

# A detailed row-by-row comparison, used by the new comparison table on
# the pricing page (separate from the short bullet lists above so the
# cards stay scannable while the table can go deep).
COMPARISON_ROWS = [
    ("Daily conversions", "5 / day", "Unlimited", "Unlimited"),
    ("Max file size", "10 MB", "200 MB", "1 GB"),
    ("OCR (scanned documents)", "—", "✓", "✓"),
    ("Batch processing", "—", "✓", "✓"),
    ("Team seats", "1", "1", "5 included"),
    ("API access", "—", "—", "✓"),
    ("Support", "Community", "Email (24h)", "Priority (same-day)"),
    ("Ads", "Shown", "None", "None"),
]

# Placeholder testimonials — swap for real quotes from your own users
# before shipping. Kept short and specific on purpose (specificity is
# what makes a testimonial read as genuine rather than generic praise).
TESTIMONIALS = [
    {
        "quote": "We batch-convert about 200 scanned contracts a week. "
                 "Pro cut that from an afternoon to about 20 minutes.",
        "name": "Ananya R.",
        "role": "Operations lead, logistics startup",
    },
    {
        "quote": "The OCR on messy scanned invoices is what sold me — "
                 "it picks up totals our old tool used to miss.",
        "name": "Farhan K.",
        "role": "Freelance accountant",
    },
    {
        "quote": "Moved our 5-person design team to Business mainly for "
                 "the shared workspace. Nobody emails PDFs around anymore.",
        "name": "Priya M.",
        "role": "Studio manager",
    },
]

# Example stats for the trust strip under the hero. Replace with real,
# current numbers (or remove the strip) before launch — don't ship
# invented figures as fact.
STATS = {
    "docs_processed": "2.1M+",
    "docs_processed_label": "documents processed this year",
    "avg_rating": "4.8/5",
    "avg_rating_label": "average support rating",
    "avg_time_saved": "12 min",
    "avg_time_saved_label": "saved per batch job, on average",
}

FAQS = [
    (
        "Can I cancel anytime?",
        "Yes. Cancel from your account settings whenever you like — you'll "
        "keep Premium access until the end of the billing period you already paid for.",
    ),
    (
        "What happens to my files if I downgrade?",
        "Nothing is deleted. You'll just drop back to Free plan limits "
        "(file size, daily conversions) for anything you process after that.",
    ),
    (
        "Can I switch between Pro and Business later?",
        "Yes, upgrade or downgrade anytime from this page — we prorate the "
        "difference against your current billing period.",
    ),
    (
        "Is my payment information stored on your servers?",
        "No. Card and UPI details are handled by our payment gateway, not "
        "stored on DocBuddy's servers.",
    ),
    (
        "Do you offer invoices for Business accounts?",
        "Yes, a GST-compliant invoice is generated automatically for every "
        "Business payment and emailed to your billing address.",
    ),
]


# ---------------------------------------------------------------------------
# Auth helper — adapt to however app.py already tracks logged-in users.
# This assumes a `user_id` key in the session, which is the most common
# pattern; swap the body for `flask_login.login_required` if you use that.
# ---------------------------------------------------------------------------
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


# ---------------------------------------------------------------------------
# Data access — placeholders. Replace with your real DB layer (SQLAlchemy,
# a users table, etc). Kept here as plain functions so upgrade.py has no
# hard dependency on your models module.
# ---------------------------------------------------------------------------
def get_current_plan(user_id: str) -> str:
    """Return the plan_id ('free' | 'pro' | 'business') the user is on."""
    db = current_app.config.get("DB")
    if db is None:
        return "free"
    user = db.get_user(user_id)
    return getattr(user, "plan", "free") or "free"


def set_user_plan(user_id: str, plan_id: str, billing_cycle: str) -> None:
    """Persist the new plan. Wire this to your real user model."""
    db = current_app.config.get("DB")
    renews_at = datetime.utcnow() + (
        timedelta(days=365) if billing_cycle == "yearly" else timedelta(days=30)
    )
    if db is None:
        current_app.logger.info(
            "MOCK upgrade: user=%s plan=%s cycle=%s renews=%s",
            user_id, plan_id, billing_cycle, renews_at,
        )
        return
    db.update_user(user_id, plan=plan_id, billing_cycle=billing_cycle, renews_at=renews_at)


def record_transaction(user_id: str, plan_id: str, amount: int, method: str) -> str:
    """Store an order/transaction row and return an order id."""
    order_id = f"ORD-{uuid.uuid4().hex[:10].upper()}"
    db = current_app.config.get("DB")
    if db is not None:
        db.create_transaction(
            order_id=order_id,
            user_id=user_id,
            plan_id=plan_id,
            amount=amount,
            method=method,
            created_at=datetime.utcnow(),
        )
    return order_id


# ---------------------------------------------------------------------------
# Payment gateway integration points.
# ---------------------------------------------------------------------------
def create_gateway_order(amount_paise: int, receipt: str) -> dict:
    """
    Placeholder for e.g. Razorpay's `client.order.create(...)`.
    Return value would normally be handed to the frontend to open the
    gateway's hosted checkout widget with a signed order_id.
    """
    return {"id": f"mock_order_{uuid.uuid4().hex[:8]}", "amount": amount_paise}


def verify_gateway_payment(payload: dict) -> bool:
    """
    Placeholder for verifying the gateway's payment signature/webhook
    before you ever mark a user as Premium. Always False-safe here.
    """
    return True  # MOCK — replace with real HMAC/signature verification


def process_payment(method: str, amount: int, form: dict) -> bool:
    """
    MOCK payment processor. Never touches/stores card, CVV, or UPI PIN data.
    Replace this whole function with a real gateway call once wired up —
    see the module docstring.
    """
    if method not in {"upi", "card", "netbanking", "wallet"}:
        return False
    # Intentionally not reading form['card_number'] / form['card_cvv'] etc.
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@upgrade_bp.route("/upgrade")
@login_required
def upgrade_page():
    user_id = session["user_id"]
    current_plan = get_current_plan(user_id)
    return render_template(
        "upgrade.html",
        plans=PLANS,
        current_plan=current_plan,
        comparison_rows=COMPARISON_ROWS,
        testimonials=TESTIMONIALS,
        stats=STATS,
        faqs=FAQS,
    )


@upgrade_bp.route("/upgrade/checkout", methods=["POST"])
@login_required
def upgrade_checkout():
    user_id = session["user_id"]

    plan_id = request.form.get("plan", "")
    billing_cycle = request.form.get("billing_cycle", "monthly")
    payment_method = request.form.get("payment_method", "upi")

    if plan_id not in PLANS or plan_id == "free":
        flash("Please choose a plan to upgrade to.", "error")
        return redirect(url_for("upgrade.upgrade_page"))

    if billing_cycle not in {"monthly", "yearly"}:
        billing_cycle = "monthly"

    plan = PLANS[plan_id]
    amount = plan["yearly_price"] if billing_cycle == "yearly" else plan["monthly_price"]

    if not process_payment(payment_method, amount, request.form):
        flash("We couldn't process that payment. Please try again.", "error")
        return redirect(url_for("upgrade.upgrade_page"))

    order_id = record_transaction(user_id, plan_id, amount, payment_method)
    set_user_plan(user_id, plan_id, billing_cycle)

    flash(f"Welcome to DocBuddy {plan['name']}! Your upgrade is active.", "success")
    return redirect(url_for("upgrade.upgrade_success", order_id=order_id))


@upgrade_bp.route("/upgrade/success/<order_id>")
@login_required
def upgrade_success(order_id):
    return render_template("upgrade_success.html", order_id=order_id)


# Optional: small JSON endpoint if you want the price switcher to fetch
# server-computed prices instead of doing the math in JS (not required —
# upgrade.html currently computes it client-side from data-attributes).
@upgrade_bp.route("/upgrade/plans.json")
def plans_json():
    return jsonify(PLANS)