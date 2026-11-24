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
    role = db.Column(db.String(10), default='customer')

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    txn_id = db.Column(db.String(50), unique=True, default=lambda: f"TXN{uuid.uuid4().hex[:10].upper()}")
    sender_upi = db.Column(db.String(50), nullable=False)
    receiver_upi = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default='SUCCESS')
    timestamp = db.Column(db.DateTime, default=datetime.now)
    split_data = db.Column(db.String(255), nullable=True) 
    split_utrs = db.Column(db.String(255), nullable=True) # NEW: Store the exact UTRs