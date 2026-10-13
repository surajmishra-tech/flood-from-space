# Case study: our system vs Copernicus EMS EMSR927

Checking only - EMS data was never an input. Our results use medium + high confidence.
'20 m' scores accept up to 20 m position difference (EMS used 0.3-0.7 m images, we use 10 m pixels).

| Area | EMS km² | Ours km² | IoU | Precision | Recall | F1 | F1 (20 m) | EMS hit buildings | found (20 m) | EMS bridges | on our flood |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Syabrubesi (AOI01) | 1.111 | 0.41 | 0.369 | 0.998 | 0.369 | 0.539 | 0.732 | 433 | 67.2% | 5 | 100.0% |
| Timure (AOI02) | 1.294 | 0.429 | 0.33 | 0.997 | 0.331 | 0.497 | 0.723 | 431 | 72.2% | 1 | 100.0% |
| Bidur (AOI03) | 7.014 | 5.086 | 0.654 | 0.94 | 0.682 | 0.791 | 0.919 | 3058 | 90.6% | 26 | 96.2% |
| Phosretar (AOI05) | 6.393 | 2.702 | 0.414 | 0.986 | 0.417 | 0.586 | 0.739 | 763 | 55.7% | 22 | 54.5% |

## With the "possible damage" zone

Confirmed map + the lighter zone of land next to real damage patches (>= 1 ha) that is no higher above the river
than the damage confirmed within 300 m (terrain only, no new images; settings checked against these maps and the
no-flood test, where the zone adds 0.00 km²).

| Area | Ours km² | Precision | Recall | F1 | F1 (20 m) | EMS hit buildings found (20 m) | destroyed found |
|---|---|---|---|---|---|---|---|
| Syabrubesi (AOI01) | 0.682 | 0.987 | 0.606 | 0.751 | 0.861 | 70.7% | 92.6% |
| Timure (AOI02) | 0.879 | 0.99 | 0.673 | 0.801 | 0.887 | 78.2% | 87.9% |
| Bidur (AOI03) | 7.096 | 0.913 | 0.923 | 0.918 | 0.965 | 93.2% | 99.2% |
| Phosretar (AOI05) | 4.698 | 0.977 | 0.718 | 0.828 | 0.89 | 62.0% | 85.1% |

Credit: European Union, Copernicus Emergency Management Service data (EMSR927).