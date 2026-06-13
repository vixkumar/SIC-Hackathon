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
        st.markdown("### Monthly Transactions")
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
                ax.bar(sorted_months, counts, color="#4e79a7")
                ax.set_xlabel("Month")
                ax.set_ylabel("Number of Transactions")
                ax.set_title("Monthly Transaction Count")
                plt.xticks(rotation=45, ha="right")
                plt.tight_layout()
                st.pyplot(fig)
            else:
                st.info("No timestamped transactions to chart.")

    @staticmethod
    def balance_trends(account: Account):
        st.markdown("### Account Balance Trends")
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
                timestamps.append(txn.get("timestamp", ""))

            fig, ax = plt.subplots(figsize=(8, 4))
            ax.plot(range(len(balances)), balances, marker="o", color="#e15759", linewidth=2)
            ax.set_xlabel("Transaction Sequence")
            ax.set_ylabel("Balance (₹)")
            ax.set_title(f"Balance Trend – {account.account_number} ({account.name})")
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
            bars = ax.barh(names, balances, color="#59a14f")
            ax.set_xlabel("Balance (₹)")
            ax.set_title("Top 5 Customers by Balance")
            ax.invert_yaxis()
            for bar, bal in zip(bars, balances):
                ax.text(bar.get_width() + max(balances) * 0.01,
                        bar.get_y() + bar.get_height() / 2,
                        f"₹{bal:,.0f}", va="center", fontsize=9)
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
            ax.bar(acc_labels, txn_counts, color="#4e79a7")
            ax.set_xlabel("Customer")
            ax.set_ylabel("Number of Transactions")
            ax.set_title("Transaction Counts by Customer")
            plt.xticks(rotation=45, ha="right", fontsize=8)
            plt.tight_layout()
            st.pyplot(fig)


# ──────────────────────────────────────────────
# Streamlit UI Layer
# ──────────────────────────────────────────────
def main():
    st.set_page_config(page_title="Banking System", layout="wide")

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
        
        if not banking_system.get_all_accounts():
            st.warning("No accounts in the system.")
            return

        # ── Overall Bank Summary Metrics ──────────
        with st.container():
            st.markdown("#### Overall Bank Summary")
            col1, col2, col3 = st.columns(3)
            col1.metric("Total Accounts", len(banking_system.get_all_accounts()))
            col2.metric("Total Bank Balance", f"₹{banking_system.get_total_balance():,.0f}")
            col3.metric("Total Transactions", banking_system.get_total_transactions())

        st.markdown("---")
        
        AnalyticsManager.top_customers(banking_system)
        st.markdown("---")
        AnalyticsManager.transaction_counts(banking_system)
        return

    # ══════════════════════════════════════════
    # USER ROLE
    # ══════════════════════════════════════════
    logged_in_acc_num = st.session_state.logged_in_account

    if logged_in_acc_num is None:
        # ── User landing: Create Account or Login ─
        st.markdown("### User Portal")
        
        user_action = st.sidebar.radio(
            "Navigation",
            ["Login to Existing Account", "Create Account"],
            key="user_action"
        )

        # ── Create Account ────────────────────
        if user_action == "Create Account":
            st.markdown("#### Create New Account")
            
            with st.container():
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
            st.markdown("Use your Account Number and Password to access your account.")
            
            with st.container():
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

    # Guard against deleted/missing account
    if not account:
        st.session_state.logged_in_account = None
        st.error("Account no longer exists.")
        return

    st.markdown(f"### Welcome, {account.name}")
    st.markdown(f"**Account Number:** {account.account_number}")
    st.markdown(f"**Current Balance:** ₹{account.balance:,.0f}")
    st.markdown("---")

    if st.sidebar.button("Logout"):
        st.session_state.logged_in_account = None
        st.session_state.logged_in = False
        st.rerun()

    menu = st.sidebar.selectbox(
        "Dashboard",
        ["Deposit", "Withdraw", "Transfer",
         "Transaction History", "Undo Transaction",
         "Analytics"],
        key="dashboard_menu"
    )

    # ── Deposit ───────────────────────────────
    if menu == "Deposit":
        st.markdown("#### Deposit")
        with st.container():
            amount = st.number_input("Amount", min_value=0.0, value=0.0, step=100.0)
            if st.button("Deposit"):
                try:
                    account.deposit(amount)
                    banking_system.save_state()
                    DataManager.log_transaction(account.account_number, "deposit", amount)
                    st.success(f"₹{amount} deposited successfully. New balance: ₹{account.balance:,.0f}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    # ── Withdraw ──────────────────────────────
    elif menu == "Withdraw":
        st.markdown("#### Withdraw")
        with st.container():
            amount = st.number_input("Amount", min_value=0.0, value=0.0, step=100.0)
            if st.button("Withdraw"):
                try:
                    account.withdraw(amount)
                    banking_system.save_state()
                    DataManager.log_transaction(account.account_number, "withdraw", amount)
                    st.success(f"₹{amount} withdrawn successfully. New balance: ₹{account.balance:,.0f}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    # ── Transfer Funds ────────────────────────
    elif menu == "Transfer":
        st.markdown("#### Transfer")
        other_accs = [a for a in banking_system.get_all_accounts() if a != account.account_number]
        if not other_accs:
            st.warning("No other accounts available for transfer.")
        else:
            with st.container():
                receiver = st.selectbox(
                    "Receiver Account", 
                    other_accs,
                    format_func=lambda x: f"{x} – {banking_system.get_account(x).name}",
                    key="transfer_receiver"
                )
                amount = st.number_input("Amount", min_value=0.0, value=0.0, step=100.0)
                if st.button("Transfer"):
                    ok, msg = banking_system.transfer(account.account_number, receiver, amount)
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
            st.dataframe(account.history)

    # ── Undo Last Transaction ─────────────────
    elif menu == "Undo Transaction":
        st.markdown("#### Undo Transaction")
        if account.undo_stack:
            last_txn = account.undo_stack[-1]
            st.info(f"Last reversible transaction: **{last_txn['type']}** of ₹{last_txn['amount']}")
        else:
            st.info("Undo stack is empty for this account.")
            
        if st.button("Undo"):
            ok, msg = account.undo_last_transaction(banking_system)
            if ok:
                banking_system.save_state()
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    # ── My Analytics ──────────────────────────
    elif menu == "Analytics":
        AnalyticsManager.monthly_transactions(account)
        AnalyticsManager.balance_trends(account)


if __name__ == "__main__":
    main()
