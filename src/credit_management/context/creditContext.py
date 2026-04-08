from contextvars import ContextVar
from dataclasses import dataclass
import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class LLMUsage:
    model: str
    provider: str
    cost: float
    metadata: Dict[str, Any]


# Thread-safe storage for LLM usage metadata via async ContextVar.
# Each async task gets its own copy. MUST be initialized with set([]) at
# the start of each request/job to avoid None checks.
llm_usage_context: ContextVar[Optional[List[LLMUsage]]] = ContextVar("llm_usage_context", default=None)


def _get_list() -> List[LLMUsage]:
    """Get or create the usage list for the current context."""
    val = llm_usage_context.get()
    if val is None:
        val = []
        llm_usage_context.set(val)
    return val


def addLlmUsage(model: str, provider: str, cost: float, metadata: Dict[str, Any]):
    """Record LLM usage metadata for credit deduction."""
    _get_list().append(LLMUsage(model=model, provider=provider, cost=cost, metadata=metadata))


def getLlmUsages() -> List[LLMUsage]:
    """Get accumulated LLM usage metadata for this context."""
    return _get_list()


def clearUsageContext():
    """Clear LLM usage for the current context."""
    llm_usage_context.set(None)


def takeLlmUsagesAndClear() -> List[LLMUsage]:
    """Atomically snapshot and clear LLM usage context."""
    usages = getLlmUsages()
    clearUsageContext()
    return usages


def initUsageContext():
    """Initialize a fresh LLM usage context."""
    llm_usage_context.set([])


def format_pretty_json(data: dict) -> str:
    """Returns a color-friendly, indented JSON string."""
    return "\n" + json.dumps(data, indent=4)
