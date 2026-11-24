from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from models import db, User, Transaction
from sqlalchemy import inspect
import time
import random
import os

app = Flask(__name__)
app.secret_key = 'splitpay_super_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///splitpay.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# --- DATABASE SETUP & AUTO-RESET ---
with app.app_context():
    db_path = 'instance/splitpay.db'
    reset_db = False
    
    if os.path.exists(db_path):
        inspector = inspect(db.engine)
        try:
            columns = [col['name'] for col in inspector.get_columns('transactions')]
            # If we add new columns in the future, add them to this list
            if 'split_utrs' not in columns or 'txn_id' not in columns:
                reset_db = True
        except Exception:
            # If the table doesn't exist yet, we just create it
            reset_db = False
            
    if reset_db:
        print("⚠️ Schema changed! Resetting database...")
        db.drop_all() 
        db.create_all() 
    else:
        db.create_all() 

    # Seed default Merchant (Cafe Mocha)
    if not User.query.filter_by(upi_id='cafe.mocha@mockupi').first():
        merchant = User(phone='9999999999', upi_id='cafe.mocha@mockupi', balance=0.0, role='merchant')
        db.session.add(merchant)
        db.session.commit()

# --- ROUTES ---
@app.route('/')
def root():
    if 'user_phone' not in session:
        return redirect(url_for('login_page'))
    if not session.get('has_pin'):
        return redirect(url_for('setup_pin_page'))
    
    user = User.query.filter_by(phone=session['user_phone']).first()
    if not user:
        session.clear()
        return redirect(url_for('login_page'))
        
    # Fetch recent transactions for the customer
    txns = Transaction.query.filter_by(sender_upi=user.upi_id).order_by(Transaction.id.desc()).limit(10).all()
    return render_template('index.html', transactions=txns)

@app.route('/login')
def login_page(): return render_template('login.html')

@app.route('/setup-pin')
def setup_pin_page(): return render_template('setup_pin.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# --- AUTHENTICATION APIs ---
@app.route('/api/request-otp', methods=['POST'])
def request_otp():
    return jsonify({"status": "success", "message": "OTP sent successfully. Use 123456"})

@app.route('/api/verify-otp', methods=['POST'])
def verify_otp():
    data = request.json
    phone = data.get('phone')
    otp = data.get('otp')
    
    if otp != "123456":
        return jsonify({"status": "error", "message": "Invalid OTP"}), 400
        
    user = User.query.filter_by(phone=phone).first()
    
    if not user:
        upi_id = f"user{phone[-4:]}@mockupi"
        user = User(phone=phone, upi_id=upi_id)
        db.session.add(user)
        db.session.commit()
    
    session['user_phone'] = phone
    session['upi_id'] = user.upi_id
    session['has_pin'] = True if user.upi_pin else False
    session['role'] = user.role
    
    if user.upi_pin:
        return jsonify({"status": "success", "redirect": "/" if user.role == 'customer' else "/merchant"})
    return jsonify({"status": "success", "redirect": "/setup-pin"})

@app.route('/api/set-pin', methods=['POST'])
def set_pin():
    data = request.json
    user = User.query.filter_by(phone=session['user_phone']).first()
    user.upi_pin = data.get('pin')
    user.role = data.get('role', 'customer')
    db.session.commit()
    
    session['has_pin'] = True
    session['role'] = user.role
    
    redirect_url = "/merchant" if user.role == 'merchant' else "/"
    return jsonify({"status": "success", "redirect": redirect_url})

@app.route('/api/get-balance')
def get_balance():
    user = User.query.filter_by(phone=session['user_phone']).first()
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"balance": user.balance, "upi_id": user.upi_id})

# --- PAYMENT API & SPLIT ENGINE ---
@app.route('/api/pay', methods=['POST'])
def process_payment():
    data = request.json
    receiver_upi = data.get('receiver')
    amount = float(data.get('amount'))
    pin = data.get('pin')
    
    sender = User.query.filter_by(phone=session['user_phone']).first()
    receiver = User.query.filter_by(upi_id=receiver_upi).first()
    
    if not sender or sender.upi_pin != pin:
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400
    if not receiver:
        return jsonify({"status": "error", "message": "Receiver not found"}), 404
    if sender.balance < amount:
        return jsonify({"status": "error", "message": "Insufficient Balance"}), 400
        
    # 1. CALCULATE SPLIT
    split_chunks = []
    remaining = amount
    while remaining > 2000:
        split_chunks.append(2000)
        remaining -= 2000
    if remaining > 0:
        split_chunks.append(round(remaining, 2))
        
    split_string = ",".join(str(x) for x in split_chunks)

    # 2. GENERATE UNIQUE UTRs FOR EACH CHUNK
    utrs = []
    for _ in split_chunks:
        utr = f"UTR{int(time.time())}{random.randint(1000, 9999)}"
        utrs.append(utr)
    utr_string = ",".join(utrs)

    # 3. TRANSFER FUNDS & SAVE
    sender.balance -= amount
    receiver.balance += amount
    
    txn = Transaction(
        sender_upi=sender.upi_id, 
        receiver_upi=receiver.upi_id, 
        amount=amount, 
        split_data=split_string,
        split_utrs=utr_string 
    )
    db.session.add(txn)
    db.session.commit()
    
    return jsonify({
        "status": "success", 
        "split": split_chunks, 
        "utrs": utrs 
    })

# --- MERCHANT ROUTES ---
@app.route('/merchant')
def merchant_page():
    if 'user_phone' not in session:
        return redirect(url_for('login_page'))
    
    user = User.query.filter_by(phone=session['user_phone']).first()
    if not user or user.role != 'merchant':
        return "Access Denied. You are not a merchant.", 403
        
    return render_template('merchant.html', merchant_upi=user.upi_id)

@app.route('/api/merchant/received')
def merchant_received():
    if 'user_phone' not in session:
        return jsonify({"error": "Unauthorized"}), 401
        
    user = User.query.filter_by(phone=session['user_phone']).first()
    
    txns = Transaction.query.filter_by(receiver_upi=user.upi_id).order_by(Transaction.id.desc()).all()
    
    result = []
    for t in txns:
        result.append({
            "txn_id": t.txn_id,
            "sender": t.sender_upi.split('@')[0],
            "amount": t.amount,
            "date": t.timestamp.strftime('%b %d, %I:%M %p'),
            "split_data": t.split_data,
            "split_utrs": t.split_utrs
        })
    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, debug=True)