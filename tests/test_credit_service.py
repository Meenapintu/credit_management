from __future__ import annotations

import pytest

from credit_management.cache.memory import InMemoryAsyncCache
from credit_management.db.memory import InMemoryDBManager
from credit_management.logging.ledger_logger import LedgerLogger
from credit_management.services.credit_service import CreditService


@pytest.mark.asyncio
async def test_add_and_deduct_credits(tmp_path):
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=tmp_path / "ledger.log")
    cache = InMemoryAsyncCache()
    service = CreditService(db=db, ledger=ledger, cache=cache)

    user_id = "user-1"

    tx_add = await service.add_credits(user_id=user_id, amount=100)
    assert tx_add.current_credits == 100

    balance = await service.get_user_credits(user_id)
    assert balance == 100

    tx_deduct = await service.deduct_credits(user_id=user_id, amount=40)
    assert tx_deduct.current_credits == 60

    balance_after = await service.get_user_credits(user_id)
    assert balance_after == 60

