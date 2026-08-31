# E-CORM v0.2 reproducibility and immutability audit

Audit date: 2026-08-31  
Repository: `Daijunfan/Turing2`  
Reproduced source commit: `825f049da99c9af2dbc145d3e7ddc69b9b2ea437`

## Repository evidence

- Git repository root: `/Users/djf/develop/CS/Turing/Turing2`.
- Audited branch: `main`, tracking `origin/main`.
- Remote push URL: `git@github.com:Daijunfan/Turing2.git`.
- The complete E-CORM v0.2 source, legacy baseline, 16 tests, documentation,
  compact results, physical traces, and independent replay auditor are present
  under `corm_prototype/`.
- No credential, token, key, virtual environment, Python bytecode, or pytest
  cache is tracked.

## Fresh reproduction on the audited source

Commands executed from `corm_prototype/`:

```bash
/tmp/ecorm-v02-venv/bin/python -m pytest -q
/tmp/ecorm-v02-venv/bin/python run_experiments_v02.py
```

Observed results:

- pytest: 16 passed, 0 failed;
- `all_hard_gates_pass=true`;
- 14/14 v0.2 hard gates true;
- unexpected failures: 0;
- full experiment runtime: 40.73979754198808 seconds;
- `results_v02/all_failures.json` is empty;
- independent physical replay, semantic-bypass negative controls, 10×1024
  random DAGs, 4096-gate stress program, 100 asynchronous schedules per
  program, 10% Gate/Wire damage recovery, state migration, and complete
  Gate/Wire/State turnover all passed.

The authoritative fresh evidence is:

- `corm_prototype/results_v02/summary.json`;
- `corm_prototype/results_v02/recheck_summary.json`;
- `corm_prototype/results_v02/environment.json`.

## Immutability boundary

The audited v0.2 state will be frozen by annotated tag `v0.2.0-ecorm` after
this audit record and fresh result files are committed. L-CORM v0.3 work must
preserve the tag and the complete v0.2 implementation/results. No force push is
permitted.

## Public reproducibility gate

The repository was made public and verified without credentials using:

```bash
git ls-remote https://github.com/Daijunfan/Turing2.git
curl -L -o /dev/null -w '%{http_code}\n' \
  https://github.com/Daijunfan/Turing2
```

Observed evidence:

- `git ls-remote` returned
  `825f049da99c9af2dbc145d3e7ddc69b9b2ea437 refs/heads/main`;
- the public repository page returned HTTP `200`.

The public reproducibility gate therefore passes. The audit commit is eligible
for the immutable `v0.2.0-ecorm` tag.
