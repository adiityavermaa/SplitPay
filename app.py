import re
import io
import csv
import json
import uuid
import random
from datetime import datetime, timedelta
from urllib.parse import unquote

from flask import Flask, render_template, request, jsonify, session, redirect, Response
from sqlalchemy import inspect
from models import db, User, Transaction

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///splitpay.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'splitpay_super_secret_key'

db.init_app(app)

SPLIT_LIMIT = 2000
IST = timedelta(hours=5, minutes=30)


def init_db():
    with app.app_context():
        db.create_all()
        insp = inspect(db.engine)
        user_cols = [c['name'] for c in insp.get_columns('users')]
        txn_cols = [c['name'] for c in insp.get_columns('transactions')]

        if 'bank_account' not in user_cols or 'split_utrs' not in txn_cols:
            db.drop_all()
            db.create_all()

        if not User.query.filter_by(upi_id='cafe.mocha@mockupi').first():
            merchant = User(
                phone='9999999999',
                upi_id='cafe.mocha@mockupi',
                upi_pin='1234',
                role='merchant',
                bank_account='1234567890',
                ifsc_code='MOCK0001234'
            )
            db.session.add(merchant)
            db.session.commit()


init_db()


# ---------------- HELPERS ----------------

def current_user():
    if 'user_phone' not in session:
        return None
    return User.query.filter_by(phone=session['user_phone']).first()


def merchant_only():
    user = current_user()
    if not user or user.role != 'merchant':
        return None
    return user


def clean_upi(value):
    """Finds name@bank inside a plain ID or a full upi://pay?pa=... QR string."""
    value = unquote((value or '').strip())
    m = re.search(r'[\w.\-]+@[\w.\-]+', value)
    return m.group(0).lower() if m else ''


def split_amount(amount):
    chunks = []
    remaining = amount
    while remaining > SPLIT_LIMIT:
        chunks.append(float(SPLIT_LIMIT))
        remaining -= SPLIT_LIMIT
    if remaining > 0:
        chunks.append(round(remaining, 2))
    utrs = [str(random.randint(10**11, 10**12 - 1)) for _ in chunks]
    return chunks, utrs


def home_for(user, needs_pin=False):
    if needs_pin:
        return '/setup-pin'
    if user.role == 'merchant':
        return '/merchant'
    return '/'


# ---------------- PAGES ----------------

@app.route('/')
def index():
    if 'user_phone' not in session:
        return redirect('/login')
    user = current_user()
    if not user:
        session.clear()
        return redirect('/login')
    if not user.upi_pin:
        return redirect('/setup-pin')
    if user.role == 'merchant':
        return redirect('/merchant')

    transactions = Transaction.query.filter(
        (Transaction.sender_upi == user.upi_id) | (Transaction.receiver_upi == user.upi_id)
    ).order_by(Transaction.timestamp.desc()).all()

    return render_template('index.html', transactions=transactions)


@app.route('/login')
def login():
    return render_template('login.html')


@app.route('/setup-pin')
def setup_pin():
    if 'user_phone' not in session:
        return redirect('/login')
    return render_template('setup_pin.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/merchant')
def merchant():
    user = current_user()
    if not user or user.role != 'merchant':
        return redirect('/login')
    return render_template('merchant.html', merchant_upi=user.upi_id)


# ---------------- AUTH APIs ----------------

@app.route('/api/request-otp', methods=['POST'])
def request_otp():
    data = request.json or {}
    phone = data.get('phone')
    if not phone or len(phone) != 10:
        return jsonify({"status": "error", "message": "Invalid phone number"}), 400
    return jsonify({"status": "success", "message": "OTP sent"})


@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json or {}
    phone = data.get('phone')
    otp = data.get('otp')

    if otp != "123456":
        return jsonify({"status": "error", "message": "Invalid OTP"}), 400

    user = User.query.filter_by(phone=phone).first()
    if not user:
        user = User(phone=phone, upi_id=f"user{phone[-4:]}@mockupi")
        db.session.add(user)
        db.session.commit()

    session['user_phone'] = phone
    needs_pin = not user.upi_pin
    target = home_for(user, needs_pin)

    return jsonify({
        "status": "success",
        "needs_pin": needs_pin,
        "role": user.role,
        "redirect": target,
        "redirect_url": target,
        "next": target,
        "url": target
    })


