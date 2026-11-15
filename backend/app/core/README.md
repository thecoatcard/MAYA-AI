# Core foundation

Settings validate required connection URLs and a bounded probe timeout, mask URL fields,
and load dotenv files only when explicitly selected. Unknown dotenv keys are ignored so
the root example can also configure Compose.

The `maya` logger emits JSON with generated request/trace IDs. It redacts configured
connection strings/passwords and bearer values, omits exception messages and does not
serialize arbitrary log extras. Do not send request bodies or arbitrary secrets to logs.

Resources own the SQLAlchemy engine and Redis client. Startup verifies both services;
readiness reports an operational failure generically. Cleanup always attempts both pools,
and unexpected programmer errors and cancellation propagate.

`security.py`, `security_otp.py` and `security_tokens.py` hold the password, one-time-code and JWT primitives;
`ratelimit.py`, `email.py` and `clock.py` are the Redis limiter, the SMTP sender and the injectable time source used by the
[authentication module](../auth/README.md). Required settings now include two distinct signing keys of at least 32 bytes;
the logger redacts them and the SMTP password too.

See the [configuration reference](../../../docs/guides/CONFIGURATION.md).
