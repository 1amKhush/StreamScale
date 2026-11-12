# Held-out ML Predictive comparison

Only correctness-passing live runs are included. Target misses remain visible.

| Workload | Mode | Repeat | Exact p95 (s) | Replica-seconds | Target met |
| --- | --- | --- | --- | --- | --- |
| flash-sale | ml-predictive | 0 | 0.896 | 1659.3 | True |
| steady | lag-keda | 2 | 0.791 | 188.0 | True |
| flash-sale | lag-keda | 0 | 28.872 | 816.1 | False |
| steady | fixed | 1 | 0.824 | 188.0 | True |
| overload-recovery | fixed | 1 | 327.467 | 479.1 | False |
| steady | cpu-hpa | 2 | 0.789 | 187.0 | True |
| flash-sale | ml-predictive | 2 | 0.833 | 1695.4 | True |
| flash-sale | lag-keda | 1 | 31.672 | 896.1 | False |
| steady | lag-keda | 1 | 0.825 | 188.0 | True |
| overload-recovery | ml-predictive | 0 | 25.915 | 1684.3 | False |
| overload-recovery | lag-keda | 0 | 57.074 | 1187.3 | False |
| flash-sale | cpu-hpa | 1 | 89.386 | 781.1 | False |
| steady | cpu-hpa | 0 | 0.721 | 187.0 | True |
| overload-recovery | lag-keda | 1 | 50.694 | 1174.2 | False |
| steady | fixed | 0 | 0.721 | 187.0 | True |
| flash-sale | fixed | 1 | 312.812 | 502.1 | False |
| steady | ml-predictive | 0 | 1.098 | 1520.2 | True |
| flash-sale | cpu-hpa | 0 | 93.091 | 741.1 | False |
| overload-recovery | cpu-hpa | 1 | 114.092 | 773.7 | False |
| overload-recovery | cpu-hpa | 2 | 112.523 | 701.8 | False |
| overload-recovery | ml-predictive | 1 | 20.320 | 1614.9 | False |
| overload-recovery | fixed | 2 | 330.991 | 490.1 | False |
| overload-recovery | fixed | 0 | 350.368 | 509.1 | False |
| steady | ml-predictive | 2 | 1.105 | 1526.2 | True |
| flash-sale | fixed | 0 | 276.368 | 465.1 | False |
| flash-sale | ml-predictive | 1 | 0.825 | 1642.6 | True |
| overload-recovery | ml-predictive | 2 | 23.038 | 1665.8 | False |
| steady | cpu-hpa | 1 | 0.825 | 188.0 | True |
| overload-recovery | cpu-hpa | 0 | 111.881 | 763.1 | False |
| steady | lag-keda | 0 | 0.720 | 189.0 | True |
| steady | ml-predictive | 1 | 1.011 | 1484.3 | True |
| steady | fixed | 2 | 0.788 | 188.0 | True |
| flash-sale | cpu-hpa | 2 | 93.329 | 929.2 | False |
| flash-sale | lag-keda | 2 | 52.314 | 899.2 | False |
| flash-sale | fixed | 2 | 353.675 | 548.1 | False |
| overload-recovery | lag-keda | 2 | 52.614 | 1192.2 | False |
