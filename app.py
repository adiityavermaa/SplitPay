import json
import uuid
import random
from flask import Flask, render_template, request, jsonify, session, redirect
from sqlalchemy import inspect
from models import db, User, Transaction

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///splitpay.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = 'splitpay_super_secret_key'

db.init_app(app)

SPLIT_LIMIT = 2000


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


def current_user():
    if 'user_phone' not in session:
        return None
    return User.query.filter_by(phone=session['user_phone']).first()


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
    return render_template('merchant.html')


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
    is_new = False
    if not user:
        is_new = True
        user = User(phone=phone, upi_id=f"user{phone[-4:]}@mockupi")
        db.session.add(user)
        db.session.commit()

    session['user_phone'] = phone
    needs_pin = is_new or not user.upi_pin
    return jsonify({"status": "success", "needs_pin": needs_pin, "role": user.role})


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
    return jsonify({"status": "success", "role": user.role})


# ---------------- BALANCE APIs ----------------

# Returns ONLY the UPI ID (balance is no longer leaked on page load)
@app.route('/api/get-balance')
def get_balance():
    if 'user_phone' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    user = User.query.filter_by(phone=session['user_phone']).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"upi_id": user.upi_id})


# Balance is returned only if the PIN matches the one stored in the DB
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
    receiver_upi = data.get('receiver_upi')
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

    receiver = User.query.filter_by(upi_id=receiver_upi).first()
    if not receiver:
        return jsonify({"status": "error", "message": "Receiver not found"}), 404
    if receiver.id == sender.id:
        return jsonify({"status": "error", "message": "Cannot pay yourself"}), 400

    chunks = []
    remaining = amount
    while remaining > SPLIT_LIMIT:
        chunks.append(float(SPLIT_LIMIT))
        remaining -= SPLIT_LIMIT
    if remaining > 0:
        chunks.append(round(remaining, 2))

    utrs = [str(random.randint(10**11, 10**12 - 1)) for _ in chunks]

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
    account = data.get('account')
    ifsc = data.get('ifsc')
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

    chunks = []
    remaining = amount
    while remaining > SPLIT_LIMIT:
        chunks.append(float(SPLIT_LIMIT))
        remaining -= SPLIT_LIMIT
    if remaining > 0:
        chunks.append(round(remaining, 2))
    utrs = [str(random.randint(10**11, 10**12 - 1)) for _ in chunks]

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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True, ssl_context='adhoc')