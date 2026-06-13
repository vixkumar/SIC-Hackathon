import streamlit as st
import json
import csv
import os
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
from collections import Counter
import random
from typing import Dict, List, Tuple, Optional

# ──────────────────────────────────────────────
# DataManager Class
# ──────────────────────────────────────────────
class DataManager:
    JSON_FILE = "accounts.json"
    CSV_FILE = "transactions.csv"

    @classmethod
    def load_accounts(cls) -> Dict[str, dict]:
        """Load raw accounts dictionaries from JSON file."""
        if os.path.exists(cls.JSON_FILE):
            with open(cls.JSON_FILE, "r") as f:
                data = json.load(f)
                if data:
                    return data
        return {}

    @classmethod
    def save_accounts(cls, accounts_dict: Dict[str, dict]):
        """Save accounts dictionaries to JSON file."""
        with open(cls.JSON_FILE, "w") as f:
            json.dump(accounts_dict, f, indent=4)

    @classmethod
    def log_transaction(cls, account_number: str, txn_type: str, amount: float, 
                        related_account: str = "", timestamp: str = None):
        """Append a single transaction row to the CSV file."""
        file_exists = os.path.exists(cls.CSV_FILE) and os.path.getsize(cls.CSV_FILE) > 0
        with open(cls.CSV_FILE, "a", newline="") as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(["Timestamp", "Account Number", "Transaction Type", 
                                 "Amount", "Related Account"])
            writer.writerow([
                timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                account_number,
                txn_type,
                amount,
                related_account
            ])


# ──────────────────────────────────────────────
# Account Class
# ──────────────────────────────────────────────
class Account:
    def __init__(self, account_number: str, name: str, password: str, balance: float, 
                 history: List[dict] = None, undo_stack: List[dict] = None):
        self.account_number = account_number
        self.name = name
        self.password = password
        self.balance = balance
        self.history = history if history is not None else []
        self.undo_stack = undo_stack if undo_stack is not None else []

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "password": self.password,
            "balance": self.balance,
            "history": self.history
        }

    @classmethod
    def from_dict(cls, account_number: str, data: dict, undo_stack: List[dict] = None):
        return cls(
            account_number=account_number,
            name=data["name"],
            password=data.get("password", ""),
            balance=data["balance"],
            history=data.get("history", []),
            undo_stack=undo_stack
        )

    def deposit(self, amount: float, timestamp: str = None):
        if amount <= 0:
            raise ValueError("Deposit amount must be greater than zero.")
        
        self.balance += amount
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn = {"type": "deposit", "amount": amount, "timestamp": ts}
        
        self.add_history(txn)
        self.push_undo(txn)

    def withdraw(self, amount: float, timestamp: str = None):
        if amount <= 0:
            raise ValueError("Withdrawal amount must be greater than zero.")
        if self.balance < amount:
            raise ValueError("Insufficient balance.")
            
        self.balance -= amount
        ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        txn = {"type": "withdraw", "amount": amount, "timestamp": ts}
        
        self.add_history(txn)
        self.push_undo(txn)

    def add_history(self, txn: dict):
        self.history.append(txn)

    def push_undo(self, txn: dict):
        self.undo_stack.append(txn)

    def pop_undo(self) -> Optional[dict]:
        if not self.undo_stack:
            return None
        return self.undo_stack.pop()

    def undo_last_transaction(self, banking_system) -> Tuple[bool, str]:
        last_txn = self.pop_undo()
        if not last_txn:
            return False, "No transactions to undo."
            
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if last_txn["type"] == "deposit":
            self.balance -= last_txn["amount"]
            undo_entry = {
                "type": "undo_deposit", 
                "amount": last_txn["amount"], 
                "timestamp": ts
            }
            self.add_history(undo_entry)
            DataManager.log_transaction(self.account_number, "undo_deposit", last_txn["amount"])
            return True, f"Deposit of ₹{last_txn['amount']} undone. New balance: ₹{self.balance:,.0f}"
            
        elif last_txn["type"] == "withdraw":
            self.balance += last_txn["amount"]
            undo_entry = {
                "type": "undo_withdraw", 
                "amount": last_txn["amount"], 
                "timestamp": ts
            }
            self.add_history(undo_entry)
            DataManager.log_transaction(self.account_number, "undo_withdraw", last_txn["amount"])
            return True, f"Withdrawal of ₹{last_txn['amount']} undone. New balance: ₹{self.balance:,.0f}"
            
        elif last_txn["type"] == "transfer":
            receiver_acc_num = last_txn["to"]
            amount = last_txn["amount"]
            
            receiver = banking_system.get_account(receiver_acc_num)
            if not receiver:
                self.push_undo(last_txn)
                return False, "Cannot undo transfer: receiver account not found."
                
            if receiver.balance < amount:
                self.push_undo(last_txn)
                return False, "Cannot undo transfer: receiver has insufficient balance."
                
            # Reverse the transfer
            receiver.balance -= amount
            self.balance += amount
            
            sender_undo = {
                "type": "undo_transfer", 
                "to": receiver_acc_num, 
                "amount": amount, 
                "timestamp": ts
            }
            self.add_history(sender_undo)
            
            receiver_undo = {
                "type": "undo_received_transfer", 
                "from": self.account_number, 
                "amount": amount, 
                "timestamp": ts
            }
            receiver.add_history(receiver_undo)
            
            DataManager.log_transaction(self.account_number, "undo_transfer", amount, receiver_acc_num)
            DataManager.log_transaction(receiver_acc_num, "undo_received_transfer", amount, self.account_number)
            return True, f"Transfer of ₹{amount} to {receiver_acc_num} undone."
            
        return False, "Unknown transaction type in undo stack."


