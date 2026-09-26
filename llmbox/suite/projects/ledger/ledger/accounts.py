class InsufficientFunds(Exception):
    pass


OVERDRAFT = {"checking": 50000, "savings": 0}


class Account:
    def __init__(self, name: str, kind: str = "checking", balance: int = 0):
        if kind not in OVERDRAFT:
            raise ValueError(f"unknown account kind {kind}")
        self.name, self.kind, self.balance = name, kind, balance

    def deposit(self, cents: int) -> None:
        if cents <= 0:
            raise ValueError("deposit must be positive")
        self.balance += cents

    def withdraw(self, cents: int) -> None:
        if cents <= 0:
            raise ValueError("withdrawal must be positive")
        if cents > self.balance + OVERDRAFT[self.kind]:  #@MUT acc-limit: if cents >= self.balance + OVERDRAFT[self.kind]:
            raise InsufficientFunds(self.name)
        self.balance -= cents

    def transfer(self, other: "Account", cents: int) -> None:
        self.withdraw(cents)  #@MUT acc-atomic: other.deposit(cents)
        other.deposit(cents)  #@MUT acc-atomic2: self.withdraw(cents)
