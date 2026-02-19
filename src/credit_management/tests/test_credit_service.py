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


@pytest.mark.asyncio
async def test_credit_info_cache_update(tmp_path):
    """Verify that credit info cache is updated (not just invalidated) on all credit modifications."""
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=tmp_path / "ledger.log")
    cache = InMemoryAsyncCache()
    service = CreditService(db=db, ledger=ledger, cache=cache)

    user_id = "user-1"

    # Initial state - no cache, fetches from DB
    info1 = await service.get_user_credits_info(user_id)
    assert info1.balance == 0
    assert info1.reserved == 0
    assert info1.available == 0

    # Add credits - cache should be updated directly (balance +100, reserved unchanged)
    await service.add_credits(user_id=user_id, amount=100)
    # This call should return from cache (no DB call)
    info2 = await service.get_user_credits_info(user_id)
    assert info2.balance == 100
    assert info2.reserved == 0
    assert info2.available == 100

    # Reserve credits - cache should be updated directly (balance unchanged, reserved +30)
    r1 = await service.reserve_credits(user_id=user_id, amount=30)
    # This call should return from cache (no DB call)
    info3 = await service.get_user_credits_info(user_id)
    assert info3.balance == 100
    assert info3.reserved == 30
    assert info3.available == 70

    # Deduct credits - cache should be updated directly (balance -20, reserved unchanged)
    await service.deduct_credits(user_id=user_id, amount=20)
    # This call should return from cache (no DB call)
    info4 = await service.get_user_credits_info(user_id)
    assert info4.balance == 80
    assert info4.reserved == 30
    assert info4.available == 50

    # Unreserve credits - cache should be updated directly (balance unchanged, reserved -30)
    await service.unreserve_credits(r1)
    # This call should return from cache (no DB call)
    info5 = await service.get_user_credits_info(user_id)
    assert info5.balance == 80
    assert info5.reserved == 0
    assert info5.available == 80

    # Reserve and commit - cache should be updated directly (balance -10, reserved -10)
    r2 = await service.reserve_credits(user_id=user_id, amount=10)
    await service.commit_reserved_credits(r2)
    # This call should return from cache (no DB call)
    info6 = await service.get_user_credits_info(user_id)
    assert info6.balance == 70
    assert info6.reserved == 0
    assert info6.available == 70


@pytest.mark.asyncio
async def test_deduct_credits_after_service_allows_negative(tmp_path):
    """Verify deduct_credits_after_service allows balance to go negative."""
    db = InMemoryDBManager()
    ledger = LedgerLogger(db=db, file_path=tmp_path / "ledger.log")
    service = CreditService(db=db, ledger=ledger)

    user_id = "user-1"
    await service.add_credits(user_id=user_id, amount=50)
    assert await service.get_user_credits(user_id) == 50

    # Regular deduct_credits should fail if insufficient
    with pytest.raises(ValueError, match="insufficient credits"):
        await service.deduct_credits(user_id=user_id, amount=60)

    # deduct_credits_after_service should allow negative balance
    tx = await service.deduct_credits_after_service(user_id=user_id, amount=60)
    assert tx.current_credits == -10
    assert await service.get_user_credits(user_id) == -10

    # Can go even more negative
    tx2 = await service.deduct_credits_after_service(user_id=user_id, amount=20)
    assert tx2.current_credits == -30
    assert await service.get_user_credits(user_id) == -30
