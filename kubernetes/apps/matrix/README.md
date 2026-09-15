# Matrix Services

This directory contains the Matrix homeserver (`continuwuity`) and its ecosystem (bridges, TURN server, LiveKit).

## Policy: E2EE Only

**End-to-End Encryption (E2EE) is mandatory for every service, bridge, and account.**

- All rooms must be encrypted. Unencrypted rooms are not permitted.
- Future bridges must be configured with E2EE support (persistent storage, crypto caching) enabled from the start.
- Any bot or appservice account must support Matrix E2EE. If a service cannot support E2EE, it cannot be deployed.