@app.route('/api/set-pin', methods=['POST'])
def set_pin():
    user = current_user()
    if not user:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json or {}
    pin = data.get('pin')
    role = data.get('role', 'customer')

    if not pin or len(pin) != 4 or not pin.isdigit():
        return jsonify({"status": "error", "message": "PIN must be 4 digits"}), 400

    user.upi_pin = pin
    user.role = role
    db.session.commit()

    target = home_for(user)
    return jsonify({
        "status": "success",
        "role": user.role,
        "redirect": target,
        "redirect_url": target,
        "next": target,
        "url": target
    })


# ---------------- BALANCE APIs ----------------

@app.route('/api/get-balance')
def get_balance():
    if 'user_phone' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    user = User.query.filter_by(phone=session['user_phone']).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"upi_id": user.upi_id})


@app.route('/api/check-balance', methods=['POST'])
def check_balance():
    if 'user_phone' not in session:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json or {}
    pin = data.get('pin')
    user = User.query.filter_by(phone=session['user_phone']).first()

    if not user or user.upi_pin != pin:
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400

    return jsonify({"status": "success", "balance": user.balance})


# ---------------- PAYMENT APIs ----------------

@app.route('/api/pay', methods=['POST'])
def pay():
    sender = current_user()
    if not sender:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json or {}
    # index.html sends the receiver as "receiver"
    receiver_upi = clean_upi(data.get('receiver') or data.get('receiver_upi'))
    pin = data.get('pin')
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid amount"}), 400

    if sender.upi_pin != pin:
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400
    if amount <= 0:
        return jsonify({"status": "error", "message": "Invalid amount"}), 400
    if amount > sender.balance:
        return jsonify({"status": "error", "message": "Insufficient balance"}), 400

    receiver = User.query.filter(db.func.lower(User.upi_id) == receiver_upi).first()
    if not receiver:
        return jsonify({"status": "error", "message": f"Receiver not found: '{receiver_upi}'"}), 404
    if receiver.id == sender.id:
        return jsonify({"status": "error", "message": "Cannot pay yourself"}), 400

    chunks, utrs = split_amount(amount)

    sender.balance -= amount
    receiver.balance += amount

    txn = Transaction(
        txn_id=str(uuid.uuid4())[:8],
        sender_upi=sender.upi_id,
        receiver_upi=receiver.upi_id,
        amount=amount,
        split_data=json.dumps(chunks),
        split_utrs=json.dumps(utrs)
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({
        "status": "success",
        "txn_id": txn.txn_id,
        "splits": chunks,
        "utrs": utrs,
        "amount": amount
    })


@app.route('/api/bank_transfer', methods=['POST'])
def bank_transfer():
    sender = current_user()
    if not sender:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json or {}
    # index.html sends the account number as "account_no"
    account = (data.get('account_no') or data.get('account') or '').strip()
    ifsc = (data.get('ifsc') or '').strip().upper()
    pin = data.get('pin')
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid amount"}), 400

    if sender.upi_pin != pin:
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400
    if amount <= 0 or amount > sender.balance:
        return jsonify({"status": "error", "message": "Invalid amount or insufficient balance"}), 400

    receiver = User.query.filter_by(bank_account=account, ifsc_code=ifsc).first()
    if not receiver:
        return jsonify({"status": "error", "message": "Bank account not found"}), 404
    if receiver.id == sender.id:
        return jsonify({"status": "error", "message": "Cannot pay yourself"}), 400

    chunks, utrs = split_amount(amount)

    sender.balance -= amount
    receiver.balance += amount

    txn = Transaction(
        txn_id=str(uuid.uuid4())[:8],
        sender_upi=sender.upi_id,
        receiver_upi=receiver.upi_id,
        amount=amount,
        split_data=json.dumps(chunks),
        split_utrs=json.dumps(utrs)
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({"status": "success", "txn_id": txn.txn_id, "splits": chunks, "utrs": utrs})


# ---------------- MERCHANT APIs ----------------

@app.route('/api/merchant/received')
def merchant_received():
    user = current_user()
    if not user or user.role != 'merchant':
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    txns = Transaction.query.filter_by(receiver_upi=user.upi_id)\
        .order_by(Transaction.timestamp.desc()).all()

    return jsonify({
        "status": "success",
        "balance": user.balance,
        "transactions": [{
            "txn_id": t.txn_id,
            "sender_upi": t.sender_upi,
            "amount": t.amount,
            "splits": json.loads(t.split_data) if t.split_data else [],
            "utrs": json.loads(t.split_utrs) if t.split_utrs else [],
            "timestamp": t.timestamp.strftime("%d %b %Y, %I:%M %p")
        } for t in txns]
    })


@app.route('/api/merchant/stats')
def merchant_stats():
    user = merchant_only()
    if not user:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    txns = Transaction.query.filter_by(receiver_upi=user.upi_id).all()
    today = (datetime.utcnow() + IST).date()

    def day_of(t):
        return (t.timestamp + IST).date()

    today_total = sum(t.amount for t in txns if day_of(t) == today)
    week_total = sum(t.amount for t in txns if 0 <= (today - day_of(t)).days < 7)
    month_total = sum(t.amount for t in txns
                      if day_of(t).year == today.year and day_of(t).month == today.month)
    total = sum(t.amount for t in txns)
    count = len(txns)
    chunks = sum(len(json.loads(t.split_data)) for t in txns if t.split_data)

    daily = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        daily.append({
            "label": day.strftime("%a"),
            "amount": sum(t.amount for t in txns if day_of(t) == day)
        })

    return jsonify({
        "status": "success",
        "balance": user.balance,
        "today": today_total,
        "week": week_total,
        "month": month_total,
        "total": total,
        "count": count,
        "average": round(total / count, 2) if count else 0,
        "chunks": chunks,
        "daily": daily,
        "bank_account": user.bank_account,
        "ifsc": user.ifsc_code
    })


@app.route('/api/merchant/export')
def merchant_export():
    user = merchant_only()
    if not user:
        return redirect('/login')

    txns = Transaction.query.filter_by(receiver_upi=user.upi_id)\
        .order_by(Transaction.timestamp.desc()).all()

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Txn ID", "From", "Amount", "Chunks", "UTRs", "Date (IST)"])
    for t in txns:
        splits = json.loads(t.split_data) if t.split_data else []
        utrs = json.loads(t.split_utrs) if t.split_utrs else []
        w.writerow([
            t.txn_id, t.sender_upi, t.amount,
            " + ".join(str(int(s)) if float(s).is_integer() else str(s) for s in splits),
            " | ".join(utrs),
            (t.timestamp + IST).strftime("%d-%m-%Y %I:%M %p")
        ])

    return Response(
        out.getvalue(),
        mimetype='text/csv',
        headers={"Content-Disposition": "attachment; filename=splitpay_received.csv"}
    )


@app.route('/api/merchant/settle', methods=['POST'])
def merchant_settle():
    user = merchant_only()
    if not user:
        return jsonify({"status": "error", "message": "Unauthorized"}), 401

    data = request.json or {}
    pin = data.get('pin')
    account = (data.get('account_no') or '').strip()
    ifsc = (data.get('ifsc') or '').strip().upper()
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid amount"}), 400

    if user.upi_pin != pin:
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400
    if amount <= 0:
        return jsonify({"status": "error", "message": "Invalid amount"}), 400
    if amount > user.balance:
        return jsonify({"status": "error", "message": "Insufficient balance"}), 400

    # First settlement: save the bank details on the merchant account
    if not user.bank_account:
        if not account or not ifsc:
            return jsonify({"status": "error", "message": "Enter bank account and IFSC"}), 400
        if User.query.filter(User.bank_account == account, User.id != user.id).first():
            return jsonify({"status": "error", "message": "Bank account already linked to another user"}), 400
        user.bank_account = account
        user.ifsc_code = ifsc

    utr = str(random.randint(10**11, 10**12 - 1))
    user.balance -= amount

    txn = Transaction(
        txn_id=str(uuid.uuid4())[:8],
        sender_upi=user.upi_id,
        receiver_upi=f"bank{user.bank_account[-4:]}@settlement",
        amount=amount,
        split_data=json.dumps([amount]),
        split_utrs=json.dumps([utr])
    )
    db.session.add(txn)
    db.session.commit()

    return jsonify({"status": "success", "utr": utr, "balance": user.balance})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True, ssl_context='adhoc')