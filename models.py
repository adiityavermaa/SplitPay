from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import uuid

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(10), unique=True, nullable=False)
    upi_id = db.Column(db.String(50), unique=True, nullable=False)
    balance = db.Column(db.Float, default=50000.0)
    upi_pin = db.Column(db.String(4), nullable=True)
    role = db.Column(db.String(10), default='customer') # 'customer' or 'merchant'
    
    # NEW: Mock Bank Details for Bank Transfers
    bank_account = db.Column(db.String(20), default="1234567890")
    ifsc_code = db.Column(db.String(15), default="MOCK0001234")

    def to_dict(self):
        return {
            'phone': self.phone,
            'upi_id': self.upi_id,
            'balance': self.balance,
            'role': self.role
        }

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    txn_id = db.Column(db.String(50), unique=True, default=lambda: f"TXN{uuid.uuid4().hex[:10].upper()}")
    sender_upi = db.Column(db.String(50), nullable=False)
    receiver_upi = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='SUCCESS')
    timestamp = db.Column(db.DateTime, default=datetime.now)
    
    # Stores comma-separated values, e.g. "2000,2000,2000,500" and "UTR123...,UTR456..."
    split_data = db.Column(db.String(255), nullable=True) 
    split_utrs = db.Column(db.String(255), nullable=True) 