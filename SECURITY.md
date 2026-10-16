# Nexus Operative security and launch gates

**Development mode only:** The GUI and examples are simulated trading research.
No production bank deposits, live brokerage orders, or withdrawals are authorized by this repository.

- Never commit API keys, Plaid Link tokens, bank or card details, encrypted snapshots containing sensitive data, or live credentials.
- Use provider-hosted Plaid Link and broker-supported authenticated ACH funding; the application must not collect bank passwords or card numbers.
- The simulated funding scheduler does not transfer money.
- Future cloud workers must use scoped short-lived credentials, an independently revocable connection, audit logs, signed requests and strict spending limits.
- The PORTAL UI must not depict illustrative signal scores as probabilities of actual profit.
- Live trading needs explicit opt-in, brokerage approval, order/position exposure limits, human-independent kill-switch controls, liquidity checks, monitoring and independently verified reconciliation.
- Never enable GitHub Actions with production financial secrets.
- All pull requests require review before merge to main.
