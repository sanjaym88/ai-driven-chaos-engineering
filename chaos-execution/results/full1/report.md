# Fault campaign full1

- Cases: **100**, per-request records: **27420**
- Outcomes: {'completed': 59, 'aborted': 41}
- Toxiproxy clean before every case: **True**, after every case: **True**

Latency percentiles use successful requests only. Recovery time = seconds from fault removal to the first of 5 consecutive good requests (within 1.5x baseline p95).

| fault | intensity | cases | baseline p95 | fault success | fault p50 / p95 / p99 ms | recovery p50 / max s | outcomes |
|---|---|---|---|---|---|---|---|
| latency | 50 | 5 | 30.8 | 100% | 62.2 / 82.8 / 89.9 | 0.04 / 0.05 | {"completed": 5} |
| latency | 100 | 5 | 33.1 | 100% | 124.6 / 133.0 / 137.9 | 0.03 / 0.04 | {"completed": 5} |
| latency | 200 | 5 | 33.9 | 100% | 216.8 / 234.5 / 236.5 | 0.04 / 0.04 | {"completed": 5} |
| latency | 400 | 5 | 31.5 | 100% | 418.4 / 433.1 / 438.1 | 0.03 / 0.05 | {"completed": 5} |
| latency | 800 | 5 | 32.8 | 100% | 811.2 / 832.2 / 837.6 | 0.04 / 0.04 | {"completed": 5} |
| latency | 1200 | 5 | 32.5 | 100% | 1213.8 / 1233.5 / 1239.5 | 0.02 / 0.03 | {"completed": 5} |
| latency | 1600 | 5 | 31.8 | 100% | 1609.6 / 1633.3 / 1642.0 | 0.02 / 0.04 | {"completed": 5} |
| latency | 2000 | 5 | 30.9 | 100% | 2008.5 / 2034.3 / 2038.5 | 0.02 / 0.03 | {"completed": 5} |
| timeout | 300 | 5 | 39.2 | 4% | 13.0 / 28.6 / 29.1 | 0.03 / 0.05 | {"aborted": 5} |
| timeout | 800 | 5 | 32.1 | 4% | 11.8 / 28.2 / 28.5 | 0.02 / 0.04 | {"aborted": 5} |
| timeout | 1500 | 5 | 33.4 | 4% | 7.3 / 35.7 / 35.7 | 0.02 / 0.05 | {"aborted": 5} |
| reset_peer | 50 | 5 | 33.7 | 9% | 13.0 / 32.4 / 37.0 | 0.03 / 0.04 | {"aborted": 5} |
| reset_peer | 200 | 4 | 31.2 | 18% | 77.0 / 178.1 / 184.5 | 0.04 / 0.04 | {"aborted": 4} |
| reset_peer | 800 | 4 | 31.3 | 35% | 391.2 / 754.6 / 779.3 | 0.03 / 0.04 | {"aborted": 4} |
| limit_data | 16 | 4 | 31.1 | 12% | 16.8 / 35.9 / 36.5 | 0.04 / 0.04 | {"aborted": 4} |
| limit_data | 64 | 4 | 31.8 | 6% | 15.1 / 24.5 / 24.7 | 0.01 / 0.03 | {"aborted": 4} |
| limit_data | 256 | 4 | 33.2 | 51% | 16.4 / 36.8 / 40.5 | 0.03 / 0.29 | {"completed": 3, "aborted": 1} |
| limit_data | 1024 | 4 | 32.7 | 80% | 14.5 / 34.7 / 44.8 | 0.04 / 0.05 | {"completed": 4} |
| bandwidth | 1 | 4 | 32.1 | 100% | 254.7 / 277.3 / 313.9 | 0.03 / 0.04 | {"completed": 4} |
| bandwidth | 10 | 4 | 31.8 | 100% | 34.8 / 55.2 / 62.0 | 0.02 / 0.37 | {"completed": 4} |
| bandwidth | 100 | 4 | 33.9 | 100% | 18.0 / 35.5 / 39.4 | 0.04 / 0.05 | {"completed": 4} |
| connection-disabled | 1 | 4 | 31.4 | 13% | 8.1 / 30.1 / 34.7 | 0.04 / 0.04 | {"aborted": 4} |
