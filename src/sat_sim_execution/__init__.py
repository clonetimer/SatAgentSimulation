"""Engine-independent execution contracts introduced by A2R."""
from .contracts import EXECUTION_REQUEST_VERSION, ExecutionRequest, validate_adapter_key
from .errors import (
    AdapterNotFoundError,
    AdapterRegistrationError,
    AdapterResultError,
    ExecutionContractError,
    ExecutionRequestError,
)
from .port import ModelExecutionAdapter, ModelExecutionPort, RegistryExecutionPort
from .registry import AdapterFactory, ExecutionAdapterRegistry
from .result import RUN_RESULT_VERSION, RunResult, RunState

EXECUTION_CONTRACT_VERSION = "sat-sim.execution.v1"

__all__ = [
    "EXECUTION_CONTRACT_VERSION",
    "EXECUTION_REQUEST_VERSION",
    "RUN_RESULT_VERSION",
    "ExecutionRequest",
    "RunResult",
    "RunState",
    "ModelExecutionAdapter",
    "ModelExecutionPort",
    "RegistryExecutionPort",
    "ExecutionAdapterRegistry",
    "AdapterFactory",
    "validate_adapter_key",
    "ExecutionContractError",
    "ExecutionRequestError",
    "AdapterRegistrationError",
    "AdapterNotFoundError",
    "AdapterResultError",
]
