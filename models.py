from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    phone = db.Column(db.String(10), unique=True, nullable=False)
    upi_id = db.Column(db.String(50), unique=True, nullable=False)
    upi_pin = db.Column(db.String(4), nullable=True)
    balance = db.Column(db.Float, default=10000.0)
    role = db.Column(db.String(20), default='customer')
    bank_account = db.Column(db.String(30), nullable=True)
    ifsc_code = db.Column(db.String(20), nullable=True)


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    txn_id = db.Column(db.String(8), nullable=False)
    sender_upi = db.Column(db.String(50), nullable=False)
    receiver_upi = db.Column(db.String(50), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    split_data = db.Column(db.Text)
    split_utrs = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)