# ──────────────────────────────────────────────
# BankingSystem Class
# ──────────────────────────────────────────────
class BankingSystem:
    def __init__(self, session_state):
        self.accounts: Dict[str, Account] = {}
        self.session_state = session_state
        self.load_state()

    def load_state(self):
        """Loads accounts from DataManager and reconstructs Account objects."""
        raw_accounts = DataManager.load_accounts()
        
        # Initialize seed data if empty
        if not raw_accounts:
            self.generate_seed_data()
            raw_accounts = DataManager.load_accounts()
            if not raw_accounts:
                raw_accounts = {}
        
        # Ensure session state has undo_stacks dictionary
        if "undo_stacks" not in self.session_state:
            self.session_state.undo_stacks = {}
            
        # Reconstruct Account objects
        for acc_num, data in raw_accounts.items():
            if acc_num not in self.session_state.undo_stacks:
                self.session_state.undo_stacks[acc_num] = []
            
            undo_stack_ref = self.session_state.undo_stacks[acc_num]
            self.accounts[acc_num] = Account.from_dict(acc_num, data, undo_stack_ref)
            
    def save_state(self):
        """Saves current Account objects back to DataManager."""
        accounts_dict = {acc_num: acc.to_dict() for acc_num, acc in self.accounts.items()}
        DataManager.save_accounts(accounts_dict)

    def generate_next_account_number(self) -> str:
        """Determines the next sequential account number."""
        if not self.accounts:
            return "1001"
        try:
            return str(max([int(k) for k in self.accounts.keys() if k.isdigit()]) + 1)
        except ValueError:
            return "1001"

    def get_account(self, account_number: str) -> Optional[Account]:
        return self.accounts.get(account_number)

    def get_all_accounts(self) -> Dict[str, Account]:
        return self.accounts

    def create_account(self, name: str, password: str, balance: float) -> Tuple[bool, str, Optional[str]]:
        if not name or not name.strip():
            return False, "Account holder name cannot be empty.", None
        if not password:
            return False, "Password cannot be empty.", None
        if balance < 0:
            return False, "Opening balance cannot be negative.", None

        acc_num = self.generate_next_account_number()
        
        if acc_num not in self.session_state.undo_stacks:
            self.session_state.undo_stacks[acc_num] = []
            
        undo_stack_ref = self.session_state.undo_stacks[acc_num]
        new_account = Account(acc_num, name.strip(), password, balance, [], undo_stack_ref)
        
        self.accounts[acc_num] = new_account
        self.save_state()
        
        return True, "Account created successfully.", acc_num

    def login(self, account_number: str, password: str) -> bool:
        account = self.get_account(account_number)
        if account and account.password == password:
            return True
        return False

    def transfer(self, sender_acc_num: str, receiver_acc_num: str, amount: float) -> Tuple[bool, str]:
        sender = self.get_account(sender_acc_num)
        receiver = self.get_account(receiver_acc_num)
        
        if not sender:
            return False, "Sender account not found."
        if not receiver:
            return False, "Receiver account not found."
        if sender_acc_num == receiver_acc_num:
            return False, "Sender and receiver cannot be the same account."
        if amount <= 0:
            return False, "Transfer amount must be greater than zero."
        if sender.balance < amount:
            return False, "Insufficient balance in sender's account."

        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Deduct from sender
        sender.balance -= amount
        sender_txn = {
            "type": "transfer", 
            "to": receiver_acc_num, 
            "amount": amount, 
            "timestamp": ts
        }
        sender.add_history(sender_txn)
        sender.push_undo(sender_txn)
        
        # Credit to receiver
        receiver.balance += amount
        receiver_txn = {
            "type": "received_transfer", 
            "from": sender_acc_num, 
            "amount": amount, 
            "timestamp": ts
        }
        receiver.add_history(receiver_txn)
        
        self.save_state()
        DataManager.log_transaction(sender_acc_num, "transfer", amount, receiver_acc_num, timestamp=ts)
        DataManager.log_transaction(receiver_acc_num, "received_transfer", amount, sender_acc_num, timestamp=ts)
        
        return True, f"₹{amount} transferred from {sender_acc_num} to {receiver_acc_num}."

    def generate_seed_data(self):
        """Generates initial seed data for demo purposes."""
        sample_data = {
            "1001": {"name": "Alice Johnson",   "password": "alice123",   "balance": 50000,  "history": []},
            "1002": {"name": "Bob Smith",       "password": "bob123",     "balance": 35000,  "history": []},
            "1003": {"name": "Charlie Davis",   "password": "charlie123", "balance": 72000,  "history": []},
            "1004": {"name": "Diana Brown",     "password": "diana123",   "balance": 15000,  "history": []},
            "1005": {"name": "Ethan Miller",    "password": "ethan123",   "balance": 90000,  "history": []},
            "1006": {"name": "Fiona Clark",     "password": "fiona123",   "balance": 28000,  "history": []},
            "1007": {"name": "George Taylor",   "password": "george123",  "balance": 61000,  "history": []},
            "1008": {"name": "Hannah Moore",    "password": "hannah123",  "balance": 43000,  "history": []},
            "1009": {"name": "Ian White",       "password": "ian123",     "balance": 8000,   "history": []},
            "1010": {"name": "Julia Wilson",    "password": "julia123",   "balance": 100000, "history": []},
        }

        now = datetime.now()
        def random_ts(months_ago_min, months_ago_max):
            days_offset = random.randint(months_ago_min * 30, months_ago_max * 30)
            dt = now - timedelta(days=days_offset, hours=random.randint(0, 23), minutes=random.randint(0, 59))
            return dt.strftime("%Y-%m-%d %H:%M:%S")

        sample_txns = [
            ("deposit",   "1001", 5000,  None,   5, 5),
            ("deposit",   "1003", 10000, None,   5, 5),
            ("withdraw",  "1005", 3000,  None,   5, 5),
            ("deposit",   "1002", 8000,  None,   4, 4),
            ("transfer",  "1010", 5000,  "1004", 4, 4),
            ("withdraw",  "1007", 2000,  None,   4, 4),
            ("deposit",   "1006", 12000, None,   3, 3),
            ("transfer",  "1001", 3000,  "1009", 3, 3),
            ("withdraw",  "1008", 5000,  None,   3, 3),
            ("deposit",   "1004", 7000,  None,   3, 3),
            ("transfer",  "1003", 8000,  "1002", 2, 2),
            ("deposit",   "1009", 15000, None,   2, 2),
            ("withdraw",  "1010", 10000, None,   2, 2),
            ("deposit",   "1005", 6000,  None,   2, 2),
            ("transfer",  "1007", 4000,  "1006", 1, 1),
            ("withdraw",  "1001", 2000,  None,   1, 1),
            ("deposit",   "1008", 9000,  None,   1, 1),
        ]

        # Apply transactions to sample_data
        for txn_def in sample_txns:
            txn_type = txn_def[0]
            ts = random_ts(txn_def[4], txn_def[5])

            if txn_type == "deposit":
                acc, amount = txn_def[1], txn_def[2]
                sample_data[acc]["balance"] += amount
                txn = {"type": "deposit", "amount": amount, "timestamp": ts}
                sample_data[acc]["history"].append(txn)
                DataManager.log_transaction(acc, "deposit", amount, timestamp=ts)

            elif txn_type == "withdraw":
                acc, amount = txn_def[1], txn_def[2]
                sample_data[acc]["balance"] -= amount
                txn = {"type": "withdraw", "amount": amount, "timestamp": ts}
                sample_data[acc]["history"].append(txn)
                DataManager.log_transaction(acc, "withdraw", amount, timestamp=ts)

            elif txn_type == "transfer":
                sender, amount, receiver = txn_def[1], txn_def[2], txn_def[3]
                
                sample_data[sender]["balance"] -= amount
                sender_txn = {"type": "transfer", "to": receiver, "amount": amount, "timestamp": ts}
                sample_data[sender]["history"].append(sender_txn)

                sample_data[receiver]["balance"] += amount
                receiver_txn = {"type": "received_transfer", "from": sender, "amount": amount, "timestamp": ts}
                sample_data[receiver]["history"].append(receiver_txn)

                DataManager.log_transaction(sender, "transfer", amount, receiver, timestamp=ts)
                DataManager.log_transaction(receiver, "received_transfer", amount, sender, timestamp=ts)

        DataManager.save_accounts(sample_data)

    def get_top_customers(self) -> List[Tuple[str, float]]:
        """Returns a list of tuples containing (Account Label, Balance) for top 5 customers."""
        sorted_accs = sorted(self.accounts.values(), key=lambda acc: acc.balance, reverse=True)[:5]
        return [(f"{acc.account_number} – {acc.name}", acc.balance) for acc in sorted_accs]

    def get_transaction_counts(self) -> Tuple[List[str], List[int]]:
        """Returns labels and counts for all transactions per customer."""
        acc_labels = [f"{acc.account_number} – {acc.name}" for acc in self.accounts.values()]
        txn_counts = [len(acc.history) for acc in self.accounts.values()]
        return acc_labels, txn_counts

    def get_total_balance(self) -> float:
        return sum(acc.balance for acc in self.accounts.values())

    def get_total_transactions(self) -> int:
        return sum(len(acc.history) for acc in self.accounts.values())


