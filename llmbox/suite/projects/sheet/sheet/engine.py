"""Spreadsheet engine - see README.md for the full specification."""


class Sheet:
    def __init__(self):
        raise NotImplementedError

    def set(self, cell: str, raw: str) -> None:
        raise NotImplementedError

    def get(self, cell: str):
        raise NotImplementedError
