from argon2 import Parameters, Type

from app.core.security import PasswordService

# The smallest parameters argon2 accepts: tests exercise logic, not hashing cost.
FAST_PARAMETERS = Parameters(
    type=Type.ID,
    version=19,
    salt_len=16,
    hash_len=32,
    time_cost=1,
    memory_cost=8,
    parallelism=1,
)


def fast_password_service() -> PasswordService:
    return PasswordService(FAST_PARAMETERS)
