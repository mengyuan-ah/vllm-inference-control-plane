# Prior-result provenance

These are compact prior results, not new independent raw-evidence audits.

- `E9_PRIOR_AUDIT.md` is an exact copy of `/workspace/project/audits/e9/deliverables/AUDIT_REPORT.md`. Source SHA-256: `bb03b45ddcf0fde1759677e05c4eaac063ebfefb8809d656681511cdd21b289d`. This CPU audit was preserved on the network volume but absent from main at closeout base `61f1e7332ab635f5f6c1ebed4ad26025da57d00f`. Copying its report closes that publication gap; its historical verification claims were reviewed, not rerun here.
- `E3_TERMINAL_SUMMARY.json` is a field projection of `/workspace/project/results/phase2/e3/e3-replacement-minimal-attempt4/FINAL.json`. Source SHA-256: `e6a247264a4c6ad5ecd239d5df28b529cd43337f0836bbbc20699892deb53253`. The source explicitly reports `INCONCLUSIVE`, reason `no-unique-monotonic-boundary`, despite successful restoration and valid screening arms. This closeout read the terminal records, not an independent E3 raw-row audit. The value 5 req/s is a passing screening point, not a confirmed maximum. Earlier attempts 1–3 failed preflight/execution/restoration and are not combined with attempt 4.

No raw request records are copied into this directory. Original files remain unchanged.
