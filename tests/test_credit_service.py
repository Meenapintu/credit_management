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


@pytest.mark.asyncio
async def test_no_overspend_when_reserving(tmp_path):
    """With balance 60, reserve 50 then reserve 55 must fail (available = 10)."""
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=tmp_path / "ledger.log")
    service = CreditService(db=db, ledger=ledger)

    user_id = "user-1"
    await service.add_credits(user_id=user_id, amount=60)
    assert await service.get_user_credits(user_id) == 60

    r1 = await service.reserve_credits(user_id=user_id, amount=50)
    assert r1.credits == 50

    with pytest.raises(ValueError, match="insufficient credits"):
        await service.reserve_credits(user_id=user_id, amount=55)

    await service.unreserve_credits(r1)
    r2 = await service.reserve_credits(user_id=user_id, amount=55)
    assert r2.credits == 55

    with pytest.raises(ValueError, match="insufficient credits"):
        await service.reserve_credits(user_id=user_id, amount=10)
