# References

Every entry was checked on 2026-11-06 against the publisher's page, a library record or the live documentation page.

## Dependability and reliability

1. A. Avizienis, J.-C. Laprie, B. Randell, C. Landwehr, "Basic Concepts and Taxonomy of Dependable and Secure
   Computing," *IEEE Transactions on Dependable and Secure Computing*, vol. 1, no. 1, pp. 11–33, 2004.
   doi:10.1109/TDSC.2004.2
2. B. Beyer, C. Jones, J. Petoff, N. R. Murphy (eds.), *Site Reliability Engineering: How Google Runs Production
   Systems*, O'Reilly, 2016. Ch. 4 "Service Level Objectives", ch. 6 "Monitoring Distributed Systems", ch. 21 "Handling
   Overload". https://sre.google/sre-book/table-of-contents/
3. M. T. Nygard, *Release It! Design and Deploy Production-Ready Software*, 2nd ed., Pragmatic Bookshelf, 2018.
   Stability patterns: timeouts, circuit breakers, bulkheads.
4. J. A. Hanley, A. Lippman-Hand, "If Nothing Goes Wrong, Is Everything All Right? Interpreting Zero Numerators,"
   *JAMA*, vol. 249, no. 13, pp. 1743–1745, 1983. doi:10.1001/jama.1983.03330370053031 (rule of three).

## Kubernetes

5. Kubernetes documentation, "Liveness, Readiness, and Startup Probes."
   https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/
6. Kubernetes documentation, "Deployments" (rolling update, `maxSurge` rounded up, `maxUnavailable` rounded down, rollback).
   https://kubernetes.io/docs/concepts/workloads/controllers/deployment/
7. Kubernetes documentation, "Pod Lifecycle" (termination, preStop hooks, container restart back-off of 10 s, 20 s, 40 s,
   capped at 300 s). https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/
8. Kubernetes documentation, "Network Policies."
   https://kubernetes.io/docs/concepts/services-networking/network-policies/
9. kind documentation, "Quick Start." https://kind.sigs.k8s.io/docs/user/quick-start/

## nginx and fault injection

10. nginx documentation, "Module ngx_http_proxy_module" (`proxy_next_upstream`, `proxy_next_upstream_tries`,
    `proxy_connect_timeout`, `proxy_read_timeout`). https://nginx.org/en/docs/http/ngx_http_proxy_module.html
11. nginx documentation, "Module ngx_http_upstream_module" (`server … resolve`, open source since 1.27.3, `max_fails`,
    `fail_timeout`). https://nginx.org/en/docs/http/ngx_http_upstream_module.html
12. Linux manual page, "tc-netem(8)." https://man7.org/linux/man-pages/man8/tc-netem.8.html

## Data store and observability

13. PostgreSQL 16 documentation, "3.4. Transactions." https://www.postgresql.org/docs/16/tutorial-transactions.html
14. PostgreSQL 16 documentation, "pg_dump." https://www.postgresql.org/docs/16/app-pgdump.html
15. Prometheus documentation, "Alerting rules."
    https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/

## Security

16. A. Shostack, *Threat Modeling: Designing for Security*, Wiley, 2014. STRIDE.
17. OWASP, "OWASP Top 10 API Security Risks – 2023." https://api-security.owasp.org/editions/2023/en/0x11-t10/

## Libraries used by the system

18. `go-chi/chi` v5.3.0 and `go-chi/httprate` v0.15.0 (HTTP router, rate limiter), `jackc/pgx` v5.9.2 (PostgreSQL driver),
    `shopspring/decimal` v1.4.0 (money arithmetic). https://github.com/go-chi/chi, https://github.com/go-chi/httprate,
    https://github.com/jackc/pgx, https://github.com/shopspring/decimal. Versions: `go.mod` at commit `06aea177`.