# ──────────────────────────────────────────────
# AnalyticsManager Class
# ──────────────────────────────────────────────
class AnalyticsManager:
    @staticmethod
    def monthly_transactions(account: Account):
        st.markdown("### Transactions by Month")
        if not account.history:
            st.warning("No transactions to analyse yet.")
            return

        with st.container():
            months = []
            for txn in account.history:
                try:
                    dt = datetime.strptime(txn["timestamp"], "%Y-%m-%d %H:%M:%S")
                    months.append(dt.strftime("%Y-%m"))
                except (ValueError, KeyError):
                    pass

            if months:
                month_counts = Counter(months)
                sorted_months = sorted(month_counts.keys())
                counts = [month_counts[m] for m in sorted_months]

                fig, ax = plt.subplots(figsize=(8, 4))
                ax.bar(sorted_months, counts, color="#1e3a8a", width=0.5, edgecolor="none", zorder=3)
                ax.set_xlabel("Month", fontsize=9, fontweight="bold", labelpad=8)
                ax.set_ylabel("Number of Transactions", fontsize=9, fontweight="bold", labelpad=8)
                ax.set_title("Monthly Transaction Count", fontsize=11, fontweight="bold", pad=12)
                ax.spines['top'].set_visible(False)
                ax.spines['right'].set_visible(False)
                ax.spines['left'].set_color('#cccccc')
                ax.spines['bottom'].set_color('#cccccc')
                ax.grid(axis='y', linestyle='--', alpha=0.5, zorder=0)
                plt.xticks(rotation=0, fontsize=8)
                plt.yticks(fontsize=8)
                plt.tight_layout()
                st.pyplot(fig)
            else:
                st.info("No timestamped transactions to chart.")

    @staticmethod
    def balance_trends(account: Account):
        st.markdown("### Balance History")
        if not account.history:
            return

        with st.container():
            running_balance = account.balance
            deltas = []
            for txn in account.history:
                t = txn["type"]
                amt = txn["amount"]
                if t in ("deposit", "undo_withdraw", "undo_received_transfer"):
                    deltas.append(amt)
                elif t in ("withdraw", "undo_deposit", "undo_transfer"):
                    deltas.append(-amt)
                elif t == "transfer":
                    deltas.append(-amt)
                elif t == "received_transfer":
                    deltas.append(amt)
                else:
                    deltas.append(0)

            initial_balance = running_balance - sum(deltas)
            balances = [initial_balance]
            for d in deltas:
                balances.append(balances[-1] + d)

            timestamps = ["Opening"]
            for txn in account.history:
                timestamps.append(txn.get("timestamp", "").split(" ")[0])

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(range(len(balances)), balances, marker="o", color="#3b82f6", linewidth=2.5, markersize=6, zorder=3)
            ax.set_xlabel("Transaction Sequence", fontsize=9, fontweight="bold", labelpad=8)
            ax.set_ylabel("Balance (₹)", fontsize=9, fontweight="bold", labelpad=8)
            ax.set_title(f"Balance Trend – {account.account_number} ({account.name})", fontsize=11, fontweight="bold", pad=12)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#cccccc')
            ax.spines['bottom'].set_color('#cccccc')
            ax.grid(True, linestyle='--', alpha=0.5, zorder=0)
            ax.set_xticks(range(len(balances)))
            ax.set_xticklabels(timestamps, rotation=45, ha="right", fontsize=7)
            plt.tight_layout()
            st.pyplot(fig)

    @staticmethod
    def top_customers(banking_system: BankingSystem):
        st.markdown("#### Top 5 Customers by Balance")
        top = banking_system.get_top_customers()
        if not top:
            return
            
        with st.container():
            names, balances = zip(*top)
            fig, ax = plt.subplots(figsize=(8, 4))
            bars = ax.barh(names, balances, color="#10b981", height=0.5, zorder=3)
            ax.set_xlabel("Balance (₹)", fontsize=9, fontweight="bold", labelpad=8)
            ax.set_title("Top 5 Customers by Balance", fontsize=11, fontweight="bold", pad=12)
            ax.invert_yaxis()
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#cccccc')
            ax.spines['bottom'].set_color('#cccccc')
            ax.grid(axis='x', linestyle='--', alpha=0.5, zorder=0)
            for bar, bal in zip(bars, balances):
                ax.text(bar.get_width() + max(balances) * 0.01,
                        bar.get_y() + bar.get_height() / 2,
                        f"₹{bal:,.0f}", va="center", fontsize=8, fontweight="bold")
            plt.tight_layout()
            st.pyplot(fig)

    @staticmethod
    def transaction_counts(banking_system: BankingSystem):
        st.markdown("#### Transaction Counts by Customer")
        acc_labels, txn_counts = banking_system.get_transaction_counts()
        
        if not acc_labels:
            return

        with st.container():
            fig, ax = plt.subplots(figsize=(8, 4))
            ax.bar(acc_labels, txn_counts, color="#1e3a8a", width=0.5, zorder=3)
            ax.set_xlabel("Customer", fontsize=9, fontweight="bold", labelpad=8)
            ax.set_ylabel("Number of Transactions", fontsize=9, fontweight="bold", labelpad=8)
            ax.set_title("Transaction Counts by Customer", fontsize=11, fontweight="bold", pad=12)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#cccccc')
            ax.spines['bottom'].set_color('#cccccc')
            ax.grid(axis='y', linestyle='--', alpha=0.5, zorder=0)
            plt.xticks(rotation=45, ha="right", fontsize=8)
            plt.tight_layout()
            st.pyplot(fig)


