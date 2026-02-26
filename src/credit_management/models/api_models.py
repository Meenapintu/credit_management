from pydantic import BaseModel


class AddCreditsRequest(BaseModel):
    user_id: str
    amount: float
    description: str | None = None


class DeductCreditsRequest(BaseModel):
    user_id: str
    amount: float
    description: str | None = None


class CreditBalanceResponse(BaseModel):
    user_id: str
    credits: float


class SubscriptionPlanRequest(BaseModel):
    name: str
    description: str | None = None
    credit_limit: float
    price: float
    billing_period: str
    validity_days: int


class SubscriptionPlanResponse(BaseModel):
    id: str
    name: str
    credit_limit: float
    price: float
    billing_period: str
    validity_days: int
