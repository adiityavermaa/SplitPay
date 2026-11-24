from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import sqlite3
import time
import os

app = Flask(__name__)
app.secret_key = 'splitpay_super_secret_key'

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('splitpay.db')
    c = conn.cursor()
    # Users table
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (phone TEXT PRIMARY KEY, 
                  balance REAL DEFAULT 50000.0,
                  upi_pin TEXT DEFAULT NULL,
                  upi_id TEXT UNIQUE)''')
    # Transactions table
    c.execute('''CREATE TABLE IF NOT EXISTS transactions
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sender TEXT, 
                  receiver TEXT, 
                  amount REAL, 
                  status TEXT,
                  timestamp TEXT)''')
    
    # Add a default merchant (Cafe Mocha) so you have someone to pay
    c.execute("INSERT OR IGNORE INTO users (phone, upi_id, balance) VALUES ('9999999999', 'cafe.mocha@mockupi', 0.0)")
    conn.commit()
    conn.close()

# Initialize DB when app starts
with app.app_context():
    init_db()

# --- ROUTES ---
@app.route('/')
def root():
    if 'user_phone' not in session:
        return redirect(url_for('login_page'))
    if not session.get('has_pin'):
        return redirect(url_for('setup_pin_page'))
    return render_template('index.html', phone=session['user_phone'])

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/setup-pin')
def setup_pin_page():
    if 'user_phone' not in session:
        return redirect(url_for('login_page'))
    return render_template('setup_pin.html')

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
        
    conn = sqlite3.connect('splitpay.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
    user = c.fetchone()
    
    if not user:
        upi_id = f"user{phone[-4:]}@mockupi"
        c.execute("INSERT INTO users (phone, upi_id) VALUES (?, ?)", (phone, upi_id))
        conn.commit()
        c.execute("SELECT * FROM users WHERE phone = ?", (phone,))
        user = c.fetchone()
    conn.close()
    
    session['user_phone'] = phone
    session['upi_id'] = user[3]
    session['has_pin'] = True if user[2] else False
    
    if user[2]:
        return jsonify({"status": "success", "redirect": "/"})
    else:
        return jsonify({"status": "success", "redirect": "/setup-pin"})

@app.route('/api/set-pin', methods=['POST'])
def set_pin():
    pin = request.json.get('pin')
    conn = sqlite3.connect('splitpay.db')
    c = conn.cursor()
    c.execute("UPDATE users SET upi_pin = ? WHERE phone = ?", (pin, session['user_phone']))
    conn.commit()
    conn.close()
    session['has_pin'] = True
    return jsonify({"status": "success", "redirect": "/"})

@app.route('/api/get-balance')
def get_balance():
    conn = sqlite3.connect('splitpay.db')
    c = conn.cursor()
    c.execute("SELECT balance, upi_id FROM users WHERE phone = ?", (session['user_phone'],))
    user = c.fetchone()
    conn.close()
    return jsonify({"balance": user[0], "upi_id": user[1]})

# --- PAYMENT API & SPLIT ENGINE ---
@app.route('/api/pay', methods=['POST'])
def process_payment():
    data = request.json
    receiver = data.get('receiver')
    amount = float(data.get('amount'))
    pin = data.get('pin')
    
    conn = sqlite3.connect('splitpay.db')
    c = conn.cursor()
    
    c.execute("SELECT balance, upi_pin FROM users WHERE phone = ?", (session['user_phone'],))
    sender_data = c.fetchone()
    
    if not sender_data or sender_data[1] != pin:
        conn.close()
        return jsonify({"status": "error", "message": "Incorrect UPI PIN"}), 400
    if sender_data[0] < amount:
        conn.close()
        return jsonify({"status": "error", "message": "Insufficient Balance"}), 400
        
    c.execute("UPDATE users SET balance = balance - ? WHERE phone = ?", (amount, session['user_phone']))
    c.execute("UPDATE users SET balance = balance + ? WHERE upi_id = ?", (amount, receiver))
    c.execute("INSERT INTO transactions (sender, receiver, amount, status, timestamp) VALUES (?, ?, ?, 'SUCCESS', ?)",
              (session['upi_id'], receiver, amount, time.strftime("%H:%M:%S")))
    conn.commit()
    conn.close()
    
    # AI SPLIT ENGINE
    split_chunks = []
    remaining = amount
    while remaining > 2000:
        split_chunks.append(2000)
        remaining -= 2000
    if remaining > 0:
        split_chunks.append(round(remaining, 2))
        
    return jsonify({"status": "success", "split": split_chunks})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)