# Streamlit UI Layer
# ──────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Banking System", layout="wide")

    # Inject custom CSS for premium styling
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        
        html, body, [class*="css"] {
            font-family: 'Inter', sans-serif;
        }
        
        /* Premium Card style for metrics */
        div[data-testid="stMetric"] {
            background-color: #ffffff;
            border: 1px solid #e2e8f0;
            padding: 16px 20px;
            border-radius: 12px;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
        }
        
        div[data-testid="stMetricLabel"] {
            font-size: 0.75rem !important;
            color: #64748b !important;
            font-weight: 600 !important;
            text-transform: uppercase !important;
            letter-spacing: 0.05em !important;
        }
        
        div[data-testid="stMetricValue"] {
            font-size: 1.5rem !important;
            color: #0f172a !important;
            font-weight: 700 !important;
        }

        /* Bordered Container */
        div[data-testid="stVerticalBlock"] > div[style*="border"] {
            border-radius: 12px !important;
            border-color: #e2e8f0 !important;
            box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05) !important;
            padding: 24px !important;
        }
        
        /* Clean corporate buttons */
        .stButton>button {
            background-color: #1e3a8a !important;
            color: #ffffff !important;
            font-weight: 500 !important;
            border-radius: 6px !important;
            border: none !important;
            padding: 8px 16px !important;
            transition: all 0.2s ease-in-out !important;
            width: auto !important;
        }
        
        .stButton>button:hover {
            background-color: #1d4ed8 !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1) !important;
        }
        
        /* Header titles */
        h1, h2, h3, h4 {
            color: #0f172a !important;
            font-weight: 600 !important;
            letter-spacing: -0.02em !important;
        }
        
        /* Sidebar layout override */
        section[data-testid="stSidebar"] {
            background-color: #0f172a;
        }
        section[data-testid="stSidebar"] h1, 
        section[data-testid="stSidebar"] h2, 
        section[data-testid="stSidebar"] h3,
        section[data-testid="stSidebar"] h4,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] p {
            color: #f8fafc !important;
        }
        </style>
    """, unsafe_allow_html=True)

    # ── Initialize System ─────────────────────
    banking_system = BankingSystem(st.session_state)

    # ── Session state defaults ────────────────
    if "role" not in st.session_state:
        st.session_state.role = None
    if "logged_in_account" not in st.session_state:
        st.session_state.logged_in_account = None
    if "user_action" not in st.session_state:
        st.session_state.user_action = "Login to Existing Account"

    # ══════════════════════════════════════════
    # ROLE SELECTION
    # ══════════════════════════════════════════
    st.sidebar.markdown("### Banking System")

    role = st.sidebar.selectbox(
        "Role Selection",
        ["User", "Admin"],
        index=(0 if st.session_state.role is None
               else (["User", "Admin"].index(st.session_state.role)
                     if st.session_state.role in ("User", "Admin") else 0)),
        key="role_select"
    )

    st.session_state.role = role

    # ══════════════════════════════════════════
    # ADMIN ROLE
    # ══════════════════════════════════════════
    if role == "Admin":
        st.session_state.logged_in_account = None
        st.markdown("### Admin Dashboard")
        st.markdown("---")
        
        if not banking_system.get_all_accounts():
            st.warning("No accounts in the system.")
            return

        # ── Overall Bank Summary Metrics ──────────
        with st.container():
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Accounts", len(banking_system.get_all_accounts()))
            col2.metric("Total Bank Balance", f"₹{banking_system.get_total_balance():,.0f}")
            col3.metric("Total Transactions", banking_system.get_total_transactions())

        st.markdown("---")
        
        col_left, col_right = st.columns(2)
        with col_left:
            AnalyticsManager.top_customers(banking_system)
            
            st.markdown("#### Ranked Customer Leaderboard")
            top_customers_data = banking_system.get_top_customers()
            ranked_list = []
            for rank, item in enumerate(top_customers_data, 1):
                label, bal = item
                parts = label.split(" – ")
                if len(parts) == 2:
                    acc_num, name = parts
                else:
                    parts = label.split(" - ")
                    acc_num, name = parts[0], parts[1] if len(parts) > 1 else label
                ranked_list.append({
                    "Rank": rank,
                    "Customer Name": name,
                    "Account Number": acc_num,
                    "Balance": f"₹{bal:,.0f}"
                })
            st.dataframe(ranked_list, use_container_width=True, hide_index=True)
            
        with col_right:
            AnalyticsManager.transaction_counts(banking_system)
            
            st.markdown("#### Customer Transaction Volume")
            acc_labels, txn_counts = banking_system.get_transaction_counts()
            txn_volume_data = []
            for label, count in zip(acc_labels, txn_counts):
                parts = label.split(" – ")
                if len(parts) == 2:
                    acc_num, name = parts
                else:
                    parts = label.split(" - ")
                    acc_num, name = parts[0], parts[1] if len(parts) > 1 else label
                txn_volume_data.append({
                    "Customer": name,
                    "Account Number": acc_num,
                    "Transaction Count": count
                })
            st.dataframe(txn_volume_data, use_container_width=True, hide_index=True)
        return

    # ══════════════════════════════════════════
    # USER ROLE
    # ══════════════════════════════════════════
    logged_in_acc_num = st.session_state.logged_in_account

    if logged_in_acc_num is None:
        st.markdown("### User Portal")
        st.markdown("---")
        
        user_action = st.sidebar.radio(
            "Navigation",
            ["Login to Existing Account", "Create Account"],
            key="user_action"
        )

        # ── Create Account ────────────────────
        if user_action == "Create Account":
            st.markdown("#### Create New Account")
            col_form, _ = st.columns([1.5, 2])
            with col_form:
                with st.container(border=True):
                    name = st.text_input("Name")
                    password = st.text_input("Password", type="password")
                    confirm_password = st.text_input("Confirm Password", type="password")
                    balance = st.number_input("Opening Balance", min_value=0.0, value=0.0, step=100.0)
                    
                    if st.button("Create Account"):
                        if password != confirm_password:
                            st.error("Passwords do not match.")
                        else:
                            ok, msg, new_acc = banking_system.create_account(name, password, balance)
                            if ok:
                                st.success(f"✓ {msg}\n\nAccount Number: {new_acc}\n\nPlease note this account number for future logins.")
                                if st.button("Proceed to Login"):
                                    st.session_state.user_action = "Login to Existing Account"
                                    st.rerun()
                            else:
                                st.error(msg)

        # ── Login ─────────────────────────────
        else:
            st.markdown("#### Login")
            col_form, _ = st.columns([1.5, 2])
            with col_form:
                with st.container(border=True):
                    st.markdown("Use your Account Number and Password to access your account.")
                    acc_number = st.text_input("Account Number")
                    password = st.text_input("Password", type="password")
                    
                    if st.button("Login"):
                        if banking_system.login(acc_number, password):
                            st.session_state.logged_in_account = acc_number
                            st.session_state.logged_in = True
                            st.rerun()
                        else:
                            st.error("Invalid Account Number or Password.")

            # ── Demo credentials expander ─────
            accounts_dict = banking_system.get_all_accounts()
            seed_ids = {"1001", "1002", "1003", "1004", "1005", "1006", "1007", "1008", "1009", "1010"}
            
            if seed_ids.issubset(accounts_dict.keys()):
                col_exp, _ = st.columns([1.5, 2])
                with col_exp:
                    with st.expander("Demo Account Credentials"):
                        creds = []
                        for sid in sorted(seed_ids):
                            creds.append({
                                "Account Number": sid,
                                "Name": accounts_dict[sid].name,
                                "Password": accounts_dict[sid].password
                            })
                        st.dataframe(creds, hide_index=True)
        return

    # ══════════════════════════════════════════
    # USER DASHBOARD (logged-in)
    # ══════════════════════════════════════════
    account = banking_system.get_account(logged_in_acc_num)

    if not account:
        st.session_state.logged_in_account = None
        st.session_state.logged_in = False
        st.rerun()
        return

    # ── Account Summary Header ────────────────
    st.markdown(f"### Welcome, {account.name}")
    
    with st.container():
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Account Holder Name", account.name)
        col2.metric("Account Number", account.account_number)
        col3.metric("Current Balance", f"₹{account.balance:,.0f}")
        col4.metric("Total Transactions", len(account.history))
        
    st.markdown("---")

    if st.sidebar.button("Logout"):
        st.session_state.logged_in_account = None
        st.session_state.logged_in = False
        st.rerun()

    menu = st.sidebar.selectbox(
        "Navigation Menu",
        ["Dashboard", "Deposit", "Withdraw", "Transfer Funds",
         "Transaction History", "Undo Transaction", "Analytics"],
        key="dashboard_menu"
    )

    # ── Dashboard Overview ────────────────────
    if menu == "Dashboard":
        st.markdown("#### Dashboard Overview")
        st.markdown("Welcome to your secure banking portal. Use the sidebar menu to deposit, withdraw, transfer funds, reverse recent transactions, or check analytics trends.")
        
        st.markdown("#### Quick Summary")
        col_sum1, col_sum2 = st.columns(2)
        with col_sum1:
            with st.container(border=True):
                st.markdown("##### Account Status")
                st.write(f"**Status:** Active")
                st.write(f"**Interest Group:** Standard Tier")
                st.write(f"**Last active:** {datetime.now().strftime('%Y-%m-%d')}")
        with col_sum2:
            with st.container(border=True):
                st.markdown("##### Recent Activity")
                if account.history:
                    last_txn = account.history[-1]
                    st.write(f"**Last Action:** {last_txn['type'].replace('_', ' ').title()}")
                    st.write(f"**Amount:** ₹{last_txn['amount']:,.0f}")
                    st.write(f"**Time:** {last_txn.get('timestamp', 'N/A')}")
                else:
                    st.write("No transactions recorded yet.")

    # ── Deposit ───────────────────────────────
    elif menu == "Deposit":
        st.markdown("#### Deposit Funds")
        col_form, _ = st.columns([1.5, 2])
        with col_form:
            with st.container(border=True):
                st.metric("Current Balance", f"₹{account.balance:,.0f}")
                amount = st.number_input("Amount (₹)", min_value=0.0, value=0.0, step=100.0)
                if st.button("Submit Deposit"):
                    try:
                        account.deposit(amount)
                        banking_system.save_state()
                        DataManager.log_transaction(account.account_number, "deposit", amount)
                        st.success(f"₹{amount:,.0f} deposited successfully.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    # ── Withdraw ──────────────────────────────
    elif menu == "Withdraw":
        st.markdown("#### Withdraw Funds")
        col_form, _ = st.columns([1.5, 2])
        with col_form:
            with st.container(border=True):
                st.metric("Current Balance", f"₹{account.balance:,.0f}")
                amount = st.number_input("Amount (₹)", min_value=0.0, value=0.0, step=100.0)
                if st.button("Submit Withdrawal"):
                    try:
                        account.withdraw(amount)
                        banking_system.save_state()
                        DataManager.log_transaction(account.account_number, "withdraw", amount)
                        st.success(f"₹{amount:,.0f} withdrawn successfully.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

    # ── Transfer Funds ────────────────────────
    elif menu == "Transfer Funds":
        st.markdown("#### Transfer Funds")
        col_form, _ = st.columns([1.5, 2])
        with col_form:
            with st.container(border=True):
                st.metric("Current Balance", f"₹{account.balance:,.0f}")
                recipient_acc_num = st.text_input("Recipient Account Number", key="transfer_recipient_acc")
                amount = st.number_input("Amount (₹)", min_value=0.0, value=0.0, step=100.0, key="transfer_amount_val")
                
                if recipient_acc_num:
                    recipient = banking_system.get_account(recipient_acc_num)
                    if not recipient:
                        st.error("Recipient account not found.")
                    elif recipient_acc_num == account.account_number:
                        st.error("Sender and receiver cannot be the same account.")
                    else:
                        st.success(f"✓ Recipient Verified. Account Holder: {recipient.name}")
                        
                        # Show review block
                        st.markdown("---")
                        st.markdown("**Transfer Details Review**")
                        st.write(f"**From Account:** {account.account_number}")
                        st.write(f"**To Account:** {recipient.account_number}")
                        st.write(f"**Recipient:** {recipient.name}")
                        st.write(f"**Amount:** ₹{amount:,.0f}")
                        st.markdown("---")
                        
                        if amount <= 0:
                            st.warning("Transfer amount must be greater than zero.")
                        elif account.balance < amount:
                            st.error("Insufficient balance in your account.")
                        else:
                            if st.button("Confirm & Execute Transfer"):
                                ok, msg = banking_system.transfer(account.account_number, recipient_acc_num, amount)
                                if ok:
                                    st.success(msg)
                                    st.rerun()
                                else:
                                    st.error(msg)

    # ── View Transaction History ──────────────
    elif menu == "Transaction History":
        st.markdown("#### Transaction History")
        if not account.history:
            st.info("No transactions yet for this account.")
        else:
            history_rows = []
            for txn in reversed(account.history):
                t = txn["type"]
                amt = txn["amount"]
                ts = txn.get("timestamp", "N/A")
                
                if t == "deposit":
                    disp_type = "Deposit"
                    desc = "Cash Deposit"
                    related = "-"
                elif t == "withdraw":
                    disp_type = "Withdrawal"
                    desc = "Cash Withdrawal"
                    related = "-"
                elif t == "transfer":
                    disp_type = "Transfer Out"
                    related = txn.get("to", "")
                    desc = f"Transferred to Account {related}"
                elif t == "received_transfer":
                    disp_type = "Transfer In"
                    related = txn.get("from", "")
                    desc = f"Received from Account {related}"
                elif t == "undo_deposit":
                    disp_type = "Deposit Reversal"
                    desc = "Deposit undone"
                    related = "-"
                elif t == "undo_withdraw":
                    disp_type = "Withdrawal Reversal"
                    desc = "Withdrawal undone"
                    related = "-"
                elif t == "undo_transfer":
                    disp_type = "Transfer Out Reversal"
                    related = txn.get("to", "")
                    desc = f"Reversed transfer to Account {related}"
                elif t == "undo_received_transfer":
                    disp_type = "Transfer In Reversal"
                    related = txn.get("from", "")
                    desc = f"Reversed transfer from Account {related}"
                else:
                    disp_type = t.replace("_", " ").title()
                    desc = "Banking Operation"
                    related = "-"
                
                history_rows.append({
                    "Date": ts,
                    "Transaction Type": disp_type,
                    "Amount": f"₹{amt:,.0f}",
                    "Related Account": related,
                    "Description": desc
                })
            
            st.dataframe(history_rows, use_container_width=True, hide_index=True)

    # ── Undo Last Transaction ─────────────────
    elif menu == "Undo Transaction":
        st.markdown("#### Undo Transaction")
        col_form, _ = st.columns([1.5, 2])
        with col_form:
            with st.container(border=True):
                if account.undo_stack:
                    last_txn = account.undo_stack[-1]
                    t = last_txn['type'].replace('_', ' ').title()
                    amt = f"₹{last_txn['amount']:,.0f}"
                    ts = last_txn.get('timestamp', 'N/A')
                    
                    st.markdown("##### Last Undoable Transaction")
                    st.write(f"**Type:** {t}")
                    st.write(f"**Amount:** {amt}")
                    st.write(f"**Date:** {ts}")
                    st.markdown("---")
                    
                    if st.button("Undo Transaction"):
                        ok, msg = account.undo_last_transaction(banking_system)
                        if ok:
                            banking_system.save_state()
                            st.success(f"✓ {msg}")
                            st.rerun()
                        else:
                            st.error(msg)
                else:
                    st.info("Undo stack is empty for this account. No reversible transactions found.")

    # ── My Analytics ──────────────────────────
    elif menu == "Analytics":
        st.markdown("#### Financial Analytics")
        col_a, col_b = st.columns(2)
        with col_a:
            AnalyticsManager.monthly_transactions(account)
        with col_b:
            AnalyticsManager.balance_trends(account)


if __name__ == "__main__":
    main()
